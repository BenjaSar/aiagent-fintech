from dataclasses import replace

import pytest

from fintech_agent.adapters.in_memory import (
    InProcessEnclaveGateway,
    SimulatedAttestationVerifier,
)
from fintech_agent.application.enclave_node import EnclaveNode
from fintech_agent.application.ports import (
    AttestationEvidence,
    AttestationResult,
    AttestationStatus,
    CommitmentRequest,
    EnclaveGateway,
    ShareRequest,
)
from fintech_agent.application.sign_user_operation import (
    InvalidSigningRequestError,
    QuorumUnavailableError,
    SigningFailedError,
    SigningRequest,
    SigningSetup,
    SignUserOperation,
)
from fintech_agent.domain.frost.aggregation import SchnorrSignature
from fintech_agent.domain.frost.signing import NonceCommitments
from tests.application.fixtures import (
    USER_OPERATION_HASH,
    CorruptingGateway,
    FailsOnShareGateway,
    UnreachableGateway,
    build_node,
    build_setup,
    group_public_key,
    signature_verifies,
)


def verifies(signature: SchnorrSignature, message: bytes) -> bool:
    return signature_verifies(signature, message, group_public_key())


def nodes_and_gateways() -> tuple[dict[int, EnclaveNode], dict[int, EnclaveGateway]]:
    nodes = {identifier: build_node(identifier) for identifier in (1, 2, 3)}
    gateways: dict[int, EnclaveGateway] = {
        identifier: InProcessEnclaveGateway(node) for identifier, node in nodes.items()
    }
    return nodes, gateways


def sign(
    gateways: dict[int, EnclaveGateway], allow_simulated: bool = True
) -> SchnorrSignature:
    use_case = SignUserOperation(build_setup(gateways, allow_simulated))
    return use_case.execute(SigningRequest(USER_OPERATION_HASH))


def sign_with(setup: SigningSetup) -> SchnorrSignature:
    return SignUserOperation(setup).execute(SigningRequest(USER_OPERATION_HASH))


def test_a_quorum_of_enclaves_produces_a_valid_signature() -> None:
    _, gateways = nodes_and_gateways()

    signature = sign(gateways)

    assert verifies(signature, USER_OPERATION_HASH)
    assert not verifies(signature, b"\x01" * 32)


def test_each_signature_uses_fresh_nonces() -> None:
    _, gateways = nodes_and_gateways()

    first = sign(gateways)
    second = sign(gateways)

    assert first != second
    assert verifies(second, USER_OPERATION_HASH)


def test_only_the_threshold_of_enclaves_is_contacted() -> None:
    nodes, gateways = nodes_and_gateways()

    sign(gateways)

    assert nodes[3].pending_session_count == 0


def test_one_unreachable_enclave_does_not_stop_signing() -> None:
    _, gateways = nodes_and_gateways()
    gateways[1] = UnreachableGateway()

    assert verifies(sign(gateways), USER_OPERATION_HASH)


def test_fewer_than_the_threshold_halts_signing_and_leaves_no_nonces() -> None:
    nodes, gateways = nodes_and_gateways()
    gateways[1] = UnreachableGateway()
    gateways[3] = UnreachableGateway()

    with pytest.raises(QuorumUnavailableError):
        sign(gateways)

    assert nodes[2].pending_session_count == 0


def test_no_enclave_reachable_halts_signing() -> None:
    gateways: dict[int, EnclaveGateway] = {
        identifier: UnreachableGateway() for identifier in (1, 2, 3)
    }

    with pytest.raises(QuorumUnavailableError):
        sign(gateways)


def test_an_invalid_signature_share_names_the_enclave_and_aborts() -> None:
    nodes, gateways = nodes_and_gateways()
    gateways[2] = CorruptingGateway(gateways[2])

    with pytest.raises(SigningFailedError) as failure:
        sign(gateways)

    assert failure.value.culprit == 2
    assert nodes[1].pending_session_count == 0


def test_an_enclave_failing_in_round_two_aborts_and_releases_the_others() -> None:
    nodes, gateways = nodes_and_gateways()
    gateways[2] = FailsOnShareGateway(gateways[2])

    with pytest.raises(SigningFailedError) as failure:
        sign(gateways)

    assert failure.value.culprit == 2
    assert nodes[1].pending_session_count == 0
    assert nodes[2].pending_session_count == 0


def test_simulated_attestation_is_refused_unless_explicitly_allowed() -> None:
    _, gateways = nodes_and_gateways()

    with pytest.raises(QuorumUnavailableError):
        sign(gateways, allow_simulated=False)


class RejectingVerifier:
    def verify(self, evidence: AttestationEvidence) -> AttestationResult:
        return AttestationResult(AttestationStatus.REJECTED, "measurement mismatch")


class VerifiedForEnclaveThreeOnly:
    def verify(self, evidence: AttestationEvidence) -> AttestationResult:
        status = (
            AttestationStatus.VERIFIED
            if evidence.identifier == 3
            else AttestationStatus.REJECTED
        )
        return AttestationResult(status, "test verifier")


def test_enclaves_with_rejected_attestation_are_excluded() -> None:
    _, gateways = nodes_and_gateways()
    setup = replace(build_setup(gateways), verifier=RejectingVerifier())

    with pytest.raises(QuorumUnavailableError):
        sign_with(setup)


def test_a_single_attested_enclave_is_not_a_quorum() -> None:
    _, gateways = nodes_and_gateways()
    setup = replace(build_setup(gateways), verifier=VerifiedForEnclaveThreeOnly())

    with pytest.raises(QuorumUnavailableError):
        sign_with(setup)


class ImpersonatingGateway:
    def __init__(self, inner: EnclaveGateway) -> None:
        self._inner = inner

    def request_commitments(self, request: CommitmentRequest) -> NonceCommitments:
        return self._inner.request_commitments(request)

    def request_signature_share(self, request: ShareRequest) -> int:
        return self._inner.request_signature_share(request)

    def release_session(self, session_identifier: bytes) -> None:
        self._inner.release_session(session_identifier)

    def attest(self, challenge: bytes) -> AttestationEvidence:
        genuine = self._inner.attest(challenge)
        return AttestationEvidence(genuine.identifier + 1, challenge, genuine.document)


class StaleGateway(ImpersonatingGateway):
    def attest(self, challenge: bytes) -> AttestationEvidence:
        genuine = self._inner.attest(challenge)
        return AttestationEvidence(genuine.identifier, b"old", genuine.document)


def test_evidence_for_another_identity_or_challenge_is_not_accepted() -> None:
    _, gateways = nodes_and_gateways()
    gateways[1] = ImpersonatingGateway(gateways[1])
    gateways[2] = StaleGateway(gateways[2])

    with pytest.raises(QuorumUnavailableError):
        sign(gateways)


def test_a_message_that_is_not_a_hash_is_not_a_signing_request() -> None:
    with pytest.raises(InvalidSigningRequestError):
        SigningRequest(b"transfer all funds")


def test_the_simulated_verifier_rejects_unknown_documents() -> None:
    evidence = AttestationEvidence(1, b"challenge", b"forged")

    result = SimulatedAttestationVerifier().verify(evidence)

    assert result.status is AttestationStatus.REJECTED
