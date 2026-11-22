import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fintech_agent.application.enclave_tenancy import PRIMARY_TENANT
from fintech_agent.application.ports import SealedDataNotFoundError
from fintech_agent.application.session_rotation import RetirementFailedError
from fintech_agent.application.sign_user_operation import GroupConfiguration
from fintech_agent.composition.local_enclaves import (
    EnclaveSet,
    ProvisioningLayout,
    admit_tenant,
    evict_tenant,
    generate_key_with_enclaves,
    issue_session_policy,
    provision_ceremony_identities,
)
from fintech_agent.domain.session_policy import SessionPolicy

PID_FILE_NAME = "enclaves.pid"
READINESS_TIMEOUT_SECONDS = 30.0
READINESS_INTERVAL_SECONDS = 0.2
LOOPBACK = "127.0.0.1"
DIRECTORY_SUFFIX_BYTES = 4


class EnclaveFleetError(Exception):
    pass


class CeremonySet(Protocol):
    def generate(self) -> GroupConfiguration: ...

    def discard(self) -> None: ...

    def require_enclaves(self) -> EnclaveSet: ...


@dataclass(frozen=True)
class FleetConfiguration:
    base_directory: Path
    script: Path
    threshold: int = 2
    size: int = 3


class LocalEnclaveFleet:
    def __init__(self, configuration: FleetConfiguration) -> None:
        self._configuration = configuration

    def launch(self, tenant: str = PRIMARY_TENANT) -> EnclaveSet:
        configuration = self._configuration
        root = configuration.base_directory / (
            f"set-{secrets.token_hex(DIRECTORY_SUFFIX_BYTES)}"
        )
        ports = dict(enumerate(_free_ports(configuration.size), start=1))
        provision_ceremony_identities(
            ProvisioningLayout(root, configuration.threshold, ports, tenant)
        )
        processes = [self._start(root, identifier) for identifier in ports]
        (root / PID_FILE_NAME).write_text(
            "\n".join(str(process.pid) for process in processes)
        )
        for port in ports.values():
            _wait_until_listening(port)
        return EnclaveSet(root, tenant)

    def admit(self, enclaves: EnclaveSet) -> None:
        admit_tenant(enclaves)

    def stop(self, root: Path) -> None:
        pid_file = root / PID_FILE_NAME
        if not pid_file.exists():
            return
        for identifier in pid_file.read_text().split():
            subprocess.run(
                ["taskkill", "/PID", identifier, "/T", "/F"],
                capture_output=True,
                check=False,
            )
        pid_file.unlink()

    def _start(self, root: Path, identifier: int) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [
                sys.executable,
                str(self._configuration.script),
                "--root",
                str(root),
                "--identifier",
                str(identifier),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class LocalKeyCeremony:
    def __init__(self, fleet: LocalEnclaveFleet, tenant: str = PRIMARY_TENANT) -> None:
        self._fleet = fleet
        self._tenant = tenant
        self.enclaves: EnclaveSet | None = None

    def generate(self) -> GroupConfiguration:
        enclaves = self._fleet.launch(self._tenant)
        self.enclaves = enclaves
        try:
            return generate_key_with_enclaves(enclaves).configuration
        except Exception:
            self._fleet.stop(enclaves.root)
            raise

    def discard(self) -> None:
        if self.enclaves is not None:
            self._fleet.stop(self.enclaves.root)

    def require_enclaves(self) -> EnclaveSet:
        if self.enclaves is None:
            raise EnclaveFleetError("no key ceremony has run")
        return self.enclaves


class TenantKeyCeremony:
    def __init__(self, fleet: LocalEnclaveFleet, enclaves: EnclaveSet) -> None:
        self._fleet = fleet
        self._enclaves = enclaves
        self.admitted: EnclaveSet | None = None

    def generate(self) -> GroupConfiguration:
        self._fleet.admit(self._enclaves)
        self.admitted = self._enclaves
        try:
            return generate_key_with_enclaves(self._enclaves).configuration
        except Exception:
            self.discard()
            raise

    def discard(self) -> None:
        if self.admitted is not None:
            evict_tenant(self.admitted)
            self.admitted = None

    def require_enclaves(self) -> EnclaveSet:
        if self.admitted is None:
            raise EnclaveFleetError("no key ceremony has run")
        return self.admitted


class LocalPolicyIssuer:
    def __init__(self, ceremony: CeremonySet) -> None:
        self._ceremony = ceremony

    def issue(self, policy: SessionPolicy) -> None:
        issue_session_policy(self._ceremony.require_enclaves(), policy)


class LocalPreviousSession:
    def __init__(self, fleet: LocalEnclaveFleet, root: Path) -> None:
        self._fleet = fleet
        self._root = root

    def retire(self) -> None:
        pid_file = self._root / PID_FILE_NAME
        if not pid_file.exists():
            raise RetirementFailedError("the previous enclaves have no process list")
        self._fleet.stop(self._root)


class PreviousTenant:
    def __init__(self, enclaves: EnclaveSet) -> None:
        self._enclaves = enclaves

    def retire(self) -> None:
        try:
            evict_tenant(self._enclaves)
        except (SealedDataNotFoundError, OSError) as failure:
            raise RetirementFailedError(str(failure)) from failure


def _free_ports(count: int) -> list[int]:
    sockets = [socket.socket() for _ in range(count)]
    try:
        for held in sockets:
            held.bind((LOOPBACK, 0))
        return [held.getsockname()[1] for held in sockets]
    finally:
        for held in sockets:
            held.close()


def _wait_until_listening(port: int) -> None:
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            socket.create_connection((LOOPBACK, port), timeout=1).close()
            return
        except OSError:
            time.sleep(READINESS_INTERVAL_SECONDS)
    raise EnclaveFleetError(f"nothing is listening on port {port}")
