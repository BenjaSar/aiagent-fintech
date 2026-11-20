import pytest

from fintech_agent.domain.frost.aggregation import (
    InvalidAggregateSignatureError,
    InvalidSignatureShareError,
    InvalidSigningPackageError,
    ParticipantCommitment,
    ParticipantShare,
    SchnorrSignature,
    SigningPackage,
    aggregate,
    compute_binding_factors,
    compute_challenge,
    compute_group_commitment,
    verify_signature,
    verify_signature_share,
)
from fintech_agent.domain.frost.group import (
    GROUP_ORDER,
    Point,
    base_multiply,
    serialize_scalar,
)

from .vectors import FROST_SECP256K1_VECTOR

VECTOR = FROST_SECP256K1_VECTOR


def scalar_of(key: str) -> int:
    return int(VECTOR[key], 16)


def point_of(key: str) -> Point:
    return Point.deserialize(bytes.fromhex(VECTOR[key]))


def commitment_of(prefix: str, identifier: int) -> ParticipantCommitment:
    return ParticipantCommitment(
        identifier,
        point_of(f"{prefix} hiding_nonce_commitment"),
        point_of(f"{prefix} binding_nonce_commitment"),
    )


def documented_signing_package() -> SigningPackage:
    return SigningPackage(
        commitments=(commitment_of("P1", 1), commitment_of("P3", 3)),
        message=bytes.fromhex(VECTOR["message"]),
        group_public_key=point_of("group_public_key"),
    )


def documented_signature_shares() -> dict[int, int]:
    return {1: scalar_of("P1 sig_share"), 3: scalar_of("P3 sig_share")}


def documented_signature_bytes() -> bytes:
    return bytes.fromhex(VECTOR["sig"])


def test_binding_factors_match_the_documented_vector() -> None:
    factors = compute_binding_factors(documented_signing_package())

    assert factors == {
        1: scalar_of("P1 binding_factor"),
        3: scalar_of("P3 binding_factor"),
    }


def test_group_commitment_matches_the_documented_signature_commitment() -> None:
    package = documented_signing_package()

    group_commitment = compute_group_commitment(
        package, compute_binding_factors(package)
    )

    assert group_commitment.serialize() == documented_signature_bytes()[:33]


def test_aggregation_reproduces_the_documented_signature() -> None:
    signature = aggregate(documented_signing_package(), documented_signature_shares())

    assert signature.serialize() == documented_signature_bytes()


def test_aggregated_signature_verifies_against_the_group_public_key() -> None:
    package = documented_signing_package()

    assert verify_signature(aggregate(package, documented_signature_shares()), package)


def test_signature_serialization_round_trips() -> None:
    signature = SchnorrSignature.deserialize(documented_signature_bytes())

    assert signature.serialize() == documented_signature_bytes()


def test_challenge_is_a_scalar() -> None:
    package = documented_signing_package()
    group_commitment = compute_group_commitment(
        package, compute_binding_factors(package)
    )

    assert 0 <= compute_challenge(group_commitment, package) < GROUP_ORDER


@pytest.mark.parametrize(("prefix", "identifier"), [("P1", 1), ("P3", 3)])
def test_each_documented_signature_share_verifies(prefix: str, identifier: int) -> None:
    verifying_share = base_multiply(scalar_of(f"{prefix} participant_share"))
    participant_share = ParticipantShare(
        identifier, verifying_share, scalar_of(f"{prefix} sig_share")
    )

    assert verify_signature_share(documented_signing_package(), participant_share)


def test_tampered_signature_share_is_detected_by_share_verification() -> None:
    verifying_share = base_multiply(scalar_of("P1 participant_share"))
    tampered = (scalar_of("P1 sig_share") + 1) % GROUP_ORDER

    participant_share = ParticipantShare(1, verifying_share, tampered)

    assert not verify_signature_share(documented_signing_package(), participant_share)


def test_share_verification_rejects_an_unknown_participant() -> None:
    participant_share = ParticipantShare(2, base_multiply(1), 1)

    with pytest.raises(InvalidSigningPackageError):
        verify_signature_share(documented_signing_package(), participant_share)


def test_tampered_signature_share_fails_aggregation() -> None:
    shares = documented_signature_shares()
    shares[1] = (shares[1] + 1) % GROUP_ORDER

    with pytest.raises(InvalidAggregateSignatureError):
        aggregate(documented_signing_package(), shares)


def test_single_share_below_the_threshold_is_rejected() -> None:
    shares = {1: scalar_of("P1 sig_share")}

    with pytest.raises(InvalidSignatureShareError):
        aggregate(documented_signing_package(), shares)


def test_single_share_alone_is_not_a_valid_signature() -> None:
    package = documented_signing_package()
    group_commitment = compute_group_commitment(
        package, compute_binding_factors(package)
    )
    partial = SchnorrSignature(group_commitment, scalar_of("P1 sig_share"))

    assert not verify_signature(partial, package)


def test_share_from_a_participant_without_a_commitment_is_rejected() -> None:
    shares = documented_signature_shares()
    shares[2] = 1

    with pytest.raises(InvalidSignatureShareError):
        aggregate(documented_signing_package(), shares)


def test_share_at_or_above_the_group_order_is_rejected() -> None:
    shares = documented_signature_shares()
    shares[3] = GROUP_ORDER

    with pytest.raises(InvalidSignatureShareError):
        aggregate(documented_signing_package(), shares)


def test_signature_does_not_verify_for_a_different_message() -> None:
    package = documented_signing_package()
    signature = aggregate(package, documented_signature_shares())
    other_package = SigningPackage(
        package.commitments, b"other message", package.group_public_key
    )

    assert not verify_signature(signature, other_package)


def test_commitments_must_be_in_ascending_identifier_order() -> None:
    with pytest.raises(InvalidSigningPackageError):
        SigningPackage(
            commitments=(commitment_of("P3", 3), commitment_of("P1", 1)),
            message=b"",
            group_public_key=point_of("group_public_key"),
        )


def test_duplicate_commitment_identifiers_are_rejected() -> None:
    with pytest.raises(InvalidSigningPackageError):
        SigningPackage(
            commitments=(commitment_of("P1", 1), commitment_of("P1", 1)),
            message=b"",
            group_public_key=point_of("group_public_key"),
        )


def test_identifier_zero_is_rejected() -> None:
    with pytest.raises(InvalidSigningPackageError):
        SigningPackage(
            commitments=(commitment_of("P1", 0),),
            message=b"",
            group_public_key=point_of("group_public_key"),
        )


def test_empty_commitment_list_is_rejected() -> None:
    with pytest.raises(InvalidSigningPackageError):
        SigningPackage(
            commitments=(), message=b"", group_public_key=point_of("group_public_key")
        )


def test_identifier_encoding_is_thirty_two_bytes_big_endian() -> None:
    assert serialize_scalar(3) == b"\x00" * 31 + b"\x03"
