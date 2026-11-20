from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fintech_agent.domain.frost.aggregation import (
    InvalidSigningPackageError,
    SigningPackage,
)
from fintech_agent.domain.frost.signing import (
    NonceCommitments,
    NonceRandomness,
    SigningNonces,
    SigningParticipant,
    commit,
    compute_signature_share,
    fresh_nonce_randomness,
)
from fintech_agent.domain.user_operation import UserOperation

from .key_share_record import KeyShareRecord
from .ports import (
    AttestationEvidence,
    AttestationProvider,
    AuthorizationRequest,
    EnclaveRefusedError,
    SealedStorage,
    SigningAuthorizer,
)

KEY_SHARE_NAME = "key-share"
MESSAGE_LENGTH = 32
MAXIMUM_PENDING_SESSIONS = 32


@dataclass(frozen=True)
class EnclaveDependencies:
    attestation: AttestationProvider
    authorizer: SigningAuthorizer
    randomness_source: Callable[[], NonceRandomness] = fresh_nonce_randomness


class EnclaveNode:
    def __init__(
        self, record: KeyShareRecord, dependencies: EnclaveDependencies
    ) -> None:
        self._record = record
        self._dependencies = dependencies
        self._pending_nonces: dict[bytes, SigningNonces] = {}

    @classmethod
    def from_sealed_storage(
        cls, storage: SealedStorage, dependencies: EnclaveDependencies
    ) -> EnclaveNode:
        record = KeyShareRecord.deserialize(storage.unseal(KEY_SHARE_NAME))
        return cls(record, dependencies)

    @property
    def identifier(self) -> int:
        return self._record.identifier

    def commit(self, session_identifier: bytes) -> NonceCommitments:
        if session_identifier in self._pending_nonces:
            raise EnclaveRefusedError("this session already has committed nonces")
        nonces, commitments = commit(
            self._record.secret_share, self._dependencies.randomness_source()
        )
        self._remember(session_identifier, nonces)
        return commitments

    def sign(
        self,
        session_identifier: bytes,
        signing_package: SigningPackage,
        operation: UserOperation | None = None,
    ) -> int:
        nonces = self._pending_nonces.pop(session_identifier, None)
        if nonces is None:
            raise EnclaveRefusedError("no unused nonces exist for this session")
        self._require_acceptable(signing_package)
        self._dependencies.authorizer.authorize(
            AuthorizationRequest(signing_package.message, operation)
        )
        participant = SigningParticipant(
            self._record.identifier, self._record.secret_share, nonces
        )
        try:
            return compute_signature_share(signing_package, participant)
        except InvalidSigningPackageError as failure:
            raise EnclaveRefusedError(str(failure)) from failure

    def release(self, session_identifier: bytes) -> None:
        self._pending_nonces.pop(session_identifier, None)

    def attest(self, challenge: bytes) -> AttestationEvidence:
        return self._dependencies.attestation.produce(
            self._record.identifier, challenge
        )

    @property
    def pending_session_count(self) -> int:
        return len(self._pending_nonces)

    def _remember(self, session_identifier: bytes, nonces: SigningNonces) -> None:
        while len(self._pending_nonces) >= MAXIMUM_PENDING_SESSIONS:
            oldest = next(iter(self._pending_nonces))
            del self._pending_nonces[oldest]
        self._pending_nonces[session_identifier] = nonces

    def _require_acceptable(self, signing_package: SigningPackage) -> None:
        if signing_package.group_public_key != self._record.group_public_key:
            raise EnclaveRefusedError("the package names a different group public key")
        if len(signing_package.commitments) < self._record.threshold:
            raise EnclaveRefusedError(
                "the package has fewer signers than the threshold"
            )
        if len(signing_package.message) != MESSAGE_LENGTH:
            raise EnclaveRefusedError("the message must be a 32-byte hash")
