from dataclasses import dataclass

from fintech_agent.domain.frost.aggregation import SigningPackage
from fintech_agent.domain.frost.dkg import Round1Package
from fintech_agent.domain.frost.signing import NonceCommitments
from fintech_agent.domain.session_policy import SessionPolicy
from fintech_agent.domain.user_operation import UserOperation

from .dkg_enclave import DkgEnclave
from .enclave_node import EnclaveDependencies, EnclaveNode
from .key_generation_ports import (
    CeremonyRequest,
    ConfirmRequest,
    FinishRequest,
    PublicKeyGeneration,
    Round2Request,
    SealedRound2Package,
)
from .ports import AttestationEvidence, EnclaveRefusedError, PolicyIssuer


@dataclass(frozen=True)
class ServiceParts:
    identifier: int
    dependencies: EnclaveDependencies
    signer: EnclaveNode | None = None
    ceremony: DkgEnclave | None = None
    policy_issuer: PolicyIssuer | None = None


class EnclaveService:
    def __init__(self, parts: ServiceParts) -> None:
        self._identifier = parts.identifier
        self._dependencies = parts.dependencies
        self._signer = parts.signer
        self._ceremony = parts.ceremony
        self._policy_issuer = parts.policy_issuer

    def commit(self, session_identifier: bytes) -> NonceCommitments:
        return self._signing().commit(session_identifier)

    def sign(
        self,
        session_identifier: bytes,
        signing_package: SigningPackage,
        operation: UserOperation | None = None,
    ) -> int:
        return self._signing().sign(session_identifier, signing_package, operation)

    def issue_policy(self, policy: SessionPolicy) -> None:
        if self._policy_issuer is None:
            raise EnclaveRefusedError("this enclave does not accept session policies")
        self._policy_issuer.issue(policy)

    def release(self, session_identifier: bytes) -> None:
        if self._signer is not None:
            self._signer.release(session_identifier)

    def attest(self, challenge: bytes) -> AttestationEvidence:
        return self._dependencies.attestation.produce(self._identifier, challenge)

    def begin_ceremony(self, request: CeremonyRequest) -> Round1Package:
        return self._ceremony_enclave().begin(request)

    def produce_round2(self, request: Round2Request) -> dict[int, SealedRound2Package]:
        return self._ceremony_enclave().produce_round2(request)

    def finish_ceremony(self, request: FinishRequest) -> PublicKeyGeneration:
        return self._ceremony_enclave().finish(request)

    def confirm_ceremony(self, request: ConfirmRequest) -> None:
        outcome = self._ceremony_enclave().confirm(request)
        self._signer = EnclaveNode(outcome.record, self._dependencies)

    def abandon_ceremony(self, ceremony_identifier: bytes) -> None:
        if self._ceremony is not None:
            self._ceremony.abandon(ceremony_identifier)

    def _signing(self) -> EnclaveNode:
        if self._signer is None:
            raise EnclaveRefusedError("this enclave holds no key share yet")
        return self._signer

    def _ceremony_enclave(self) -> DkgEnclave:
        if self._ceremony is None:
            raise EnclaveRefusedError("this enclave is not set up for key generation")
        return self._ceremony
