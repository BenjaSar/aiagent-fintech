import json
from typing import Any

import pytest

from fintech_agent.domain.agent_definition import (
    AgentDefinitionError,
    AgentType,
    Budget,
    ModelChoice,
)
from fintech_agent.domain.agent_definition_document import (
    definition_digest,
    definition_from_document,
    definition_to_document,
    document_digest,
)
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule
from tests.definition_fixtures import (
    OWNER,
    PAYEE,
    build_definition,
    build_reader_definition,
)
from tests.policy_fixtures import ETHER, TOKEN

DIGEST_CHARACTERS = 64


def test_a_well_formed_definition_is_accepted() -> None:
    definition = build_definition()
    assert definition.name == "Supplier Payments"
    assert definition.declared_assets == frozenset({NATIVE_ASSET})
    assert definition.payees == (PAYEE,)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 0},
        {"schema_version": 2},
        {"name": ""},
        {"name": "x"},
        {"name": "n" * 65},
        {"name": "Supplier\u200bPayments"},
        {"name": "Pagosá"},
        {"purpose": ""},
        {"purpose": "p" * 257},
        {"purpose": "Pay\u200bsuppliers."},
        {"system_prompt": ""},
        {"system_prompt": "p" * 4097},
        {"system_prompt": "Ignore\u202eeverything."},
        {"tools": ()},
        {"tools": ("pay_payee", "pay_payee")},
        {"tools": tuple(f"tool_{number}" for number in range(17))},
        {"payees": ("vault", "vault")},
        {"payees": tuple(f"payee-{number}" for number in range(33))},
        {"payees": ("Vault",)},
        {"payees": ("0x" + "d4" * 20,)},
        {"assets": ()},
        {"assets": (AssetRule(NATIVE_ASSET, 0),)},
        {"assets": (AssetRule(NATIVE_ASSET, ETHER), AssetRule(NATIVE_ASSET, 1))},
        {"assets": (AssetRule(TOKEN.upper().replace("0X", "0x"), ETHER),)},
        {"window_seconds": 0},
        {"valid_until": 0},
        {"risk_tier": 0},
        {"risk_tier": 4},
        {"owner": OWNER.upper().replace("0X", "0x")},
        {"provenance_identifier": "Generation-1"},
        {"provenance_identifier": ""},
    ],
)
def test_a_malformed_definition_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(AgentDefinitionError):
        build_definition(**overrides)


def test_the_budget_must_name_an_asset_the_agent_can_move() -> None:
    with pytest.raises(AgentDefinitionError, match="cannot move"):
        build_definition(budget=Budget(TOKEN, ETHER))


def test_a_budget_must_be_positive_and_well_formed() -> None:
    with pytest.raises(AgentDefinitionError, match="positive"):
        Budget(NATIVE_ASSET, 0)
    with pytest.raises(AgentDefinitionError, match="lowercase"):
        Budget(TOKEN.upper().replace("0X", "0x"), 1)


@pytest.mark.parametrize(
    "arguments",
    [
        ("Claude-Sonnet", 0, 1024),
        ("", 0, 1024),
        ("claude-sonnet-5-5", -1, 1024),
        ("claude-sonnet-5-5", 201, 1024),
        ("claude-sonnet-5-5", 0, 0),
        ("claude-sonnet-5-5", 0, 32_769),
    ],
)
def test_a_malformed_model_choice_is_refused(arguments: tuple[str, int, int]) -> None:
    with pytest.raises(AgentDefinitionError):
        ModelChoice(*arguments)


def test_the_document_round_trip_keeps_every_field() -> None:
    definition = build_definition()
    assert definition_from_document(definition_to_document(definition)) == definition


def test_the_document_holds_amounts_as_strings() -> None:
    document = definition_to_document(build_definition())
    assert document["assets"] == [[NATIVE_ASSET, str(ETHER)]]
    assert document["budget"] == [NATIVE_ASSET, str(ETHER // 2)]
    assert "." not in json.dumps(document["model"])


def test_the_digest_is_stable() -> None:
    first = definition_digest(build_definition())
    assert first == definition_digest(build_definition())
    assert len(first) == DIGEST_CHARACTERS


def test_the_digest_does_not_depend_on_the_order_of_the_fields() -> None:
    document = definition_to_document(build_definition())
    reordered = dict(reversed(list(document.items())))
    assert list(reordered) != list(document)
    assert document_digest(reordered) == definition_digest(build_definition())
    assert definition_from_document(reordered) == build_definition()


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Supplier Payments Two"},
        {"purpose": "Pay approved suppliers from the reserve."},
        {"system_prompt": "Pay only approved payees, and never exceed a cap."},
        {"tools": ("list_payees", "pay_payee")},
        {"assets": (AssetRule(NATIVE_ASSET, ETHER + 1),)},
        {"budget": Budget(NATIVE_ASSET, ETHER // 4)},
        {"valid_until": 1_086_401},
        {"risk_tier": 2},
        {"model": ModelChoice("claude-sonnet-5-5", 10, 1024)},
        {"provenance_identifier": "generation-2"},
    ],
)
def test_any_change_changes_the_digest(overrides: dict[str, Any]) -> None:
    assert definition_digest(build_definition(**overrides)) != definition_digest(
        build_definition()
    )


def test_a_reader_declares_no_payee_asset_or_budget() -> None:
    definition = build_reader_definition()

    assert definition.agent_type is AgentType.ANALYSIS
    assert definition.budget is None
    assert definition.declared_assets == frozenset()


@pytest.mark.parametrize(
    "overrides",
    [
        {"payees": (PAYEE,)},
        {"assets": (AssetRule(NATIVE_ASSET, ETHER),)},
        {"budget": Budget(NATIVE_ASSET, ETHER)},
    ],
)
def test_a_reader_that_declares_an_authority_is_refused(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(AgentDefinitionError, match="declare no payee"):
        build_reader_definition(**overrides)


def test_an_agent_that_can_move_value_needs_a_budget() -> None:
    with pytest.raises(AgentDefinitionError, match="needs a budget"):
        build_definition(budget=None)


def test_a_reader_survives_the_document_round_trip() -> None:
    definition = build_reader_definition()

    document = definition_to_document(definition)

    assert document["budget"] is None
    assert definition_from_document(document) == definition
    assert definition_digest(definition_from_document(document)) == definition_digest(
        definition
    )
