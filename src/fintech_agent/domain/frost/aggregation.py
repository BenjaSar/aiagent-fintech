from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .group import (
    COMPRESSED_POINT_LENGTH,
    GROUP_ORDER,
    SCALAR_LENGTH,
    InvalidEncodingError,
    Point,
    base_multiply,
    deserialize_scalar,
    serialize_scalar,
)
from .hashing import (
    hash_commitments,
    hash_message,
    hash_to_binding_factor,
    hash_to_challenge,
)
from .interpolation import derive_interpolating_value

SIGNATURE_LENGTH = COMPRESSED_POINT_LENGTH + SCALAR_LENGTH


class InvalidSigningPackageError(ValueError):
    pass


class InvalidSignatureShareError(ValueError):
    pass


class InvalidAggregateSignatureError(ValueError):
    pass


@dataclass(frozen=True)
class ParticipantCommitment:
    identifier: int
    hiding_nonce_commitment: Point
    binding_nonce_commitment: Point


@dataclass(frozen=True)
class SigningPackage:
    commitments: tuple[ParticipantCommitment, ...]
    message: bytes
    group_public_key: Point

    def __post_init__(self) -> None:
        identifiers = self.identifiers
        if not identifiers:
            raise InvalidSigningPackageError("at least one commitment is required")
        if list(identifiers) != sorted(set(identifiers)):
            raise InvalidSigningPackageError(
                "commitments must have unique identifiers in ascending order"
            )
        if identifiers[0] < 1 or identifiers[-1] >= GROUP_ORDER:
            raise InvalidSigningPackageError("identifiers must be non-zero scalars")

    @property
    def identifiers(self) -> tuple[int, ...]:
        return tuple(commitment.identifier for commitment in self.commitments)


@dataclass(frozen=True)
class ParticipantShare:
    identifier: int
    verifying_share: Point
    signature_share: int


@dataclass(frozen=True)
class SchnorrSignature:
    group_commitment: Point
    response: int

    def serialize(self) -> bytes:
        return self.group_commitment.serialize() + serialize_scalar(self.response)

    @classmethod
    def deserialize(cls, encoded: bytes) -> SchnorrSignature:
        if len(encoded) != SIGNATURE_LENGTH:
            raise InvalidEncodingError("a signature must be 65 bytes")
        group_commitment = Point.deserialize(encoded[:COMPRESSED_POINT_LENGTH])
        return cls(
            group_commitment, deserialize_scalar(encoded[COMPRESSED_POINT_LENGTH:])
        )


def encode_commitments(signing_package: SigningPackage) -> bytes:
    return b"".join(
        serialize_scalar(commitment.identifier)
        + commitment.hiding_nonce_commitment.serialize()
        + commitment.binding_nonce_commitment.serialize()
        for commitment in signing_package.commitments
    )


def compute_binding_factors(signing_package: SigningPackage) -> dict[int, int]:
    prefix = (
        signing_package.group_public_key.serialize()
        + hash_message(signing_package.message)
        + hash_commitments(encode_commitments(signing_package))
    )
    return {
        identifier: hash_to_binding_factor(prefix + serialize_scalar(identifier))
        for identifier in signing_package.identifiers
    }


def compute_group_commitment(
    signing_package: SigningPackage, binding_factors: Mapping[int, int]
) -> Point:
    group_commitment = Point.identity()
    for commitment in signing_package.commitments:
        binding_factor = _binding_factor_for(binding_factors, commitment.identifier)
        group_commitment = group_commitment.add(
            _commitment_share(commitment, binding_factor)
        )
    return group_commitment


def compute_challenge(group_commitment: Point, signing_package: SigningPackage) -> int:
    return hash_to_challenge(
        group_commitment.serialize()
        + signing_package.group_public_key.serialize()
        + signing_package.message
    )


def aggregate(
    signing_package: SigningPackage, signature_shares: Mapping[int, int]
) -> SchnorrSignature:
    _validate_signature_shares(signing_package, signature_shares)
    binding_factors = compute_binding_factors(signing_package)
    group_commitment = compute_group_commitment(signing_package, binding_factors)
    response = sum(signature_shares.values()) % GROUP_ORDER
    signature = SchnorrSignature(group_commitment, response)
    if not verify_signature(signature, signing_package):
        raise InvalidAggregateSignatureError(
            "the aggregated signature does not verify against the group public key"
        )
    return signature


def verify_signature(
    signature: SchnorrSignature, signing_package: SigningPackage
) -> bool:
    if signature.group_commitment.is_identity:
        return False
    challenge = compute_challenge(signature.group_commitment, signing_package)
    expected = signature.group_commitment.add(
        signing_package.group_public_key.multiply(challenge)
    )
    return base_multiply(signature.response) == expected


def verify_signature_share(
    signing_package: SigningPackage, participant_share: ParticipantShare
) -> bool:
    binding_factors = compute_binding_factors(signing_package)
    binding_factor = _binding_factor_for(binding_factors, participant_share.identifier)
    group_commitment = compute_group_commitment(signing_package, binding_factors)
    challenge = compute_challenge(group_commitment, signing_package)
    interpolating_value = derive_interpolating_value(
        signing_package.identifiers, participant_share.identifier
    )
    commitment_share = _commitment_share(
        _commitment_for(signing_package, participant_share.identifier), binding_factor
    )
    expected = commitment_share.add(
        participant_share.verifying_share.multiply(challenge * interpolating_value)
    )
    return base_multiply(participant_share.signature_share) == expected


def _binding_factor_for(binding_factors: Mapping[int, int], identifier: int) -> int:
    if identifier not in binding_factors:
        raise InvalidSigningPackageError(
            "the participant is not in the signing package"
        )
    return binding_factors[identifier]


def _commitment_for(
    signing_package: SigningPackage, identifier: int
) -> ParticipantCommitment:
    for commitment in signing_package.commitments:
        if commitment.identifier == identifier:
            return commitment
    raise InvalidSigningPackageError("the participant is not in the signing package")


def _commitment_share(commitment: ParticipantCommitment, binding_factor: int) -> Point:
    return commitment.hiding_nonce_commitment.add(
        commitment.binding_nonce_commitment.multiply(binding_factor)
    )


def _validate_signature_shares(
    signing_package: SigningPackage, signature_shares: Mapping[int, int]
) -> None:
    if set(signature_shares) != set(signing_package.identifiers):
        raise InvalidSignatureShareError(
            "signature shares must come from exactly the committed participants"
        )
    if any(not 0 <= share < GROUP_ORDER for share in signature_shares.values()):
        raise InvalidSignatureShareError(
            "a signature share must be below the group order"
        )
