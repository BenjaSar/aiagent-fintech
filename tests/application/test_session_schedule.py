from dataclasses import replace

import pytest

from fintech_agent.application.session_schedule import (
    RENEWAL_LEAD_SECONDS,
    RenewalRequest,
    ScheduledSession,
    ScheduleError,
    ScheduleQuery,
    due_for_renewal,
    renewal_of,
)
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.authority_order import (
    AuthorityChange,
    AuthorityOrderError,
    is_renewal,
)
from fintech_agent.domain.fleet_identity import FleetIdentityError
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from tests.definition_fixtures import SESSION_SECONDS, build_definition
from tests.policy_fixtures import ETHER, NOW

FIRST = "agent-one"
SECOND = "agent-two"


def session(identifier: str, valid_until: int) -> ScheduledSession:
    definition = build_definition(
        valid_after=valid_until - SESSION_SECONDS, valid_until=valid_until
    )
    return ScheduledSession(identifier, definition)


def query(*sessions: ScheduledSession, **overrides: int) -> ScheduleQuery:
    return ScheduleQuery(sessions, overrides.get("now", NOW), **_lead(overrides))


def _lead(overrides: dict[str, int]) -> dict[str, int]:
    if "lead_seconds" in overrides:
        return {"lead_seconds": overrides["lead_seconds"]}
    return {}


def test_a_session_far_from_its_end_is_not_due() -> None:
    assert due_for_renewal(query(session(FIRST, NOW + SESSION_SECONDS))) == ()


def test_a_session_inside_the_lead_is_due() -> None:
    due = session(FIRST, NOW + RENEWAL_LEAD_SECONDS - 1)
    assert due_for_renewal(query(due)) == (FIRST,)


def test_a_session_exactly_at_the_lead_is_due() -> None:
    due = session(FIRST, NOW + RENEWAL_LEAD_SECONDS)
    assert due_for_renewal(query(due)) == (FIRST,)


def test_an_expired_session_is_due() -> None:
    assert due_for_renewal(query(session(FIRST, NOW - 1))) == (FIRST,)


def test_only_the_due_agents_are_named_and_in_order() -> None:
    schedule = query(
        session(SECOND, NOW + 10),
        session(FIRST, NOW + 20),
        session("agent-three", NOW + 10 * RENEWAL_LEAD_SECONDS),
    )
    assert due_for_renewal(schedule) == (FIRST, SECOND)


def test_a_shorter_lead_narrows_what_is_due() -> None:
    due = session(FIRST, NOW + 100)
    assert due_for_renewal(query(due, lead_seconds=99)) == ()
    assert due_for_renewal(query(due, lead_seconds=100)) == (FIRST,)


def test_a_schedule_holds_each_agent_once() -> None:
    with pytest.raises(ScheduleError, match="each agent once"):
        query(session(FIRST, NOW), session(FIRST, NOW))


def test_a_schedule_checks_its_identifiers() -> None:
    with pytest.raises(FleetIdentityError):
        query(session("Agent One", NOW))


def test_a_schedule_needs_a_time_and_a_positive_lead() -> None:
    with pytest.raises(ScheduleError, match="the time it is read at"):
        query(session(FIRST, NOW), now=0)
    with pytest.raises(ScheduleError, match="lead must be positive"):
        query(session(FIRST, NOW), lead_seconds=0)


def test_a_renewal_moves_the_window_and_keeps_its_length() -> None:
    definition = build_definition()
    renewed = renewal_of(
        RenewalRequest(definition, STAGE_ONE_PAYMENT_CATALOG, NOW + SESSION_SECONDS)
    )
    assert renewed.valid_after == NOW + SESSION_SECONDS
    assert renewed.valid_until == NOW + 2 * SESSION_SECONDS
    assert is_renewal(AuthorityChange(definition, renewed, STAGE_ONE_PAYMENT_CATALOG))


def test_a_renewal_changes_nothing_but_the_window() -> None:
    definition = build_definition()
    renewed = renewal_of(RenewalRequest(definition, STAGE_ONE_PAYMENT_CATALOG, NOW + 1))
    assert (
        replace(
            renewed,
            valid_after=definition.valid_after,
            valid_until=definition.valid_until,
        )
        == definition
    )


def test_a_renewal_cannot_move_the_window_backwards() -> None:
    definition = build_definition()
    with pytest.raises(AuthorityOrderError, match="same authority"):
        renewal_of(RenewalRequest(definition, STAGE_ONE_PAYMENT_CATALOG, NOW - 1))


def test_a_renewal_needs_the_time_it_is_made_at() -> None:
    with pytest.raises(ScheduleError, match="the time it is made at"):
        renewal_of(RenewalRequest(build_definition(), STAGE_ONE_PAYMENT_CATALOG, 0))


def test_a_renewal_of_a_narrowed_definition_keeps_the_narrowing() -> None:
    narrowed: AgentDefinition = build_definition(
        assets=(AssetRule(NATIVE_ASSET, ETHER // 4),)
    )
    renewed = renewal_of(
        RenewalRequest(narrowed, STAGE_ONE_PAYMENT_CATALOG, NOW + SESSION_SECONDS)
    )
    assert renewed.assets == narrowed.assets
