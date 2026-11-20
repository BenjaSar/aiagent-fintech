import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from fintech_agent.application.session_rotation import RetirementFailedError
from fintech_agent.application.sign_user_operation import GroupConfiguration
from fintech_agent.composition.local_enclaves import (
    ProvisioningLayout,
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


@dataclass(frozen=True)
class FleetConfiguration:
    base_directory: Path
    script: Path
    threshold: int = 2
    size: int = 3


class LocalEnclaveFleet:
    def __init__(self, configuration: FleetConfiguration) -> None:
        self._configuration = configuration

    def launch(self) -> Path:
        configuration = self._configuration
        root = configuration.base_directory / (
            f"rotation-{secrets.token_hex(DIRECTORY_SUFFIX_BYTES)}"
        )
        ports = dict(enumerate(_free_ports(configuration.size), start=1))
        provision_ceremony_identities(
            ProvisioningLayout(root, configuration.threshold, ports)
        )
        processes = [self._start(root, identifier) for identifier in ports]
        (root / PID_FILE_NAME).write_text(
            "\n".join(str(process.pid) for process in processes)
        )
        for port in ports.values():
            _wait_until_listening(port)
        return root

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
    def __init__(self, fleet: LocalEnclaveFleet) -> None:
        self._fleet = fleet
        self.root: Path | None = None

    def generate(self) -> GroupConfiguration:
        root = self._fleet.launch()
        self.root = root
        try:
            return generate_key_with_enclaves(root).configuration
        except Exception:
            self._fleet.stop(root)
            raise

    def discard(self) -> None:
        if self.root is not None:
            self._fleet.stop(self.root)

    def require_root(self) -> Path:
        if self.root is None:
            raise EnclaveFleetError("no key ceremony has run")
        return self.root


class LocalPolicyIssuer:
    def __init__(self, ceremony: LocalKeyCeremony) -> None:
        self._ceremony = ceremony

    def issue(self, policy: SessionPolicy) -> None:
        issue_session_policy(self._ceremony.require_root(), policy)


class LocalPreviousSession:
    def __init__(self, fleet: LocalEnclaveFleet, root: Path) -> None:
        self._fleet = fleet
        self._root = root

    def retire(self) -> None:
        pid_file = self._root / PID_FILE_NAME
        if not pid_file.exists():
            raise RetirementFailedError("the previous enclaves have no process list")
        self._fleet.stop(self._root)


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
