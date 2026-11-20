import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from fintech_agent.application.enclave_node import KEY_SHARE_NAME
from fintech_agent.application.key_share_record import KeyShareRecord
from fintech_agent.application.ports import (
    EnclaveRefusedError,
    EnclaveUnavailableError,
)
from fintech_agent.application.run_key_generation import KeyGenerationFailedError
from fintech_agent.application.sign_user_operation import (
    QuorumUnavailableError,
    SigningFailedError,
    SigningRequest,
    SignUserOperation,
)
from fintech_agent.composition.local_enclaves import (
    AlreadyProvisionedError,
    ProvisioningLayout,
    build_signing_setup,
    generate_key_with_enclaves,
    issue_session_policy,
    node_storage,
    provision_ceremony_identities,
    provision_development_enclaves,
)
from fintech_agent.domain.session_policy import SessionPolicy
from fintech_agent.domain.user_operation import UserOperation
from fintech_agent.domain.user_operation_hash import user_operation_hash
from tests.application.fixtures import USER_OPERATION_HASH, signature_verifies
from tests.policy_fixtures import ETHER, TARGET, build_operation, build_policy, single

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the sealed storage adapter uses Windows DPAPI"
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = REPOSITORY_ROOT / "scripts" / "run_enclave_node.py"
READINESS_TIMEOUT_SECONDS = 20.0
IDENTIFIERS = (1, 2, 3)


@dataclass(frozen=True)
class RunningEnclaves:
    root: Path
    processes: dict[int, subprocess.Popen[bytes]]

    def stop(self, identifier: int) -> None:
        self.processes[identifier].kill()
        self.processes[identifier].wait()


def free_ports(count: int) -> list[int]:
    sockets = [socket.socket() for _ in range(count)]
    try:
        for held in sockets:
            held.bind(("127.0.0.1", 0))
        return [held.getsockname()[1] for held in sockets]
    finally:
        for held in sockets:
            held.close()


def provision(root: Path) -> None:
    ports = dict(enumerate(free_ports(len(IDENTIFIERS)), start=1))
    provision_development_enclaves(ProvisioningLayout(root, 2, ports))


def start_process(root: Path, identifier: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            str(RUN_SCRIPT),
            "--root",
            str(root),
            "--identifier",
            str(identifier),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_until_listening(root: Path, identifier: int) -> None:
    gateway = build_signing_setup(root).gateways[identifier]
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            gateway.attest(b"readiness")
            return
        except EnclaveUnavailableError:
            time.sleep(0.2)
    raise TimeoutError(f"enclave {identifier} did not start")


@pytest.fixture
def running_enclaves(tmp_path: Path) -> Iterator[RunningEnclaves]:
    provision(tmp_path)
    running = RunningEnclaves(
        tmp_path,
        {identifier: start_process(tmp_path, identifier) for identifier in IDENTIFIERS},
    )
    try:
        for identifier in IDENTIFIERS:
            wait_until_listening(tmp_path, identifier)
        issue_session_policy(tmp_path, wall_clock_policy())
        yield running
    finally:
        for identifier in IDENTIFIERS:
            running.stop(identifier)


def wall_clock_policy() -> SessionPolicy:
    return replace(build_policy(), valid_after=0, valid_until=2**40)


def sign_operation(root: Path, operation: UserOperation) -> bool:
    message = user_operation_hash(operation, wall_clock_policy().domain)
    setup = build_signing_setup(root)
    signature = SignUserOperation(setup).execute(SigningRequest(message, operation))
    return signature_verifies(signature, message, setup.group.group_public_key)


def sign_and_verify(root: Path) -> bool:
    return sign_operation(root, build_operation(single(TARGET)))


def test_three_separate_processes_produce_a_valid_threshold_signature(
    running_enclaves: RunningEnclaves,
) -> None:
    assert sign_and_verify(running_enclaves.root)


def test_the_system_keeps_signing_with_one_process_stopped(
    running_enclaves: RunningEnclaves,
) -> None:
    running_enclaves.stop(1)

    assert sign_and_verify(running_enclaves.root)


def test_the_system_halts_with_fewer_than_the_threshold_running(
    running_enclaves: RunningEnclaves,
) -> None:
    running_enclaves.stop(1)
    running_enclaves.stop(2)

    with pytest.raises(QuorumUnavailableError):
        sign_and_verify(running_enclaves.root)


def test_a_share_never_appears_in_plaintext_on_disk(tmp_path: Path) -> None:
    provision(tmp_path)
    plaintext = node_storage(tmp_path, 1).unseal(KEY_SHARE_NAME)
    secret_share = KeyShareRecord.deserialize(plaintext).secret_share

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret_share.to_bytes(32, "big") not in path.read_bytes()


def test_provisioning_never_overwrites_existing_keys(tmp_path: Path) -> None:
    provision(tmp_path)

    with pytest.raises(AlreadyProvisionedError):
        provision(tmp_path)


@pytest.fixture
def ceremony_enclaves(tmp_path: Path) -> Iterator[RunningEnclaves]:
    ports = dict(enumerate(free_ports(len(IDENTIFIERS)), start=1))
    provision_ceremony_identities(ProvisioningLayout(tmp_path, 2, ports))
    running = RunningEnclaves(
        tmp_path,
        {identifier: start_process(tmp_path, identifier) for identifier in IDENTIFIERS},
    )
    try:
        for identifier in IDENTIFIERS:
            wait_until_listening_for_ceremony(tmp_path, identifier)
        yield running
    finally:
        for identifier in IDENTIFIERS:
            running.stop(identifier)


def wait_until_listening_for_ceremony(root: Path, identifier: int) -> None:
    layout_ports = json.loads((root / "layout.json").read_text())["ports"]
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            socket.create_connection(
                ("127.0.0.1", layout_ports[str(identifier)]), timeout=0.5
            ).close()
            return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"enclave {identifier} did not start")


def test_processes_generate_a_key_together_and_then_sign_with_it(
    ceremony_enclaves: RunningEnclaves,
) -> None:
    group = generate_key_with_enclaves(ceremony_enclaves.root)
    issue_session_policy(ceremony_enclaves.root, wall_clock_policy())

    assert sign_and_verify(ceremony_enclaves.root)
    assert group.configuration.group_public_key == (
        build_signing_setup(ceremony_enclaves.root).group.group_public_key
    )


def test_a_restarted_process_reloads_its_generated_share_from_sealed_storage(
    ceremony_enclaves: RunningEnclaves,
) -> None:
    generate_key_with_enclaves(ceremony_enclaves.root)
    issue_session_policy(ceremony_enclaves.root, wall_clock_policy())
    for identifier in (1, 2):
        ceremony_enclaves.stop(identifier)
        ceremony_enclaves.processes[identifier] = start_process(
            ceremony_enclaves.root, identifier
        )
    for identifier in (1, 2):
        wait_until_listening(ceremony_enclaves.root, identifier)

    assert sign_and_verify(ceremony_enclaves.root)


def test_a_second_ceremony_is_refused_once_shares_exist(
    ceremony_enclaves: RunningEnclaves,
) -> None:
    generate_key_with_enclaves(ceremony_enclaves.root)

    with pytest.raises(KeyGenerationFailedError):
        generate_key_with_enclaves(ceremony_enclaves.root)


def test_processes_refuse_a_bare_hash_even_with_a_policy_installed(
    running_enclaves: RunningEnclaves,
) -> None:
    setup = build_signing_setup(running_enclaves.root)

    with pytest.raises(SigningFailedError):
        SignUserOperation(setup).execute(SigningRequest(USER_OPERATION_HASH))


def test_processes_accept_a_policy_once_only(
    running_enclaves: RunningEnclaves,
) -> None:
    with pytest.raises(EnclaveRefusedError):
        issue_session_policy(running_enclaves.root, wall_clock_policy())


def test_the_spend_ledger_survives_a_process_restart(
    running_enclaves: RunningEnclaves,
) -> None:
    assert sign_operation(
        running_enclaves.root, build_operation(single(TARGET, ETHER * 6 // 10))
    )
    for identifier in IDENTIFIERS:
        running_enclaves.stop(identifier)
        running_enclaves.processes[identifier] = start_process(
            running_enclaves.root, identifier
        )
    for identifier in IDENTIFIERS:
        wait_until_listening(running_enclaves.root, identifier)

    with pytest.raises(SigningFailedError):
        sign_operation(
            running_enclaves.root, build_operation(single(TARGET, ETHER * 5 // 10))
        )
    assert sign_operation(
        running_enclaves.root, build_operation(single(TARGET, ETHER * 4 // 10))
    )
