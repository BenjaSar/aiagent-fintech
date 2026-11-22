from dataclasses import replace
from typing import Any

import pytest

from fintech_agent.domain.agent_definition import (
    AgentDefinition,
    AgentType,
    Budget,
)
from fintech_agent.domain.authority_order import (
    AuthorityChange,
    AuthorityOrderError,
    AuthorityWidenedError,
    is_renewal,
    require_renewal,
    widenings,
)
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from tests.definition_fixtures import (
    PAYEE,
    SESSION_SECONDS,
    build_definition,
    build_reader_definition,
)
from tests.policy_fixtures import ETHER, NOW, TOKEN

OTHER_OWNER = "0x" + "0b" * 20
PAYING_TOOLS = ("list_payees", "pay_payee")


def change(previous: AgentDefinition, **overrides: Any) -> AuthorityChange:
    return AuthorityChange(
        previous, replace(previous, **overrides), STAGE_ONE_PAYMENT_CATALOG
    )


def test_an_unchanged_authority_widens_nothing_and_is_a_renewal() -> None:
    definition = build_definition()
    unchanged = AuthorityChange(definition, definition, STAGE_ONE_PAYMENT_CATALOG)
    assert widenings(unchanged) == ()
    assert is_renewal(unchanged)


def test_a_later_window_of_the_same_length_is_a_renewal() -> None:
    definition = build_definition()
    renewal = change(
        definition,
        valid_after=NOW + SESSION_SECONDS,
        valid_until=NOW + 2 * SESSION_SECONDS,
    )
    assert widenings(renewal) == ()
    assert is_renewal(renewal)
    require_renewal(renewal)


def test_an_earlier_window_is_not_a_renewal() -> None:
    definition = build_definition()
    backwards = change(
        definition, valid_after=NOW - 1, valid_until=NOW - 1 + SESSION_SECONDS
    )
    assert widenings(backwards) == ()
    assert not is_renewal(backwards)
    with pytest.raises(AuthorityOrderError, match="same authority"):
        require_renewal(backwards)


def test_a_changed_prompt_is_not_a_widening_but_is_not_a_renewal() -> None:
    rewritten = change(build_definition(), system_prompt="Pay whoever asks nicely.")
    assert widenings(rewritten) == ()
    assert not is_renewal(rewritten)


def test_a_raised_cap_is_a_widening() -> None:
    raised = change(build_definition(), assets=(AssetRule(NATIVE_ASSET, 2 * ETHER),))
    assert widenings(raised) == (
        f"the cap for {NATIVE_ASSET} rises from {ETHER} to {2 * ETHER}",
    )
    assert not is_renewal(raised)


def test_a_lowered_cap_is_not_a_widening() -> None:
    lowered = change(build_definition(), assets=(AssetRule(NATIVE_ASSET, ETHER // 2),))
    assert widenings(lowered) == ()
    assert not is_renewal(lowered)


def test_an_added_payee_is_a_widening() -> None:
    definition = build_definition()
    widened = change(definition, payees=(*definition.payees, "supplier"))
    assert widenings(widened) == (
        "supplier is a payee the previous authority did not hold",
    )


def test_an_added_asset_is_a_widening() -> None:
    definition = build_definition()
    widened = change(
        definition, assets=(*definition.assets, AssetRule(TOKEN, 1_000_000))
    )
    assert widenings(widened) == (
        f"{TOKEN} is an asset the previous authority did not hold",
    )


def test_an_added_paying_tool_is_a_widening() -> None:
    crippled = build_definition(tools=("list_payees", "get_payment_status"))
    restored = AuthorityChange(crippled, build_definition(), STAGE_ONE_PAYMENT_CATALOG)
    assert widenings(restored) == (
        "pay_payee is a paying tool the previous authority did not hold",
    )


def test_an_added_reading_tool_is_not_a_widening() -> None:
    crippled = build_definition(tools=PAYING_TOOLS)
    restored = AuthorityChange(crippled, build_definition(), STAGE_ONE_PAYMENT_CATALOG)
    assert widenings(restored) == ()
    assert not is_renewal(restored)


def test_a_longer_session_is_a_widening_and_never_a_renewal() -> None:
    definition = build_definition()
    longer = change(definition, valid_until=definition.valid_until + 1)
    assert widenings(longer) == (
        f"the session runs {SESSION_SECONDS + 1} seconds instead of {SESSION_SECONDS}",
    )
    assert not is_renewal(longer)
    with pytest.raises(AuthorityWidenedError, match="the session runs"):
        require_renewal(longer)


def test_any_change_of_rolling_window_is_a_widening() -> None:
    shortened = change(build_definition(), window_seconds=3_600)
    assert widenings(shortened) == (
        "the rolling window changes from 86400 to 3600 seconds",
    )
    assert not is_renewal(shortened)


def test_a_raised_risk_tier_is_a_widening() -> None:
    raised = change(build_definition(), risk_tier=2)
    assert widenings(raised) == ("the risk tier rises from 1 to 2",)


def test_a_raised_budget_is_a_widening() -> None:
    definition = build_definition()
    raised = change(definition, budget=Budget(NATIVE_ASSET, ETHER))
    assert widenings(raised) == (f"the budget rises from {ETHER // 2} to {ETHER}",)


def test_a_budget_moved_to_another_asset_is_a_widening() -> None:
    definition = build_definition(
        assets=(AssetRule(NATIVE_ASSET, ETHER), AssetRule(TOKEN, 1_000))
    )
    moved = change(definition, budget=Budget(TOKEN, 500))
    assert widenings(moved) == (f"the budget moves from {NATIVE_ASSET} to {TOKEN}",)


def test_a_changed_owner_is_a_widening() -> None:
    moved = change(build_definition(), owner=OTHER_OWNER)
    assert widenings(moved) == (
        f"the owner changes from {build_definition().owner} to {OTHER_OWNER}",
    )


def test_several_widenings_are_all_reported() -> None:
    definition = build_definition()
    widened = change(
        definition,
        payees=(*definition.payees, "supplier"),
        assets=(AssetRule(NATIVE_ASSET, 2 * ETHER),),
        risk_tier=3,
    )
    assert len(widenings(widened)) == 3


def test_a_widened_renewal_is_refused_by_name() -> None:
    raised = change(build_definition(), assets=(AssetRule(NATIVE_ASSET, 2 * ETHER),))
    with pytest.raises(AuthorityWidenedError, match="the cap for"):
        require_renewal(raised)


def test_an_uncatalogued_tool_cannot_be_compared() -> None:
    definition = build_definition()
    unknown = change(definition, tools=("list_payees", "pay_payee", "wire_anywhere"))
    with pytest.raises(AuthorityOrderError, match="wire_anywhere"):
        widenings(unknown)


def test_turning_a_reader_into_a_payer_is_a_widening() -> None:
    reader = build_reader_definition()
    paying = replace(
        reader,
        agent_type=AgentType.PAYMENT,
        tools=PAYING_TOOLS,
        payees=(PAYEE,),
        assets=(AssetRule(NATIVE_ASSET, ETHER),),
        budget=Budget(NATIVE_ASSET, ETHER),
    )

    reasons = widenings(AuthorityChange(reader, paying, STAGE_ONE_PAYMENT_CATALOG))

    assert any("gains a budget" in reason for reason in reasons)
    assert any("pay_payee is a paying tool" in reason for reason in reasons)
    assert any(PAYEE in reason for reason in reasons)


def test_a_reader_compared_with_itself_widens_nothing() -> None:
    reader = build_reader_definition()

    change = AuthorityChange(reader, reader, STAGE_ONE_PAYMENT_CATALOG)

    assert widenings(change) == ()
    assert is_renewal(change)
