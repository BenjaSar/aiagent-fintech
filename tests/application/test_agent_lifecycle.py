from dataclasses import replace

import pytest

from fintech_agent.application.agent_lifecycle import (
    AgentLifecycle,
    IterationBudgetError,
    IterationLimits,
    LifecycleError,
    LifecycleParts,
    LifecycleState,
)
from fintech_agent.application.agent_synthesis import Candidate, GenerationCost
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.authority_order import AuthorityWidenedError
from fintech_agent.domain.fleet_identity import FleetIdentityError
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from tests.definition_fixtures import SESSION_SECONDS, build_definition
from tests.policy_fixtures import ETHER, NOW

AGENT = "agent-one"
COST_MICROS = 1_800


def candidate_of(
    definition: AgentDefinition | None = None, micros: int = COST_MICROS
) -> Candidate:
    definition = definition or build_definition()
    return Candidate(
        definition,
        definition_digest(definition),
        definition.model,
        GenerationCost(1_200, 400, micros),
    )


def lifecycle(limits: IterationLimits | None = None) -> AgentLifecycle:
    return AgentLifecycle(
        LifecycleParts(limits or IterationLimits(), STAGE_ONE_PAYMENT_CATALOG)
    )


def begun(limits: IterationLimits | None = None) -> AgentLifecycle:
    loop = lifecycle(limits)
    loop.begin(AGENT, candidate_of())
    return loop


def test_a_lifecycle_begins_at_its_first_generation() -> None:
    standing = begun().standing_of(AGENT)
    assert standing.state is LifecycleState.GENERATED
    assert standing.attempts == 1
    assert standing.cost_micros == COST_MICROS
    assert standing.definition == build_definition()


def test_an_unknown_agent_has_no_standing() -> None:
    with pytest.raises(LifecycleError, match="no lifecycle"):
        lifecycle().standing_of(AGENT)


def test_an_agent_cannot_begin_twice() -> None:
    loop = begun()
    with pytest.raises(LifecycleError, match="already has a lifecycle"):
        loop.begin(AGENT, candidate_of())


def test_a_malformed_identifier_is_refused() -> None:
    with pytest.raises(FleetIdentityError):
        lifecycle().begin("Agent One", candidate_of())


def test_the_happy_path_runs_to_active_and_then_retired() -> None:
    loop = begun()
    loop.evaluated(AGENT)
    loop.approved(AGENT)
    assert loop.activated(AGENT).state is LifecycleState.ACTIVE
    assert loop.retired(AGENT).state is LifecycleState.RETIRED


def test_a_state_cannot_be_skipped() -> None:
    loop = begun()
    with pytest.raises(LifecycleError, match="cannot move from generated to active"):
        loop.activated(AGENT)


def test_a_retired_agent_moves_nowhere() -> None:
    loop = begun()
    loop.evaluated(AGENT)
    loop.approved(AGENT)
    loop.activated(AGENT)
    loop.retired(AGENT)
    with pytest.raises(LifecycleError, match="cannot move from retired"):
        loop.activated(AGENT)


def test_an_iteration_follows_a_refusal_and_counts_against_the_budget() -> None:
    loop = begun()
    loop.evaluated(AGENT)
    loop.refused(AGENT)
    standing = loop.iterate(AGENT, candidate_of())
    assert standing.state is LifecycleState.GENERATED
    assert standing.attempts == 2
    assert standing.cost_micros == 2 * COST_MICROS


def test_an_iteration_without_a_refusal_is_refused() -> None:
    loop = begun()
    with pytest.raises(LifecycleError, match="cannot move from generated"):
        loop.iterate(AGENT, candidate_of())


def test_the_attempt_budget_is_enforced() -> None:
    loop = begun(IterationLimits(maximum_attempts=2))
    loop.evaluated(AGENT)
    loop.refused(AGENT)
    loop.iterate(AGENT, candidate_of())
    loop.evaluated(AGENT)
    loop.refused(AGENT)
    with pytest.raises(IterationBudgetError, match="all 2 attempts"):
        loop.iterate(AGENT, candidate_of())


def test_the_cost_ceiling_is_enforced_across_attempts() -> None:
    loop = begun(IterationLimits(maximum_cost_micros=3_000))
    loop.evaluated(AGENT)
    loop.refused(AGENT)
    with pytest.raises(IterationBudgetError, match="3600 micros of the 3000 allowed"):
        loop.iterate(AGENT, candidate_of())


def test_the_cost_ceiling_is_enforced_on_the_first_attempt() -> None:
    loop = lifecycle(IterationLimits(maximum_cost_micros=1_000))
    with pytest.raises(IterationBudgetError, match="1800 micros of the 1000 allowed"):
        loop.begin(AGENT, candidate_of())


def test_a_refused_attempt_over_the_budget_leaves_the_earlier_standing() -> None:
    loop = begun(IterationLimits(maximum_attempts=1))
    loop.evaluated(AGENT)
    loop.refused(AGENT)
    with pytest.raises(IterationBudgetError):
        loop.iterate(AGENT, candidate_of())
    standing = loop.standing_of(AGENT)
    assert standing.state is LifecycleState.REFUSED
    assert standing.attempts == 1


def test_an_iteration_that_would_widen_authority_is_refused() -> None:
    loop = begun()
    loop.evaluated(AGENT)
    loop.refused(AGENT)
    wider = build_definition(assets=(AssetRule(NATIVE_ASSET, 2 * ETHER),))
    with pytest.raises(AuthorityWidenedError, match="the cap for"):
        loop.iterate(AGENT, candidate_of(wider))


def test_an_iteration_that_narrows_authority_is_allowed() -> None:
    loop = begun()
    loop.evaluated(AGENT)
    loop.refused(AGENT)
    narrower = build_definition(assets=(AssetRule(NATIVE_ASSET, ETHER // 2),))
    assert loop.iterate(AGENT, candidate_of(narrower)).definition == narrower


def test_an_active_session_is_renewed_in_place() -> None:
    loop = begun()
    loop.evaluated(AGENT)
    loop.approved(AGENT)
    loop.activated(AGENT)
    definition = build_definition()
    renewed = replace(
        definition,
        valid_after=NOW + SESSION_SECONDS,
        valid_until=NOW + 2 * SESSION_SECONDS,
    )
    standing = loop.renewed(AGENT, renewed)
    assert standing.state is LifecycleState.ACTIVE
    assert standing.definition == renewed


def test_a_renewal_that_widens_authority_is_refused() -> None:
    loop = begun()
    loop.evaluated(AGENT)
    loop.approved(AGENT)
    loop.activated(AGENT)
    wider = replace(
        build_definition(),
        valid_after=NOW + SESSION_SECONDS,
        valid_until=NOW + 2 * SESSION_SECONDS,
        assets=(AssetRule(NATIVE_ASSET, 2 * ETHER),),
    )
    with pytest.raises(AuthorityWidenedError, match="the cap for"):
        loop.renewed(AGENT, wider)
    assert loop.standing_of(AGENT).definition == build_definition()


def test_only_an_active_session_can_be_renewed() -> None:
    loop = begun()
    with pytest.raises(LifecycleError, match="generated, so there is no session"):
        loop.renewed(AGENT, build_definition())


def test_limits_must_be_positive() -> None:
    with pytest.raises(LifecycleError, match="at least one attempt"):
        IterationLimits(maximum_attempts=0)
    with pytest.raises(LifecycleError, match="positive cost ceiling"):
        IterationLimits(maximum_cost_micros=0)


def test_two_agents_keep_separate_standings() -> None:
    loop = begun()
    loop.begin("agent-two", candidate_of())
    loop.evaluated(AGENT)
    assert loop.standing_of(AGENT).state is LifecycleState.EVALUATED
    assert loop.standing_of("agent-two").state is LifecycleState.GENERATED
