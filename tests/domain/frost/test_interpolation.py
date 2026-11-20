import pytest

from fintech_agent.domain.frost.group import GROUP_ORDER
from fintech_agent.domain.frost.interpolation import (
    InvalidParticipantListError,
    derive_interpolating_value,
)

from .vectors import FROST_SECP256K1_VECTOR


def test_interpolating_values_reconstruct_the_documented_group_secret() -> None:
    participants = [1, 3]
    shares = {
        1: int(FROST_SECP256K1_VECTOR["P1 participant_share"], 16),
        3: int(FROST_SECP256K1_VECTOR["P3 participant_share"], 16),
    }

    reconstructed = (
        sum(
            derive_interpolating_value(participants, identifier) * share
            for identifier, share in shares.items()
        )
        % GROUP_ORDER
    )

    assert reconstructed == int(FROST_SECP256K1_VECTOR["group_secret_key"], 16)


def test_single_participant_has_interpolating_value_one() -> None:
    assert derive_interpolating_value([5], 5) == 1


def test_identifier_missing_from_the_list_is_rejected() -> None:
    with pytest.raises(InvalidParticipantListError):
        derive_interpolating_value([1, 3], 2)


def test_duplicate_identifiers_are_rejected() -> None:
    with pytest.raises(InvalidParticipantListError):
        derive_interpolating_value([1, 1, 3], 1)
