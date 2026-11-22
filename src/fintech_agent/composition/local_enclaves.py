import json
import secrets
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from fintech_agent.adapters.dpapi import DpapiFileSealedStorage
from fintech_agent.adapters.http_enclave import (
    LOOPBACK_ADDRESS,
    HttpEnclaveGateway,
    HttpEndpoint,
    HttpKeyGenerationGateway,
    HttpPolicyGateway,
    HttpTenantGateway,
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
from fintech_agent.application.enclave_tenancy import (
    PRIMARY_TENANT,
    TenancyParts,
    TenantedEnclave,
    require_tenant_name,
)
from fintech_agent.application.halt_switch import (
    GuardParts,
    HaltGuard,
    HaltOrder,
    HaltScope,
    HaltScopes,
    HaltSwitch,
)
from fintech_agent.application.key_generation_ports import KeyGenerationGateway
from fintech_agent.application.key_share_record import KeyShareRecord
from fintech_agent.application.policy_authorizer import (
    AuthorizerParts,
    PolicyAuthorizer,
)
from fintech_agent.application.ports import (
    EnclaveGateway,
    EnclaveRefusedError,
    EnclaveUnavailableError,
    SealedDataCorruptError,
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
class EnclaveSet:
    root: Path
    tenant: str = PRIMARY_TENANT


@dataclass(frozen=True)
class TenantSlot:
    root: Path
    identifier: int
    tenant: str = PRIMARY_TENANT


@dataclass(frozen=True)
class ProvisioningLayout:
    root: Path
    threshold: int
    ports: dict[int, int]
    tenant: str = PRIMARY_TENANT


@dataclass(frozen=True)
class PublicGroup:
    configuration: GroupConfiguration
    ports: dict[int, int]


def node_storage(root: Path, identifier: int) -> DpapiFileSealedStorage:
    return DpapiFileSealedStorage(root / f"node-{identifier}")


def tenant_storage(slot: TenantSlot) -> DpapiFileSealedStorage:
    return DpapiFileSealedStorage(
        slot.root / f"node-{slot.identifier}" / require_tenant_name(slot.tenant)
    )


def coordinator_storage(enclaves: EnclaveSet) -> DpapiFileSealedStorage:
    return DpapiFileSealedStorage(
        enclaves.root / COORDINATOR_DIRECTORY / require_tenant_name(enclaves.tenant)
    )


class LocalTenantDirectory:
    def __init__(self, root: Path, identifier: int) -> None:
        self._root = root
        self._identifier = identifier

    def token_of(self, tenant: str) -> bytes:
        slot = TenantSlot(self._root, self._identifier, tenant)
        try:
            return tenant_storage(slot).unseal(TOKEN_NAME)
        except (SealedDataNotFoundError, SealedDataCorruptError, ValueError):
            return b""


def provision_development_enclaves(layout: ProvisioningLayout) -> PublicGroup:
    _require_empty(layout.root)
    polynomial = random_polynomial(random_non_zero_scalar(), layout.threshold)
    dealt = trusted_dealer_keygen(polynomial, len(layout.ports))
    _provision_tokens(layout)
    for share in dealt.shares:
        record = KeyShareRecord(
            share.identifier, layout.threshold, share.value, dealt.group_public_key
        )
        tenant_storage(TenantSlot(layout.root, share.identifier, layout.tenant)).seal(
            KEY_SHARE_NAME, record.serialize()
        )
    _, verifying_shares = derive_group_info(len(layout.ports), dealt.vss_commitment)
    configuration = GroupConfiguration(
        dealt.group_public_key, layout.threshold, verifying_shares
    )
    _write_group_file(EnclaveSet(layout.root, layout.tenant), configuration)
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


def admit_tenant(enclaves: EnclaveSet) -> None:
    layout = _load_layout(enclaves.root)
    tenant = require_tenant_name(enclaves.tenant)
    if LocalTenantDirectory(enclaves.root, next(iter(layout.ports))).token_of(tenant):
        raise AlreadyProvisionedError(f"{tenant} is already admitted to this set")
    token = secrets.token_hex(TOKEN_BYTES).encode()
    for identifier in layout.ports:
        tenant_storage(TenantSlot(enclaves.root, identifier, tenant)).seal(
            TOKEN_NAME, token
        )
    coordinator_storage(enclaves).seal(TOKEN_NAME, token)


def evict_tenant(enclaves: EnclaveSet) -> None:
    layout = _load_layout(enclaves.root)
    tenant = require_tenant_name(enclaves.tenant)
    for endpoint in endpoints_of(enclaves).values():
        _ask_to_forget(endpoint)
    for identifier in layout.ports:
        shutil.rmtree(enclaves.root / f"node-{identifier}" / tenant, True)
    shutil.rmtree(enclaves.root / COORDINATOR_DIRECTORY / tenant, True)
    shutil.rmtree(enclaves.root / tenant, True)


def tenant_is_reachable(enclaves: EnclaveSet) -> bool:
    try:
        endpoints = endpoints_of(enclaves)
    except (SealedDataNotFoundError, SealedDataCorruptError):
        return False
    first = endpoints[next(iter(sorted(endpoints)))]
    try:
        HttpEnclaveGateway(first).attest(enclaves.tenant.encode())
    except (EnclaveUnavailableError, EnclaveRefusedError):
        return False
    return True


def halted_nodes(root: Path) -> tuple[int, ...]:
    return tuple(
        identifier
        for identifier in _load_layout(root).ports
        if _switch_of(TenantSlot(root, identifier)).in_force() is not None
    )


def halt_fleet(root: Path, order: HaltOrder) -> None:
    ordered = replace(order, scope=HaltScope.FLEET)
    for identifier in _load_layout(root).ports:
        _switch_of(TenantSlot(root, identifier)).halt(ordered)


def resume_fleet(root: Path, order: HaltOrder) -> None:
    ordered = replace(order, scope=HaltScope.FLEET)
    for identifier in _load_layout(root).ports:
        _switch_of(TenantSlot(root, identifier)).resume(ordered)


def halt_agent(enclaves: EnclaveSet, order: HaltOrder) -> None:
    ordered = replace(order, scope=HaltScope.AGENT)
    for slot in _slots_of(enclaves):
        _switch_of(slot).halt(ordered)


def resume_agent(enclaves: EnclaveSet, order: HaltOrder) -> None:
    ordered = replace(order, scope=HaltScope.AGENT)
    for slot in _slots_of(enclaves):
        _switch_of(slot).resume(ordered)


def endpoints_of(enclaves: EnclaveSet) -> dict[int, HttpEndpoint]:
    token = _token_of(enclaves)
    return {
        identifier: HttpEndpoint(_url_of(port), token, enclaves.tenant)
        for identifier, port in _load_layout(enclaves.root).ports.items()
    }


def generate_key_with_enclaves(enclaves: EnclaveSet) -> PublicGroup:
    layout = _load_layout(enclaves.root)
    gateways: dict[int, KeyGenerationGateway] = {
        identifier: HttpKeyGenerationGateway(endpoint)
        for identifier, endpoint in endpoints_of(enclaves).items()
    }
    configuration = RunKeyGeneration(
        KeyGenerationSetup(layout.threshold, gateways)
    ).execute()
    _write_group_file(enclaves, configuration)
    return PublicGroup(configuration, layout.ports)


def load_public_group(enclaves: EnclaveSet) -> PublicGroup:
    layout = _load_layout(enclaves.root)
    document = json.loads(_group_path(enclaves).read_text())
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


def issue_session_policy(enclaves: EnclaveSet, policy: SessionPolicy) -> None:
    for endpoint in endpoints_of(enclaves).values():
        HttpPolicyGateway(endpoint).issue_policy(policy)


def build_served_enclave(root: Path, identifier: int) -> ServedEnclave:
    def build(tenant: str) -> EnclaveService:
        return _tenant_service(TenantSlot(root, identifier, tenant))

    return ServedEnclave(
        TenantedEnclave(TenancyParts(LocalTenantDirectory(root, identifier), build))
    )


def serve_enclave_forever(root: Path, identifier: int) -> None:
    port = _load_layout(root).ports[identifier]
    server = build_server(build_served_enclave(root, identifier), port)
    print(f"enclave {identifier} listening on {LOOPBACK_ADDRESS}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def build_signing_setup(enclaves: EnclaveSet) -> SigningSetup:
    group = load_public_group(enclaves)
    endpoints = endpoints_of(enclaves)
    gateways: dict[int, EnclaveGateway] = {
        identifier: HttpEnclaveGateway(endpoints[identifier])
        for identifier in group.ports
    }
    return SigningSetup(
        group=group.configuration,
        gateways=gateways,
        verifier=SimulatedAttestationVerifier(),
        allow_simulated_attestation=True,
    )


def _tenant_service(slot: TenantSlot) -> EnclaveService:
    storage = tenant_storage(slot)
    authorizer = PolicyAuthorizer(AuthorizerParts(storage, wall_clock_seconds))
    guard = HaltGuard(GuardParts(_switch_of(slot), authorizer))
    dependencies = EnclaveDependencies(SimulatedAttestationProvider(), guard)
    return EnclaveService(
        ServiceParts(
            slot.identifier,
            dependencies,
            _load_signer(storage, dependencies),
            _load_ceremony(slot),
            authorizer,
        )
    )


def _switch_of(slot: TenantSlot) -> HaltSwitch:
    return HaltSwitch(
        HaltScopes(node_storage(slot.root, slot.identifier), tenant_storage(slot))
    )


def _slots_of(enclaves: EnclaveSet) -> tuple[TenantSlot, ...]:
    return tuple(
        TenantSlot(enclaves.root, identifier, enclaves.tenant)
        for identifier in _load_layout(enclaves.root).ports
    )


def _token_of(enclaves: EnclaveSet) -> bytes:
    return coordinator_storage(enclaves).unseal(TOKEN_NAME)


def _ask_to_forget(endpoint: HttpEndpoint) -> None:
    with suppress(EnclaveUnavailableError):
        HttpTenantGateway(endpoint).forget()


def _url_of(port: int) -> str:
    return f"http://{LOOPBACK_ADDRESS}:{port}"


def _require_empty(root: Path) -> None:
    if (root / LAYOUT_FILE_NAME).exists():
        raise AlreadyProvisionedError(
            "enclaves are already provisioned here; remove the folder to start over"
        )


def _provision_tokens(layout: ProvisioningLayout) -> None:
    token = secrets.token_hex(TOKEN_BYTES).encode()
    tenant = require_tenant_name(layout.tenant)
    for identifier in layout.ports:
        tenant_storage(TenantSlot(layout.root, identifier, tenant)).seal(
            TOKEN_NAME, token
        )
    coordinator_storage(EnclaveSet(layout.root, tenant)).seal(TOKEN_NAME, token)
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


def _load_ceremony(slot: TenantSlot) -> DkgEnclave | None:
    process = node_storage(slot.root, slot.identifier)
    try:
        identity = process.unseal(IDENTITY_NAME)
        roster_document = json.loads(process.unseal(ROSTER_NAME))
    except SealedDataNotFoundError:
        return None
    roster = {
        int(member): bytes.fromhex(encoded)
        for member, encoded in roster_document.items()
    }
    channel = X25519ChaChaChannel(KeyPair(identity[:32], identity[32:]), roster)
    return DkgEnclave(
        DkgEnclaveSetup(slot.identifier, len(roster), channel, tenant_storage(slot))
    )


def _group_path(enclaves: EnclaveSet) -> Path:
    return enclaves.root / require_tenant_name(enclaves.tenant) / GROUP_FILE_NAME


def _write_group_file(enclaves: EnclaveSet, configuration: GroupConfiguration) -> None:
    path = _group_path(enclaves)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
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
