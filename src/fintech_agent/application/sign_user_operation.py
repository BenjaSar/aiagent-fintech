import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fintech_agent.domain.frost.aggregation import (
    ParticipantCommitment,
    ParticipantShare,
    SchnorrSignature,
    SigningPackage,
    aggregate,
    verify_signature_share,
)
from fintech_agent.domain.frost.group import Point
from fintech_agent.domain.user_operation import UserOperation

from .enclave_node import MESSAGE_LENGTH
from .ports import (
    AttestationStatus,
    AttestationVerifier,
    CommitmentRequest,
    EnclaveGateway,
    EnclaveRefusedError,
    EnclaveUnavailableError,
    ShareRequest,
)

SESSION_IDENTIFIER_LENGTH = 16
ENCLAVE_FAILURES = (EnclaveUnavailableError, EnclaveRefusedError)


class SigningFailedError(Exception):
    def __init__(self, message: str, culprit: int | None = None) -> None:
        super().__init__(message)
        self.culprit = culprit


class QuorumUnavailableError(SigningFailedError):
    pass


class InvalidSigningRequestError(ValueError):
    pass


def fresh_session_identifier() -> bytes:
    return secrets.token_bytes(SESSION_IDENTIFIER_LENGTH)


@dataclass(frozen=True)
class SigningRequest:
    message: bytes
    operation: UserOperation | None = None

    def __post_init__(self) -> None:
        if len(self.message) != MESSAGE_LENGTH:
            raise InvalidSigningRequestError("the message must be a 32-byte hash")


@dataclass(frozen=True)
class GroupConfiguration:
    group_public_key: Point
    threshold: int
    verifying_shares: Mapping[int, Point]


@dataclass(frozen=True)
class SigningSetup:
    group: GroupConfiguration
    gateways: Mapping[int, EnclaveGateway]
    verifier: AttestationVerifier
    allow_simulated_attestation: bool = False
    session_identifier_source: Callable[[], bytes] = fresh_session_identifier


class SignUserOperation:
    def __init__(self, setup: SigningSetup) -> None:
        self._setup = setup

    def execute(self, request: SigningRequest) -> SchnorrSignature:
        session = self._setup.session_identifier_source()
        commitments = self._collect_commitments(session)
        identifiers = [commitment.identifier for commitment in commitments]
        try:
            package = SigningPackage(
                tuple(commitments), request.message, self._setup.group.group_public_key
            )
            return self._sign(session, package, request.operation)
        except Exception:
            self._release(session, identifiers)
            raise

    def _sign(
        self, session: bytes, package: SigningPackage, operation: UserOperation | None
    ) -> SchnorrSignature:
        shares = {
            identifier: self._signature_share(session, (identifier, package, operation))
            for identifier in package.identifiers
        }
        return aggregate(package, shares)

    def _signature_share(
        self, session: bytes, member: tuple[int, SigningPackage, UserOperation | None]
    ) -> int:
        identifier, package, operation = member
        try:
            share = self._setup.gateways[identifier].request_signature_share(
                ShareRequest(session, package, operation)
            )
        except ENCLAVE_FAILURES as failure:
            raise SigningFailedError(
                f"enclave {identifier} did not return a signature share", identifier
            ) from failure
        trusted = ParticipantShare(
            identifier, self._setup.group.verifying_shares[identifier], share
        )
        if not verify_signature_share(package, trusted):
            raise SigningFailedError(
                f"enclave {identifier} returned an invalid signature share", identifier
            )
        return share

    def _collect_commitments(self, session: bytes) -> list[ParticipantCommitment]:
        collected: list[ParticipantCommitment] = []
        for identifier in sorted(self._setup.gateways):
            if len(collected) == self._setup.group.threshold:
                break
            commitment = self._commitment_from(session, identifier)
            if commitment is not None:
                collected.append(commitment)
        if len(collected) < self._setup.group.threshold:
            self._release(session, [commitment.identifier for commitment in collected])
            raise QuorumUnavailableError(
                f"{len(collected)} of {self._setup.group.threshold} required "
                "enclaves are available"
            )
        return collected

    def _commitment_from(
        self, session: bytes, identifier: int
    ) -> ParticipantCommitment | None:
        gateway = self._setup.gateways[identifier]
        try:
            if not self._is_admitted(session, identifier):
                return None
            committed = gateway.request_commitments(CommitmentRequest(session))
        except ENCLAVE_FAILURES:
            return None
        return ParticipantCommitment(
            identifier,
            committed.hiding_nonce_commitment,
            committed.binding_nonce_commitment,
        )

    def _is_admitted(self, session: bytes, identifier: int) -> bool:
        evidence = self._setup.gateways[identifier].attest(session)
        if evidence.identifier != identifier or evidence.challenge != session:
            return False
        status = self._setup.verifier.verify(evidence).status
        if status is AttestationStatus.VERIFIED:
            return True
        return (
            status is AttestationStatus.SIMULATED
            and self._setup.allow_simulated_attestation
        )

    def _release(self, session: bytes, identifiers: list[int]) -> None:
        for identifier in identifiers:
            try:
                self._setup.gateways[identifier].release_session(session)
            except ENCLAVE_FAILURES:
                continue
