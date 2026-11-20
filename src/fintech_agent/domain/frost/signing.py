import secrets
from dataclasses import dataclass

from .aggregation import (
    InvalidSigningPackageError,
    ParticipantCommitment,
    SigningPackage,
    compute_binding_factors,
    compute_challenge,
    compute_group_commitment,
)
from .group import GROUP_ORDER, Point, base_multiply, serialize_scalar
from .hashing import hash_to_nonce
from .interpolation import derive_interpolating_value

RANDOMNESS_LENGTH = 32


@dataclass(frozen=True)
class NonceRandomness:
    hiding: bytes
    binding: bytes

    def __post_init__(self) -> None:
        if (
            len(self.hiding) != RANDOMNESS_LENGTH
            or len(self.binding) != RANDOMNESS_LENGTH
        ):
            raise ValueError("nonce randomness must be exactly 32 bytes for each nonce")


@dataclass(frozen=True)
class SigningNonces:
    hiding_nonce: int
    binding_nonce: int


@dataclass(frozen=True)
class NonceCommitments:
    hiding_nonce_commitment: Point
    binding_nonce_commitment: Point


@dataclass(frozen=True)
class SigningParticipant:
    identifier: int
    secret_share: int
    nonces: SigningNonces


def fresh_nonce_randomness() -> NonceRandomness:
    return NonceRandomness(
        hiding=secrets.token_bytes(RANDOMNESS_LENGTH),
        binding=secrets.token_bytes(RANDOMNESS_LENGTH),
    )


def generate_nonce(secret_share: int, randomness: bytes) -> int:
    return hash_to_nonce(randomness + serialize_scalar(secret_share))


def commit(
    secret_share: int, randomness: NonceRandomness
) -> tuple[SigningNonces, NonceCommitments]:
    nonces = SigningNonces(
        hiding_nonce=generate_nonce(secret_share, randomness.hiding),
        binding_nonce=generate_nonce(secret_share, randomness.binding),
    )
    return nonces, commitments_of(nonces)


def commitments_of(nonces: SigningNonces) -> NonceCommitments:
    return NonceCommitments(
        hiding_nonce_commitment=base_multiply(nonces.hiding_nonce),
        binding_nonce_commitment=base_multiply(nonces.binding_nonce),
    )


def compute_signature_share(
    signing_package: SigningPackage, participant: SigningParticipant
) -> int:
    _require_own_commitments(signing_package, participant)
    binding_factors = compute_binding_factors(signing_package)
    group_commitment = compute_group_commitment(signing_package, binding_factors)
    challenge = compute_challenge(group_commitment, signing_package)
    interpolating_value = derive_interpolating_value(
        signing_package.identifiers, participant.identifier
    )
    return (
        participant.nonces.hiding_nonce
        + participant.nonces.binding_nonce * binding_factors[participant.identifier]
        + interpolating_value * participant.secret_share * challenge
    ) % GROUP_ORDER


def _require_own_commitments(
    signing_package: SigningPackage, participant: SigningParticipant
) -> None:
    expected = commitments_of(participant.nonces)
    committed = _committed_by(signing_package, participant.identifier)
    if (
        committed.hiding_nonce_commitment != expected.hiding_nonce_commitment
        or committed.binding_nonce_commitment != expected.binding_nonce_commitment
    ):
        raise InvalidSigningPackageError(
            "the signing package does not contain this participant's commitments"
        )


def _committed_by(
    signing_package: SigningPackage, identifier: int
) -> ParticipantCommitment:
    for commitment in signing_package.commitments:
        if commitment.identifier == identifier:
            return commitment
    raise InvalidSigningPackageError("the participant is not in the signing package")
