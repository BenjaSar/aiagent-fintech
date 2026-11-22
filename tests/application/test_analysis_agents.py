from dataclasses import replace

import pytest

from fintech_agent.adapters.in_memory import InMemorySealedStorage
from fintech_agent.adapters.local_proposer import (
    ReadOnlyProposer,
    WidestAllowedProposer,
)
from fintech_agent.application.agent_evaluation import (
    EvaluationBudget,
    EvaluationError,
    EvaluationReport,
    EvaluationRequest,
    Finding,
    evaluate,
)
from fintech_agent.application.agent_promotion import PromotionRequest, promote
from fintech_agent.application.agent_synthesis import (
    Candidate,
    GenerationCost,
    SynthesisError,
    SynthesisLimits,
    SynthesisRequest,
    synthesize,
)
from fintech_agent.application.policy_derivation import (
    DerivationError,
    derive_session_policy,
)
from fintech_agent.domain.agent_approval import (
    NO_AUTHORITY,
    AgentAcknowledgement,
    AgentApprovalError,
    AgentApprovalScreen,
    AgentPresentation,
    ApprovalBinding,
    agent_fingerprint,
    approve_agent,
    describe_agent,
)
from fintech_agent.domain.agent_definition import AgentDefinition, AgentType
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.approval_display import AssetDisplay, DisplayCatalog
from fintech_agent.domain.approval_text import render_agent_text
from fintech_agent.domain.policy_approval import ApprovalLimits
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule
from fintech_agent.domain.task_specification import (
    TaskSpecification,
    TaskSpecificationError,
)
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from tests.definition_fixtures import (
    OWNER,
    PAYEE,
    SESSION_SECONDS,
    WINDOW_SECONDS,
    build_definition,
    build_reader_definition,
    build_registry,
    build_request,
)
from tests.policy_fixtures import ETHER, NOW, TARGET

READER_TOOLS = ("list_payees", "get_payment_status")
PAYING_TOOL = "pay_payee"


def reader_specification(**overrides: object) -> TaskSpecification:
    specification = TaskSpecification(
        identifier="agent-report",
        agent_type=AgentType.ANALYSIS,
        purpose="Report on the payments the fleet has made.",
        model=build_definition().model,
        tools=READER_TOOLS,
        payees=(),
        assets=(),
        window_seconds=WINDOW_SECONDS,
        valid_after=NOW,
        valid_until=NOW + SESSION_SECONDS,
        budget=None,
        maximum_risk_tier=1,
        owner=OWNER,
        provenance_identifier="generation-1",
    )
    return replace(specification, **overrides)  # type: ignore[arg-type]


def synthesis(specification: TaskSpecification) -> SynthesisRequest:
    return SynthesisRequest(
        specification,
        STAGE_ONE_PAYMENT_CATALOG,
        build_registry(),
        SynthesisLimits(),
    )


def candidate_of(definition: AgentDefinition) -> Candidate:
    return Candidate(
        definition,
        definition_digest(definition),
        definition.model,
        GenerationCost(1_200, 400, 1_800),
    )


def reader_evaluation(definition: AgentDefinition | None = None) -> EvaluationRequest:
    return EvaluationRequest(
        candidate_of(definition or build_reader_definition()),
        None,
        {PAYEE: TARGET},
        STAGE_ONE_PAYMENT_CATALOG,
        0,
        InMemorySealedStorage,
        EvaluationBudget(),
        NOW,
    )


def named(report: EvaluationReport, name: str) -> Finding:
    for finding in (*report.checks, *report.scenarios):
        if finding.name == name:
            return finding
    raise AssertionError(f"{name} is not in the report")


def reader_presentation(**overrides: object) -> AgentPresentation:
    presentation = AgentPresentation(
        build_reader_definition(),
        None,
        b"",
        STAGE_ONE_PAYMENT_CATALOG,
        DisplayCatalog(AssetDisplay("ETH", 18), {}, {}),
        ApprovalLimits(),
        NOW,
    )
    return replace(presentation, **overrides)  # type: ignore[arg-type]


def reader_screen() -> AgentApprovalScreen:
    return describe_agent(reader_presentation())


def test_a_reader_is_synthesised_from_a_read_only_task() -> None:
    candidate = synthesize(synthesis(reader_specification()), ReadOnlyProposer())

    assert candidate.definition.agent_type is AgentType.ANALYSIS
    assert candidate.definition.payees == ()
    assert candidate.definition.assets == ()
    assert candidate.definition.budget is None


def test_a_task_that_allows_a_paying_tool_for_a_reader_is_refused() -> None:
    mistaken = reader_specification(tools=(*READER_TOOLS, PAYING_TOOL))

    with pytest.raises(SynthesisError, match="can move value"):
        synthesize(synthesis(mistaken), WidestAllowedProposer())


def test_a_reader_that_proposes_a_payee_is_refused() -> None:
    class PayeeSeeker:
        def propose(self, specification: TaskSpecification) -> object:
            return replace(ReadOnlyProposer().propose(specification), payees=(PAYEE,))

    with pytest.raises(SynthesisError, match="not a payee this task allows"):
        synthesize(synthesis(reader_specification()), PayeeSeeker())  # type: ignore[arg-type]


def test_a_reader_that_proposes_a_cap_is_refused() -> None:
    class CapSeeker:
        def propose(self, specification: TaskSpecification) -> object:
            return replace(
                ReadOnlyProposer().propose(specification),
                assets=(AssetRule(NATIVE_ASSET, ETHER),),
            )

    with pytest.raises(SynthesisError, match="no ceiling in this task"):
        synthesize(synthesis(reader_specification()), CapSeeker())  # type: ignore[arg-type]


def test_a_read_only_task_with_a_payee_is_refused() -> None:
    with pytest.raises(TaskSpecificationError, match="no payee"):
        reader_specification(payees=(PAYEE,))


def test_a_read_only_task_with_a_budget_is_refused() -> None:
    with pytest.raises(TaskSpecificationError, match="no payee"):
        reader_specification(budget=build_definition().budget)


def test_no_session_policy_is_derived_for_a_reader() -> None:
    request = build_request(build_reader_definition())

    with pytest.raises(DerivationError, match="hold no authority"):
        derive_session_policy(request)


def test_a_reader_passes_its_own_rubric_and_scenarios() -> None:
    report = evaluate(reader_evaluation())

    assert report.passed, report.failures
    assert report.score == "5/5 checks, 2/2 scenarios"


def test_a_reader_holding_a_paying_tool_fails_evaluation() -> None:
    paying = build_reader_definition(tools=(*READER_TOOLS, PAYING_TOOL))

    report = evaluate(reader_evaluation(paying))

    assert not named(report, "holds-no-paying-tool").passed
    assert not report.passed


def test_a_reader_evaluated_with_a_policy_fails_evaluation() -> None:
    request = replace(
        reader_evaluation(),
        policy=derive_session_policy(build_request(build_definition())),
    )

    report = evaluate(request)

    assert not named(report, "derives-no-policy").passed


def test_a_paying_candidate_cannot_be_evaluated_without_a_policy() -> None:
    request = replace(reader_evaluation(), candidate=candidate_of(build_definition()))

    with pytest.raises(EvaluationError, match="with a policy"):
        evaluate(request)


def test_a_readers_screen_shows_no_policy_and_asks_for_no_acknowledgement() -> None:
    screen = reader_screen()

    assert screen.policy_screen is None
    assert not screen.holds_authority
    assert screen.required_acknowledgements == frozenset()


def test_a_readers_fingerprint_binds_its_definition_alone() -> None:
    screen = reader_screen()

    expected = ApprovalBinding(definition_digest(screen.definition), "", b"")
    assert screen.binding == expected
    assert screen.fingerprint == agent_fingerprint(expected)


def test_a_readers_screen_says_it_holds_no_authority() -> None:
    text = render_agent_text(reader_screen())

    assert NO_AUTHORITY in text
    assert "Spending limits:" not in text
    assert "funded into its own account" not in text


def test_a_reader_presented_with_a_policy_is_refused() -> None:
    policy = derive_session_policy(build_request(build_definition()))

    with pytest.raises(AgentApprovalError, match="without a policy"):
        describe_agent(reader_presentation(policy=policy))


def test_a_reader_presented_with_a_session_key_is_refused() -> None:
    with pytest.raises(AgentApprovalError, match="without a policy"):
        describe_agent(reader_presentation(group_public_key=b"\x02" + bytes(32)))


def test_a_paying_agent_presented_without_a_policy_is_refused() -> None:
    presentation = reader_presentation(
        definition=build_definition(), group_public_key=b"\x02" + bytes(32)
    )

    with pytest.raises(AgentApprovalError, match="needs a policy"):
        describe_agent(presentation)


def test_an_approved_reader_holds_no_policy() -> None:
    screen = reader_screen()

    approved = approve_agent(
        screen,
        AgentAcknowledgement(screen.fingerprint, frozenset(), OWNER, NOW),
    )

    assert approved.policy is None
    assert approved.binding.group_public_key == b""


def test_a_reader_is_promoted_on_its_own_report() -> None:
    report = evaluate(reader_evaluation())

    promoted = promote(PromotionRequest(report, reader_presentation()))

    assert promoted.screen.policy_screen is None
