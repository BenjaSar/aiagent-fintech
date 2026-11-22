from fintech_agent.adapters.in_memory import InMemorySealedStorage
from fintech_agent.application.agent_development import (
    DevelopmentParts,
    DevelopmentRequest,
    develop,
)
from fintech_agent.application.agent_evaluation import (
    STATUS_TOOL,
    EvaluationBudget,
    EvaluationReport,
    EvaluationRequest,
    evaluate,
)
from fintech_agent.application.agent_lifecycle import (
    AgentLifecycle,
    IterationLimits,
    LifecycleParts,
    LifecycleState,
)
from fintech_agent.application.agent_synthesis import Candidate, GenerationCost
from fintech_agent.application.policy_derivation import derive_session_policy
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from tests.definition_fixtures import PAYEE, build_definition, build_request
from tests.policy_fixtures import ETHER, NOW, TARGET

AGENT = "agent-one"
BALANCE = ETHER // 4
COST_MICROS = 1_800
HALF_CAP = (AssetRule(NATIVE_ASSET, ETHER // 2),)
FULL_CAP = (AssetRule(NATIVE_ASSET, ETHER),)
WITHOUT_STATUS = ("list_payees", "get_balance", "pay_payee")


def candidate_of(definition: AgentDefinition, micros: int = COST_MICROS) -> Candidate:
    return Candidate(
        definition,
        definition_digest(definition),
        definition.model,
        GenerationCost(1_200, 400, micros),
    )


class ScriptedSource:
    def __init__(self, candidates: tuple[Candidate, ...]) -> None:
        self._candidates = candidates
        self.feedback: list[tuple[str, ...]] = []

    def generate(self, feedback: tuple[str, ...]) -> Candidate:
        self.feedback.append(feedback)
        return self._candidates[min(len(self.feedback) - 1, len(self._candidates) - 1)]


class RealJudge:
    def judge(self, candidate: Candidate) -> EvaluationReport:
        return evaluate(
            EvaluationRequest(
                candidate,
                derive_session_policy(build_request(candidate.definition)),
                {PAYEE: TARGET},
                STAGE_ONE_PAYMENT_CATALOG,
                BALANCE,
                InMemorySealedStorage,
                EvaluationBudget(),
                NOW,
            )
        )


def crippled(micros: int = COST_MICROS) -> Candidate:
    return candidate_of(build_definition(tools=WITHOUT_STATUS, assets=HALF_CAP), micros)


def sound(assets: tuple[AssetRule, ...] = HALF_CAP) -> Candidate:
    return candidate_of(build_definition(assets=assets))


def run(
    candidates: tuple[Candidate, ...], limits: IterationLimits | None = None
) -> tuple[DevelopmentParts, ScriptedSource]:
    source = ScriptedSource(candidates)
    lifecycle = AgentLifecycle(
        LifecycleParts(limits or IterationLimits(), STAGE_ONE_PAYMENT_CATALOG)
    )
    return DevelopmentParts(lifecycle, source, RealJudge()), source


def test_a_sound_candidate_is_accepted_on_the_first_attempt() -> None:
    parts, source = run((sound(),))
    outcome = develop(DevelopmentRequest(parts, AGENT))
    assert outcome.accepted is not None
    assert len(outcome.attempts) == 1
    assert outcome.escalation == ""
    assert outcome.cost_micros == COST_MICROS
    assert source.feedback == [()]
    assert parts.lifecycle.standing_of(AGENT).state is LifecycleState.EVALUATED


def test_a_failing_candidate_is_iterated_and_the_second_attempt_is_accepted() -> None:
    parts, _ = run((crippled(), sound()))
    outcome = develop(DevelopmentRequest(parts, AGENT))
    assert outcome.accepted is outcome.attempts[1]
    assert len(outcome.attempts) == 2
    assert outcome.cost_micros == 2 * COST_MICROS
    assert outcome.summary == "accepted on attempt 2 for 3600 micros"
    assert parts.lifecycle.standing_of(AGENT).attempts == 2


def test_the_failures_of_an_attempt_are_the_feedback_for_the_next() -> None:
    parts, source = run((crippled(), sound()))
    develop(DevelopmentRequest(parts, AGENT))
    assert source.feedback[0] == ()
    assert any(STATUS_TOOL in line for line in source.feedback[1])


def test_a_candidate_that_keeps_failing_is_escalated_at_the_attempt_budget() -> None:
    parts, _ = run((crippled(),), IterationLimits(maximum_attempts=3))
    outcome = develop(DevelopmentRequest(parts, AGENT))
    assert outcome.accepted is None
    assert len(outcome.attempts) == 3
    assert "all 3 attempts" in outcome.escalation
    assert parts.lifecycle.standing_of(AGENT).state is LifecycleState.REFUSED


def test_one_attempt_is_a_legitimate_budget() -> None:
    parts, _ = run((crippled(),), IterationLimits(maximum_attempts=1))
    outcome = develop(DevelopmentRequest(parts, AGENT))
    assert len(outcome.attempts) == 1
    assert "all 1 attempts" in outcome.escalation


def test_an_iteration_that_widens_authority_is_escalated_not_installed() -> None:
    parts, _ = run((crippled(), sound(FULL_CAP)))
    outcome = develop(DevelopmentRequest(parts, AGENT))
    assert outcome.accepted is None
    assert len(outcome.attempts) == 1
    assert "would widen its authority" in outcome.escalation
    assert f"the cap for {NATIVE_ASSET} rises" in outcome.escalation


def test_a_loop_over_its_cost_ceiling_is_escalated() -> None:
    parts, _ = run((crippled(), sound()), IterationLimits(maximum_cost_micros=3_000))
    outcome = develop(DevelopmentRequest(parts, AGENT))
    assert outcome.accepted is None
    assert len(outcome.attempts) == 1
    assert "3600 micros of the 3000 allowed" in outcome.escalation


def test_a_first_candidate_over_the_cost_ceiling_is_escalated_before_evaluation() -> (
    None
):
    parts, _ = run(
        (crippled(micros=9_000),), IterationLimits(maximum_cost_micros=1_000)
    )
    outcome = develop(DevelopmentRequest(parts, AGENT))
    assert outcome.attempts == ()
    assert outcome.accepted is None
    assert "9000 micros of the 1000 allowed" in outcome.escalation
    assert outcome.summary == (
        "escalated after 0 attempts: agent-one has spent 9000 micros of the 1000 allowed"
    )


def test_a_crippled_candidate_really_does_fail_the_judge() -> None:
    report = RealJudge().judge(crippled())
    assert not report.passed
    assert any(STATUS_TOOL in failure for failure in report.failures)
    assert RealJudge().judge(sound()).passed
