from fintech_agent.application.enclave_node import EnclaveNode
from fintech_agent.application.enclave_service import EnclaveService
from fintech_agent.application.key_generation_ports import (
    CeremonyRequest,
    ConfirmRequest,
    FinishRequest,
    PublicKeyGeneration,
    Round2Request,
    SealedRound2Package,
)
from fintech_agent.application.ports import (
    AttestationEvidence,
    AttestationResult,
    AttestationStatus,
    CommitmentRequest,
    SealedDataNotFoundError,
    ShareRequest,
)
from fintech_agent.domain.frost.dkg import Round1Package
from fintech_agent.domain.frost.signing import NonceCommitments

SIMULATED_DOCUMENT = b"SIMULATED-ATTESTATION"


class InMemorySealedStorage:
    def __init__(self) -> None:
        self._entries: dict[str, bytes] = {}

    def seal(self, name: str, plaintext: bytes) -> None:
        self._entries[name] = plaintext

    def unseal(self, name: str) -> bytes:
        if name not in self._entries:
            raise SealedDataNotFoundError(name)
        return self._entries[name]


class InProcessEnclaveGateway:
    def __init__(self, node: EnclaveNode | EnclaveService) -> None:
        self._node = node

    def request_commitments(self, request: CommitmentRequest) -> NonceCommitments:
        return self._node.commit(request.session_identifier)

    def request_signature_share(self, request: ShareRequest) -> int:
        return self._node.sign(
            request.session_identifier, request.signing_package, request.operation
        )

    def release_session(self, session_identifier: bytes) -> None:
        self._node.release(session_identifier)

    def attest(self, challenge: bytes) -> AttestationEvidence:
        return self._node.attest(challenge)


class InProcessKeyGenerationGateway:
    def __init__(self, service: EnclaveService) -> None:
        self._service = service

    def begin_ceremony(self, request: CeremonyRequest) -> Round1Package:
        return self._service.begin_ceremony(request)

    def produce_round2(self, request: Round2Request) -> dict[int, SealedRound2Package]:
        return self._service.produce_round2(request)

    def finish_ceremony(self, request: FinishRequest) -> PublicKeyGeneration:
        return self._service.finish_ceremony(request)

    def confirm_ceremony(self, request: ConfirmRequest) -> None:
        self._service.confirm_ceremony(request)

    def abandon_ceremony(self, ceremony_identifier: bytes) -> None:
        self._service.abandon_ceremony(ceremony_identifier)


class SimulatedAttestationProvider:
    def produce(self, identifier: int, challenge: bytes) -> AttestationEvidence:
        return AttestationEvidence(identifier, challenge, SIMULATED_DOCUMENT)


class SimulatedAttestationVerifier:
    def verify(self, evidence: AttestationEvidence) -> AttestationResult:
        if evidence.document != SIMULATED_DOCUMENT:
            return AttestationResult(
                AttestationStatus.REJECTED, "not a simulated attestation document"
            )
        return AttestationResult(
            AttestationStatus.SIMULATED, "development process, no hardware evidence"
        )
