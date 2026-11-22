import pytest

from fintech_agent.domain.fleet_identity import (
    TREASURY_IDENTIFIER,
    FleetIdentityError,
    account_salt,
    require_agent_identifier,
)

SALT_CHARACTERS = 66


def test_a_salt_is_a_full_bytes32_word() -> None:
    salt = account_salt("agent-one")
    assert len(salt) == SALT_CHARACTERS
    assert salt.startswith("0x")
    assert int(salt, 16) >= 0


def test_a_salt_is_deterministic() -> None:
    assert account_salt("agent-one") == account_salt("agent-one")


def test_different_identifiers_get_different_salts() -> None:
    salts = {
        account_salt(identifier)
        for identifier in ("agent-one", "agent-two", "agentone", TREASURY_IDENTIFIER)
    }
    assert len(salts) == 4


def test_the_treasury_has_its_own_salt_and_cannot_be_an_agent() -> None:
    assert account_salt(TREASURY_IDENTIFIER) != account_salt("treasury-one")
    with pytest.raises(FleetIdentityError, match="reserved"):
        require_agent_identifier(TREASURY_IDENTIFIER)


def test_a_well_formed_agent_identifier_is_returned() -> None:
    assert require_agent_identifier("agent-one") == "agent-one"
    assert require_agent_identifier("payments2") == "payments2"


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "a",
        "Agent-One",
        "1agent",
        "-agent",
        "agent_one",
        "agent one",
        "agent.one",
        "a" * 33,
        "agent\u200bone",
    ],
)
def test_a_malformed_identifier_is_refused(identifier: str) -> None:
    with pytest.raises(FleetIdentityError):
        require_agent_identifier(identifier)
    with pytest.raises(FleetIdentityError):
        account_salt(identifier)


def test_the_longest_allowed_identifier_is_accepted() -> None:
    assert require_agent_identifier("a" * 32) == "a" * 32
