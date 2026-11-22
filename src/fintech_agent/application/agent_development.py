from dataclasses import dataclass
from typing import Protocol

from fintech_agent.application.agent_evaluation import EvaluationReport
from fintech_agent.application.agent_lifecycle import AgentLifecycle, LifecycleError
from fintech_agent.application.agent_synthesis import Candidate
from fintech_agent.domain.authority_order import AuthorityOrderError

ESCALATIONS = (LifecycleError, AuthorityOrderError)


class CandidateSource(Protocol):
    def generate(self, feedback: tuple[str, ...]) -> Candidate: ...


class CandidateJudge(Protocol):
    def judge(self, candidate: Candidate) -> EvaluationReport: ...


@dataclass(frozen=True)
class DevelopmentParts:
    lifecycle: AgentLifecycle
    source: CandidateSource
    judge: CandidateJudge


@dataclass(frozen=True)
class DevelopmentRequest:
    parts: DevelopmentParts
    agent_identifier: str


@dataclass(frozen=True)
class Attempt:
    candidate: Candidate
    report: EvaluationReport


@dataclass(frozen=True)
class DevelopmentOutcome:
    agent_identifier: str
    attempts: tuple[Attempt, ...]
    accepted: Attempt | None
    escalation: str

    @property
    def cost_micros(self) -> int:
        return sum(attempt.candidate.cost.currency_micros for attempt in self.attempts)

    @property
    def summary(self) -> str:
        if self.accepted is not None:
            return (
                f"accepted on attempt {len(self.attempts)} for "
                f"{self.cost_micros} micros"
            )
        return f"escalated after {len(self.attempts)} attempts: {self.escalation}"


def develop(request: DevelopmentRequest) -> DevelopmentOutcome:
    parts = request.parts
    try:
        opening = parts.source.generate(())
        parts.lifecycle.begin(request.agent_identifier, opening)
    except ESCALATIONS as escalation:
        return DevelopmentOutcome(request.agent_identifier, (), None, str(escalation))
    return _loop(request, opening)


def _loop(request: DevelopmentRequest, opening: Candidate) -> DevelopmentOutcome:
    parts = request.parts
    identifier = request.agent_identifier
    candidate = opening
    attempts: list[Attempt] = []
    while True:
        report = parts.judge.judge(candidate)
        parts.lifecycle.evaluated(identifier)
        attempts.append(Attempt(candidate, report))
        if report.passed:
            return DevelopmentOutcome(identifier, tuple(attempts), attempts[-1], "")
        parts.lifecycle.refused(identifier)
        try:
            candidate = parts.source.generate(report.failures)
            parts.lifecycle.iterate(identifier, candidate)
        except ESCALATIONS as escalation:
            return DevelopmentOutcome(
                identifier, tuple(attempts), None, str(escalation)
            )
