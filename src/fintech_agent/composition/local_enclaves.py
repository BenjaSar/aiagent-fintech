import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from fintech_agent.adapters.dpapi import DpapiFileSealedStorage
from fintech_agent.adapters.http_enclave import (
    LOOPBACK_ADDRESS,
    HttpEnclaveGateway,
    HttpEndpoint,
    HttpKeyGenerationGateway,
    HttpPolicyGateway,
    ServedEnclave,
    build_server,
)
from fintech_agent.adapters.in_memory import (
    SimulatedAttestationProvider,
    SimulatedAttestationVerifier,
)
from fintech_agent.adapters.x25519_channel import (
    KeyPair,
    X25519ChaChaChannel,
    generate_identity,
)
from fintech_agent.application.dkg_enclave import DkgEnclave, DkgEnclaveSetup
from fintech_agent.application.enclave_node import (
    KEY_SHARE_NAME,
    EnclaveDependencies,
    EnclaveNode,
)
from fintech_agent.application.enclave_service import EnclaveService, ServiceParts
from fintech_agent.application.key_generation_ports import KeyGenerationGateway
from fintech_agent.application.key_share_record import KeyShareRecord
from fintech_agent.application.policy_authorizer import (
    AuthorizerParts,
    PolicyAuthorizer,
)
from fintech_agent.application.ports import (
    EnclaveGateway,
    SealedDataNotFoundError,
    SealedStorage,
)
from fintech_agent.application.run_key_generation import (
    KeyGenerationSetup,
    RunKeyGeneration,
)
from fintech_agent.application.sign_user_operation import (
    GroupConfiguration,
    SigningSetup,
)
from fintech_agent.domain.frost.group import Point
from fintech_agent.domain.frost.keygen import (
    derive_group_info,
    random_non_zero_scalar,
    random_polynomial,
    trusted_dealer_keygen,
)
from fintech_agent.domain.session_policy import SessionPolicy

LAYOUT_FILE_NAME = "layout.json"
GROUP_FILE_NAME = "group.json"
TOKEN_NAME = "coordinator-token"
IDENTITY_NAME = "identity-key"
ROSTER_NAME = "roster"
COORDINATOR_DIRECTORY = "coordinator"
TOKEN_BYTES = 24


class AlreadyProvisionedError(Exception):
    pass


@dataclass(frozen=True)
class ProvisioningLayout:
    root: Path
    threshold: int
    ports: dict[int, int]


@dataclass(frozen=True)
class PublicGroup:
    configuration: GroupConfiguration
    ports: dict[int, int]


def node_storage(root: Path, identifier: int) -> DpapiFileSealedStorage:
    return DpapiFileSealedStorage(root / f"node-{identifier}")


def coordinator_storage(root: Path) -> DpapiFileSealedStorage:
    return DpapiFileSealedStorage(root / COORDINATOR_DIRECTORY)


def provision_development_enclaves(layout: ProvisioningLayout) -> PublicGroup:
    _require_empty(layout.root)
    polynomial = random_polynomial(random_non_zero_scalar(), layout.threshold)
    dealt = trusted_dealer_keygen(polynomial, len(layout.ports))
    _provision_tokens(layout)
    for share in dealt.shares:
        record = KeyShareRecord(
            share.identifier, layout.threshold, share.value, dealt.group_public_key
        )
        node_storage(layout.root, share.identifier).seal(
            KEY_SHARE_NAME, record.serialize()
        )
    _, verifying_shares = derive_group_info(len(layout.ports), dealt.vss_commitment)
    configuration = GroupConfiguration(
        dealt.group_public_key, layout.threshold, verifying_shares
    )
    _write_group_file(layout.root, configuration)
    return PublicGroup(configuration, dict(layout.ports))


def provision_ceremony_identities(layout: ProvisioningLayout) -> None:
    _require_empty(layout.root)
    _provision_tokens(layout)
    identities = {identifier: generate_identity() for identifier in layout.ports}
    roster = json.dumps(
        {
            str(identifier): pair.public_key.hex()
            for identifier, pair in identities.items()
        }
    ).encode()
    for identifier, pair in identities.items():
        storage = node_storage(layout.root, identifier)
        storage.seal(IDENTITY_NAME, pair.private_key + pair.public_key)
        storage.seal(ROSTER_NAME, roster)


def generate_key_with_enclaves(root: Path) -> PublicGroup:
    layout = _load_layout(root)
    token = coordinator_storage(root).unseal(TOKEN_NAME)
    gateways: dict[int, KeyGenerationGateway] = {
        identifier: HttpKeyGenerationGateway(HttpEndpoint(_url_of(port), token))
        for identifier, port in layout.ports.items()
    }
    configuration = RunKeyGeneration(
        KeyGenerationSetup(layout.threshold, gateways)
    ).execute()
    _write_group_file(root, configuration)
    return PublicGroup(configuration, layout.ports)


def load_public_group(root: Path) -> PublicGroup:
    layout = _load_layout(root)
    document = json.loads((root / GROUP_FILE_NAME).read_text())
    configuration = GroupConfiguration(
        Point.deserialize(bytes.fromhex(document["group_public_key"])),
        layout.threshold,
        {
            int(identifier): Point.deserialize(bytes.fromhex(encoded))
            for identifier, encoded in document["verifying_shares"].items()
        },
    )
    return PublicGroup(configuration, layout.ports)


def wall_clock_seconds() -> int:
    return int(time.time())


def issue_session_policy(root: Path, policy: SessionPolicy) -> None:
    layout = _load_layout(root)
    token = coordinator_storage(root).unseal(TOKEN_NAME)
    for port in layout.ports.values():
        HttpPolicyGateway(HttpEndpoint(_url_of(port), token)).issue_policy(policy)


def build_served_enclave(root: Path, identifier: int) -> ServedEnclave:
    storage = node_storage(root, identifier)
    authorizer = PolicyAuthorizer(AuthorizerParts(storage, wall_clock_seconds))
    dependencies = EnclaveDependencies(SimulatedAttestationProvider(), authorizer)
    service = EnclaveService(
        ServiceParts(
            identifier,
            dependencies,
            _load_signer(storage, dependencies),
            _load_ceremony(storage, identifier),
            authorizer,
        )
    )
    return ServedEnclave(service, storage.unseal(TOKEN_NAME))


def serve_enclave_forever(root: Path, identifier: int) -> None:
    port = _load_layout(root).ports[identifier]
    server = build_server(build_served_enclave(root, identifier), port)
    print(f"enclave {identifier} listening on {LOOPBACK_ADDRESS}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def build_signing_setup(root: Path) -> SigningSetup:
    group = load_public_group(root)
    token = coordinator_storage(root).unseal(TOKEN_NAME)
    gateways: dict[int, EnclaveGateway] = {
        identifier: HttpEnclaveGateway(HttpEndpoint(_url_of(port), token))
        for identifier, port in group.ports.items()
    }
    return SigningSetup(
        group=group.configuration,
        gateways=gateways,
        verifier=SimulatedAttestationVerifier(),
        allow_simulated_attestation=True,
    )


def _url_of(port: int) -> str:
    return f"http://{LOOPBACK_ADDRESS}:{port}"


def _require_empty(root: Path) -> None:
    if (root / LAYOUT_FILE_NAME).exists():
        raise AlreadyProvisionedError(
            "enclaves are already provisioned here; remove the folder to start over"
        )


def _provision_tokens(layout: ProvisioningLayout) -> None:
    token = secrets.token_hex(TOKEN_BYTES).encode()
    for identifier in layout.ports:
        node_storage(layout.root, identifier).seal(TOKEN_NAME, token)
    coordinator_storage(layout.root).seal(TOKEN_NAME, token)
    layout.root.mkdir(parents=True, exist_ok=True)
    (layout.root / LAYOUT_FILE_NAME).write_text(
        json.dumps(
            {
                "threshold": layout.threshold,
                "ports": {
                    str(identifier): port for identifier, port in layout.ports.items()
                },
            },
            indent=2,
        )
    )


def _load_layout(root: Path) -> ProvisioningLayout:
    document = json.loads((root / LAYOUT_FILE_NAME).read_text())
    ports = {
        int(identifier): int(port) for identifier, port in document["ports"].items()
    }
    return ProvisioningLayout(root, int(document["threshold"]), ports)


def _load_signer(
    storage: SealedStorage, dependencies: EnclaveDependencies
) -> EnclaveNode | None:
    try:
        return EnclaveNode.from_sealed_storage(storage, dependencies)
    except SealedDataNotFoundError:
        return None


def _load_ceremony(storage: SealedStorage, identifier: int) -> DkgEnclave | None:
    try:
        identity = storage.unseal(IDENTITY_NAME)
        roster_document = json.loads(storage.unseal(ROSTER_NAME))
    except SealedDataNotFoundError:
        return None
    roster = {
        int(member): bytes.fromhex(encoded)
        for member, encoded in roster_document.items()
    }
    channel = X25519ChaChaChannel(KeyPair(identity[:32], identity[32:]), roster)
    return DkgEnclave(DkgEnclaveSetup(identifier, len(roster), channel, storage))


def _write_group_file(root: Path, configuration: GroupConfiguration) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / GROUP_FILE_NAME).write_text(
        json.dumps(
            {
                "group_public_key": configuration.group_public_key.serialize().hex(),
                "verifying_shares": {
                    str(identifier): point.serialize().hex()
                    for identifier, point in configuration.verifying_shares.items()
                },
            },
            indent=2,
        )
    )
