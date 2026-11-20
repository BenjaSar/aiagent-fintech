from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from fintech_agent.domain.frost.aggregation import SigningPackage
from fintech_agent.domain.frost.signing import NonceCommitments
from fintech_agent.domain.session_policy import SessionPolicy
from fintech_agent.domain.user_operation import UserOperation


class EnclaveUnavailableError(Exception):
    pass


class EnclaveRefusedError(Exception):
    pass


class SealedDataNotFoundError(Exception):
    pass


class SealedDataCorruptError(Exception):
    pass


class SecureChannelError(Exception):
    pass


class AttestationStatus(Enum):
    VERIFIED = "verified"
    SIMULATED = "simulated"
    REJECTED = "rejected"


@dataclass(frozen=True)
class CommitmentRequest:
    session_identifier: bytes


@dataclass(frozen=True)
class ShareRequest:
    session_identifier: bytes
    signing_package: SigningPackage
    operation: UserOperation | None = None


@dataclass(frozen=True)
class AuthorizationRequest:
    message: bytes
    operation: UserOperation | None


@dataclass(frozen=True)
class AttestationEvidence:
    identifier: int
    challenge: bytes
    document: bytes


@dataclass(frozen=True)
class AttestationResult:
    status: AttestationStatus
    detail: str


@dataclass(frozen=True)
class ChannelMessage:
    sender: int
    receiver: int
    associated_data: bytes
    payload: bytes


@dataclass(frozen=True)
class CardSignatureCheck:
    public_key: bytes
    message: bytes
    signature: bytes


class SecureChannel(Protocol):
    def seal(self, message: ChannelMessage) -> bytes: ...

    def open(self, message: ChannelMessage) -> bytes: ...


class EnclaveGateway(Protocol):
    def request_commitments(self, request: CommitmentRequest) -> NonceCommitments: ...

    def request_signature_share(self, request: ShareRequest) -> int: ...

    def release_session(self, session_identifier: bytes) -> None: ...

    def attest(self, challenge: bytes) -> AttestationEvidence: ...


class SigningAuthorizer(Protocol):
    def authorize(self, request: AuthorizationRequest) -> None: ...


class PolicyIssuer(Protocol):
    def issue(self, policy: SessionPolicy) -> None: ...


class SealedStorage(Protocol):
    def seal(self, name: str, plaintext: bytes) -> None: ...

    def unseal(self, name: str) -> bytes: ...


class AttestationProvider(Protocol):
    def produce(self, identifier: int, challenge: bytes) -> AttestationEvidence: ...


class AttestationVerifier(Protocol):
    def verify(self, evidence: AttestationEvidence) -> AttestationResult: ...


class CardSignatureVerifier(Protocol):
    def verify(self, request: CardSignatureCheck) -> bool: ...
