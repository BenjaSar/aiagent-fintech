import hashlib
from dataclasses import dataclass

from fintech_agent.adapters.in_memory import (
    InMemorySealedStorage,
    InProcessEnclaveGateway,
    SimulatedAttestationProvider,
    SimulatedAttestationVerifier,
)
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
)
from fintech_agent.application.key_share_record import KeyShareRecord
from fintech_agent.application.ports import (
    AttestationEvidence,
    AuthorizationRequest,
    CommitmentRequest,
    EnclaveGateway,
    EnclaveUnavailableError,
    ShareRequest,
)
from fintech_agent.application.sign_user_operation import (
    GroupConfiguration,
    SigningSetup,
)
from fintech_agent.domain.frost.aggregation import (
    ParticipantCommitment,
    SchnorrSignature,
    SigningPackage,
    verify_signature,
)
from fintech_agent.domain.frost.group import Point, base_multiply
from fintech_agent.domain.frost.signing import NonceCommitments
from tests.domain.frost.vectors import FROST_SECP256K1_VECTOR as VECTOR

THRESHOLD = 2
PARTICIPANT_COUNT = 3
USER_OPERATION_HASH = hashlib.sha256(b"user operation").digest()


class AllowEverything:
    def authorize(self, request: AuthorizationRequest) -> None:
        return None


def permissive_dependencies() -> EnclaveDependencies:
    return EnclaveDependencies(SimulatedAttestationProvider(), AllowEverything())


def group_public_key() -> Point:
    return Point.deserialize(bytes.fromhex(VECTOR["group_public_key"]))


def signature_verifies(
    signature: SchnorrSignature, message: bytes, public_key: Point
) -> bool:
    placeholder = ParticipantCommitment(
        1, signature.group_commitment, signature.group_commitment
    )
    return verify_signature(
        signature, SigningPackage((placeholder,), message, public_key)
    )


def secret_share_of(identifier: int) -> int:
    return int(VECTOR[f"P{identifier} participant_share"], 16)


def record_of(identifier: int) -> KeyShareRecord:
    return KeyShareRecord(
        identifier, THRESHOLD, secret_share_of(identifier), group_public_key()
    )


def build_node(identifier: int) -> EnclaveNode:
    return EnclaveNode(record_of(identifier), permissive_dependencies())


class SingleTenant:
    def __init__(self, token: bytes, tenant: str = PRIMARY_TENANT) -> None:
        self._token = token
        self._tenant = tenant

    def token_of(self, tenant: str) -> bytes:
        return self._token if tenant == self._tenant else b""


def tenanted(service: EnclaveService, token: bytes) -> TenantedEnclave:
    return TenantedEnclave(TenancyParts(SingleTenant(token), lambda _: service))


def service_for(node: EnclaveNode) -> EnclaveService:
    return EnclaveService(
        ServiceParts(
            node.identifier,
            permissive_dependencies(),
            signer=node,
        )
    )


def build_node_from_storage(identifier: int) -> EnclaveNode:
    storage = InMemorySealedStorage()
    storage.seal(KEY_SHARE_NAME, record_of(identifier).serialize())
    return EnclaveNode.from_sealed_storage(storage, permissive_dependencies())


def group_configuration() -> GroupConfiguration:
    return GroupConfiguration(
        group_public_key(),
        THRESHOLD,
        {
            identifier: base_multiply(secret_share_of(identifier))
            for identifier in range(1, PARTICIPANT_COUNT + 1)
        },
    )


def build_setup(
    gateways: dict[int, EnclaveGateway], allow_simulated: bool = True
) -> SigningSetup:
    return SigningSetup(
        group=group_configuration(),
        gateways=gateways,
        verifier=SimulatedAttestationVerifier(),
        allow_simulated_attestation=allow_simulated,
    )


def in_process_gateways() -> dict[int, EnclaveGateway]:
    return {
        identifier: InProcessEnclaveGateway(build_node(identifier))
        for identifier in range(1, PARTICIPANT_COUNT + 1)
    }


@dataclass
class UnreachableGateway:
    def request_commitments(self, request: CommitmentRequest) -> NonceCommitments:
        raise EnclaveUnavailableError("connection refused")

    def request_signature_share(self, request: ShareRequest) -> int:
        raise EnclaveUnavailableError("connection refused")

    def release_session(self, session_identifier: bytes) -> None:
        raise EnclaveUnavailableError("connection refused")

    def attest(self, challenge: bytes) -> AttestationEvidence:
        raise EnclaveUnavailableError("connection refused")


@dataclass
class CorruptingGateway:
    inner: EnclaveGateway

    def request_commitments(self, request: CommitmentRequest) -> NonceCommitments:
        return self.inner.request_commitments(request)

    def request_signature_share(self, request: ShareRequest) -> int:
        return self.inner.request_signature_share(request) + 1

    def release_session(self, session_identifier: bytes) -> None:
        self.inner.release_session(session_identifier)

    def attest(self, challenge: bytes) -> AttestationEvidence:
        return self.inner.attest(challenge)


@dataclass
class FailsOnShareGateway:
    inner: EnclaveGateway

    def request_commitments(self, request: CommitmentRequest) -> NonceCommitments:
        return self.inner.request_commitments(request)

    def request_signature_share(self, request: ShareRequest) -> int:
        raise EnclaveUnavailableError("connection lost")

    def release_session(self, session_identifier: bytes) -> None:
        self.inner.release_session(session_identifier)

    def attest(self, challenge: bytes) -> AttestationEvidence:
        return self.inner.attest(challenge)
