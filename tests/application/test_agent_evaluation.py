from dataclasses import replace
from typing import Any

import pytest

from fintech_agent.adapters.in_memory import InMemorySealedStorage
from fintech_agent.application.agent_evaluation import (
    EvaluationBudget,
    EvaluationReport,
    EvaluationRequest,
    Finding,
    evaluate,
)
from fintech_agent.application.agent_promotion import (
    PromotionRefusedError,
    PromotionRequest,
    promote,
)
from fintech_agent.application.agent_synthesis import Candidate, GenerationCost
from fintech_agent.application.policy_derivation import derive_session_policy
from fintech_agent.domain.agent_approval import AgentPresentation
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.approval_display import AssetDisplay, DisplayCatalog
from fintech_agent.domain.policy_approval import ApprovalLimits
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule, SessionPolicy
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from tests.definition_fixtures import (
    PAYEE,
    build_definition,
    build_registry,
    build_request,
)
from tests.policy_fixtures import ETHER, NOW, TARGET

SESSION_KEY = bytes.fromhex("02" + "44" * 32)
BALANCE = ETHER // 4
UNLIMITED = 2**200


def candidate_of(definition: AgentDefinition, micros: int = 1_800) -> Candidate:
    return Candidate(
        definition,
        definition_digest(definition),
        definition.model,
        GenerationCost(1_200, 400, micros),
    )


def evaluation(
    definition: AgentDefinition | None = None,
    policy: SessionPolicy | None = None,
    budget: EvaluationBudget | None = None,
    micros: int = 1_800,
) -> EvaluationRequest:
    definition = definition or build_definition()
    return EvaluationRequest(
        candidate_of(definition, micros),
        policy or derive_session_policy(build_request(definition)),
        {PAYEE: TARGET},
        STAGE_ONE_PAYMENT_CATALOG,
        BALANCE,
        InMemorySealedStorage,
        budget or EvaluationBudget(),
        NOW,
    )


def report_of(**overrides: Any) -> EvaluationReport:
    return evaluate(evaluation(**overrides))


def named(report: EvaluationReport, name: str) -> Finding:
    for finding in (*report.checks, *report.scenarios):
        if finding.name == name:
            return finding
    raise AssertionError(f"{name} is not in the report")


def test_a_sound_candidate_passes_every_check_and_scenario() -> None:
    report = report_of()
    assert report.passed
    assert report.failures == ()
    assert len(report.scenarios) == 7
    assert report.score == "7/7 checks, 7/7 scenarios"
    assert report.candidate_digest == definition_digest(build_definition())


def test_the_scenarios_exercise_the_enclave_side_policy() -> None:
    report = report_of()
    for name in (
        "pays-an-approved-payee",
        "pays-once-on-a-retry",
        "refuses-an-unapproved-payee",
        "refuses-above-the-cap",
        "refuses-above-the-balance",
        "refuses-cap-splitting",
        "refuses-after-the-session-ends",
    ):
        assert named(report, name).passed, name


def test_a_candidate_that_cannot_check_a_retry_is_refused() -> None:
    definition = build_definition(tools=("list_payees", "get_balance", "pay_payee"))
    report = report_of(definition=definition)
    assert not report.passed
    assert not named(report, "can-check-a-retry").passed
    assert "get_payment_status" in named(report, "can-check-a-retry").detail


def test_a_candidate_with_too_many_payees_is_refused() -> None:
    registry = build_registry()
    payees = (PAYEE,)
    budget = EvaluationBudget(maximum_payees=0)
    report = report_of(budget=budget)
    assert not report.passed
    assert not named(report, "payees-are-few").passed
    assert registry.identifiers == payees


def test_a_candidate_with_an_unbounded_cap_is_refused() -> None:
    definition = build_definition(assets=(AssetRule(NATIVE_ASSET, UNLIMITED),))
    policy = replace(
        derive_session_policy(build_request(build_definition())),
        rules=(AssetRule(NATIVE_ASSET, UNLIMITED),),
    )
    report = report_of(definition=definition, policy=policy)
    assert not named(report, "caps-are-bounded").passed


def test_a_session_spanning_many_windows_is_refused() -> None:
    definition = build_definition(valid_until=NOW + 5 * 86_400)
    report = report_of(definition=definition)
    assert not named(report, "session-spans-few-windows").passed
    assert "6 rolling windows" in named(report, "session-spans-few-windows").detail


def test_a_generation_over_the_cost_budget_is_refused() -> None:
    report = report_of(micros=250_001)
    assert not report.passed
    assert not named(report, "within-the-cost-budget").passed


def test_an_evaluation_slower_than_its_budget_is_refused() -> None:
    report = evaluate(evaluation(budget=EvaluationBudget(maximum_seconds=0.0)))
    assert not named(report, "within-the-time-budget").passed
    assert report.seconds > 0


def test_a_policy_that_is_not_payments_only_is_refused() -> None:
    policy = replace(
        derive_session_policy(build_request(build_definition())),
        payments_only=False,
        allowed_payees=frozenset(),
    )
    report = report_of(policy=policy)
    assert not named(report, "authority-is-payments-only").passed


def presentation(definition: AgentDefinition | None = None) -> AgentPresentation:
    definition = definition or build_definition()
    return AgentPresentation(
        definition,
        derive_session_policy(build_request(definition)),
        SESSION_KEY,
        STAGE_ONE_PAYMENT_CATALOG,
        DisplayCatalog(AssetDisplay("ETH", 18), {}, build_registry().labels_by_address),
        ApprovalLimits(),
        NOW,
    )


def test_a_passing_candidate_is_promoted_to_a_screen() -> None:
    report = report_of()
    promoted = promote(PromotionRequest(report, presentation()))
    assert promoted.report is report
    assert promoted.screen.definition == build_definition()
    assert promoted.screen.fingerprint


def test_a_failing_candidate_is_never_promoted() -> None:
    definition = build_definition(tools=("list_payees", "get_balance", "pay_payee"))
    report = report_of(definition=definition)
    with pytest.raises(PromotionRefusedError, match="failed evaluation"):
        promote(PromotionRequest(report, presentation(definition)))


def test_a_report_for_another_candidate_is_never_promoted() -> None:
    report = report_of()
    other = build_definition(name="Another Agent")
    with pytest.raises(PromotionRefusedError, match="another candidate"):
        promote(PromotionRequest(report, presentation(other)))


def test_a_candidate_the_approval_screen_refuses_is_never_promoted() -> None:
    definition = build_definition(valid_until=NOW + 86_400)
    report = report_of(definition=definition)
    assert report.passed
    stale = replace(presentation(definition), now=NOW + 86_401)
    with pytest.raises(PromotionRefusedError, match="approval screen refused"):
        promote(PromotionRequest(report, stale))


def test_the_report_names_every_failure() -> None:
    definition = build_definition(tools=("list_payees", "get_balance", "pay_payee"))
    report = report_of(definition=definition, micros=250_001)
    assert len(report.failures) == 2
    assert any("can-check-a-retry" in failure for failure in report.failures)
    assert any("within-the-cost-budget" in failure for failure in report.failures)


def test_an_agent_that_cannot_pay_fails_the_paying_scenarios() -> None:
    crippled = build_definition(
        tools=("list_payees", "get_balance", "get_payment_status")
    )
    sound_policy = derive_session_policy(build_request(build_definition()))
    report = report_of(definition=crippled, policy=sound_policy)
    assert not report.passed
    assert report.score == "7/7 checks, 4/7 scenarios"
    assert not named(report, "pays-an-approved-payee").passed
    assert not named(report, "pays-once-on-a-retry").passed
    assert not named(report, "refuses-cap-splitting").passed
    assert named(report, "refuses-an-unapproved-payee").passed
    assert all(
        failure.startswith(("pays-", "refuses-cap")) for failure in report.failures
    )
    assert len(report.failures) == 3


def test_a_report_that_fails_only_a_scenario_is_never_promoted() -> None:
    crippled = build_definition(
        tools=("list_payees", "get_balance", "get_payment_status")
    )
    sound_policy = derive_session_policy(build_request(build_definition()))
    report = report_of(definition=crippled, policy=sound_policy)
    shown = replace(presentation(), definition=crippled)
    with pytest.raises(PromotionRefusedError, match="pays-an-approved-payee"):
        promote(PromotionRequest(report, shown))
