import pytest

from fintech_agent.domain.frost.group import GROUP_ORDER, Point, base_multiply
from fintech_agent.domain.frost.keygen import (
    InvalidKeygenParametersError,
    SecretShare,
    SharingPolynomial,
    combine_shares,
    derive_group_info,
    random_polynomial,
    trusted_dealer_keygen,
    vss_commit,
    vss_verify,
)

from .vectors import FROST_SECP256K1_VECTOR

VECTOR = FROST_SECP256K1_VECTOR


def scalar_of(key: str) -> int:
    return int(VECTOR[key], 16)


def documented_polynomial() -> SharingPolynomial:
    return SharingPolynomial(
        (scalar_of("group_secret_key"), scalar_of("share_polynomial_coefficients[1]"))
    )


def documented_shares() -> dict[int, int]:
    return {
        identifier: scalar_of(f"P{identifier} participant_share")
        for identifier in (1, 2, 3)
    }


def test_dealer_reproduces_the_documented_shares_and_group_key() -> None:
    output = trusted_dealer_keygen(documented_polynomial(), 3)

    assert {
        share.identifier: share.value for share in output.shares
    } == documented_shares()
    assert output.group_public_key.serialize() == bytes.fromhex(
        VECTOR["group_public_key"]
    )
    assert output.vss_commitment[0] == output.group_public_key


def test_every_documented_share_verifies_against_the_commitment() -> None:
    commitment = vss_commit(documented_polynomial())

    for identifier, value in documented_shares().items():
        assert vss_verify(SecretShare(identifier, value), commitment)


def test_a_tampered_share_fails_verification() -> None:
    commitment = vss_commit(documented_polynomial())
    tampered = SecretShare(1, (documented_shares()[1] + 1) % GROUP_ORDER)

    assert not vss_verify(tampered, commitment)


def test_a_share_checked_under_the_wrong_identifier_fails_verification() -> None:
    commitment = vss_commit(documented_polynomial())

    assert not vss_verify(SecretShare(2, documented_shares()[1]), commitment)


def test_group_info_gives_each_participant_the_public_key_of_its_share() -> None:
    commitment = vss_commit(documented_polynomial())

    group_public_key, verifying_shares = derive_group_info(3, commitment)

    assert group_public_key.serialize() == bytes.fromhex(VECTOR["group_public_key"])
    assert verifying_shares == {
        identifier: base_multiply(value)
        for identifier, value in documented_shares().items()
    }


@pytest.mark.parametrize("identifiers", [(1, 2), (1, 3), (2, 3), (1, 2, 3)])
def test_any_threshold_subset_recovers_the_group_secret(
    identifiers: tuple[int, ...],
) -> None:
    shares = [
        SecretShare(identifier, documented_shares()[identifier])
        for identifier in identifiers
    ]

    assert combine_shares(shares, 2) == scalar_of("group_secret_key")


def test_fewer_shares_than_the_threshold_cannot_combine() -> None:
    with pytest.raises(InvalidKeygenParametersError):
        combine_shares([SecretShare(1, documented_shares()[1])], 2)


def test_random_polynomial_has_the_requested_secret_and_degree() -> None:
    polynomial = random_polynomial(scalar_of("group_secret_key"), 3)

    assert len(polynomial.coefficients) == 3
    assert polynomial.coefficients[0] == scalar_of("group_secret_key")
    assert len(set(polynomial.coefficients)) == 3


def test_random_dealing_produces_verifiable_shares_for_a_three_of_five_group() -> None:
    polynomial = random_polynomial(scalar_of("group_secret_key"), 3)

    output = trusted_dealer_keygen(polynomial, 5)

    assert len(output.shares) == 5
    assert all(vss_verify(share, output.vss_commitment) for share in output.shares)
    assert combine_shares(list(output.shares[1:4]), 3) == scalar_of("group_secret_key")


def test_threshold_below_two_is_rejected() -> None:
    with pytest.raises(InvalidKeygenParametersError):
        random_polynomial(7, 1)


@pytest.mark.parametrize("participants", [0, 2])
def test_fewer_participants_than_the_threshold_is_rejected(participants: int) -> None:
    with pytest.raises(InvalidKeygenParametersError):
        trusted_dealer_keygen(random_polynomial(7, 3), participants)


def test_secret_must_be_a_valid_non_zero_scalar() -> None:
    with pytest.raises(InvalidKeygenParametersError):
        random_polynomial(0, 2)
    with pytest.raises(InvalidKeygenParametersError):
        random_polynomial(GROUP_ORDER, 2)


def test_group_public_key_is_a_point() -> None:
    output = trusted_dealer_keygen(documented_polynomial(), 3)

    assert isinstance(output.group_public_key, Point)
