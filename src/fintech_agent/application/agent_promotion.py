from dataclasses import dataclass

from fintech_agent.application.agent_evaluation import EvaluationReport
from fintech_agent.domain.agent_approval import (
    AgentApprovalError,
    AgentApprovalScreen,
    AgentPresentation,
    describe_agent,
)
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.policy_approval import ApprovalRefusedError


class PromotionRefusedError(Exception):
    pass


@dataclass(frozen=True)
class PromotionRequest:
    report: EvaluationReport
    presentation: AgentPresentation


@dataclass(frozen=True)
class PromotedCandidate:
    screen: AgentApprovalScreen
    report: EvaluationReport


def promote(request: PromotionRequest) -> PromotedCandidate:
    _require_the_report_is_for_this_candidate(request)
    _require_the_report_passed(request.report)
    return PromotedCandidate(_screened(request), request.report)


def _require_the_report_is_for_this_candidate(request: PromotionRequest) -> None:
    digest = definition_digest(request.presentation.definition)
    if request.report.candidate_digest != digest:
        raise PromotionRefusedError("the report is for another candidate")


def _require_the_report_passed(report: EvaluationReport) -> None:
    if not report.passed:
        raise PromotionRefusedError(
            "the candidate failed evaluation: " + "; ".join(report.failures)
        )


def _screened(request: PromotionRequest) -> AgentApprovalScreen:
    try:
        return describe_agent(request.presentation)
    except (AgentApprovalError, ApprovalRefusedError) as refusal:
        raise PromotionRefusedError(
            f"the approval screen refused the candidate: {refusal}"
        ) from refusal
