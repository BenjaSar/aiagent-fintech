import pytest

from fintech_agent.domain.frost.aggregation import (
    InvalidSigningPackageError,
    ParticipantCommitment,
    SigningPackage,
    aggregate,
)
from fintech_agent.domain.frost.group import Point
from fintech_agent.domain.frost.signing import (
    NonceRandomness,
    SigningNonces,
    SigningParticipant,
    commit,
    compute_signature_share,
    fresh_nonce_randomness,
    generate_nonce,
)

from .vectors import FROST_SECP256K1_VECTOR

VECTOR = FROST_SECP256K1_VECTOR


def scalar_of(key: str) -> int:
    return int(VECTOR[key], 16)


def point_of(key: str) -> Point:
    return Point.deserialize(bytes.fromhex(VECTOR[key]))


def randomness_of(prefix: str) -> NonceRandomness:
    return NonceRandomness(
        hiding=bytes.fromhex(VECTOR[f"{prefix} hiding_nonce_randomness"]),
        binding=bytes.fromhex(VECTOR[f"{prefix} binding_nonce_randomness"]),
    )


def documented_signing_package() -> SigningPackage:
    commitments = tuple(
        ParticipantCommitment(
            identifier,
            point_of(f"{prefix} hiding_nonce_commitment"),
            point_of(f"{prefix} binding_nonce_commitment"),
        )
        for prefix, identifier in (("P1", 1), ("P3", 3))
    )
    return SigningPackage(
        commitments=commitments,
        message=bytes.fromhex(VECTOR["message"]),
        group_public_key=point_of("group_public_key"),
    )


def documented_participant(prefix: str, identifier: int) -> SigningParticipant:
    return SigningParticipant(
        identifier=identifier,
        secret_share=scalar_of(f"{prefix} participant_share"),
        nonces=SigningNonces(
            hiding_nonce=scalar_of(f"{prefix} hiding_nonce"),
            binding_nonce=scalar_of(f"{prefix} binding_nonce"),
        ),
    )


@pytest.mark.parametrize("prefix", ["P1", "P3"])
def test_nonces_match_the_documented_vector(prefix: str) -> None:
    randomness = randomness_of(prefix)
    secret_share = scalar_of(f"{prefix} participant_share")

    assert generate_nonce(secret_share, randomness.hiding) == scalar_of(
        f"{prefix} hiding_nonce"
    )
    assert generate_nonce(secret_share, randomness.binding) == scalar_of(
        f"{prefix} binding_nonce"
    )


@pytest.mark.parametrize("prefix", ["P1", "P3"])
def test_commit_reproduces_the_documented_nonces_and_commitments(prefix: str) -> None:
    secret_share = scalar_of(f"{prefix} participant_share")

    nonces, commitments = commit(secret_share, randomness_of(prefix))

    assert nonces == SigningNonces(
        scalar_of(f"{prefix} hiding_nonce"), scalar_of(f"{prefix} binding_nonce")
    )
    assert commitments.hiding_nonce_commitment == point_of(
        f"{prefix} hiding_nonce_commitment"
    )
    assert commitments.binding_nonce_commitment == point_of(
        f"{prefix} binding_nonce_commitment"
    )


@pytest.mark.parametrize(("prefix", "identifier"), [("P1", 1), ("P3", 3)])
def test_signature_share_matches_the_documented_vector(
    prefix: str, identifier: int
) -> None:
    share = compute_signature_share(
        documented_signing_package(), documented_participant(prefix, identifier)
    )

    assert share == scalar_of(f"{prefix} sig_share")


def test_shares_produced_here_aggregate_to_the_documented_signature() -> None:
    package = documented_signing_package()
    shares = {
        1: compute_signature_share(package, documented_participant("P1", 1)),
        3: compute_signature_share(package, documented_participant("P3", 3)),
    }

    assert aggregate(package, shares).serialize() == bytes.fromhex(VECTOR["sig"])


def test_signer_absent_from_the_package_is_rejected() -> None:
    participant = documented_participant("P1", 2)

    with pytest.raises(InvalidSigningPackageError):
        compute_signature_share(documented_signing_package(), participant)


def test_signer_whose_commitments_are_not_in_the_package_is_rejected() -> None:
    other_nonces, _ = commit(scalar_of("P1 participant_share"), randomness_of("P3"))
    participant = SigningParticipant(
        identifier=1,
        secret_share=scalar_of("P1 participant_share"),
        nonces=other_nonces,
    )

    with pytest.raises(InvalidSigningPackageError):
        compute_signature_share(documented_signing_package(), participant)


def test_randomness_must_be_thirty_two_bytes_each() -> None:
    with pytest.raises(ValueError):
        NonceRandomness(hiding=b"\x01" * 31, binding=b"\x02" * 32)
    with pytest.raises(ValueError):
        NonceRandomness(hiding=b"\x01" * 32, binding=b"\x02" * 33)


def test_fresh_randomness_is_thirty_two_bytes_and_never_repeats() -> None:
    first = fresh_nonce_randomness()
    second = fresh_nonce_randomness()

    assert len(first.hiding) == len(first.binding) == 32
    assert first != second
    assert first.hiding != first.binding
