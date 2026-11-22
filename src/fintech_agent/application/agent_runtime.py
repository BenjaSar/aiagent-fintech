from dataclasses import dataclass, field
from typing import Protocol

from fintech_agent.application.agent_tools import (
    ToolInvocation,
    ToolResult,
    ToolRunner,
)
from fintech_agent.application.interaction_trace import NoTrace, RunEvent, RunTrace

TASK_STEP = "task"
FINISHED_STEP = "finished"
STOPPED_STEP = "stopped"

MAXIMUM_STEPS = 8


@dataclass(frozen=True)
class PaymentTask:
    description: str
    payee: str
    asset: str
    amount: int
    idempotency_key: str


@dataclass(frozen=True)
class ReportTask:
    description: str
    payee: str
    idempotency_key: str


AgentTask = PaymentTask | ReportTask


@dataclass(frozen=True)
class Situation:
    task: AgentTask
    history: tuple[ToolResult, ...]


@dataclass(frozen=True)
class CallTool:
    invocation: ToolInvocation


@dataclass(frozen=True)
class Finish:
    summary: str


AgentAction = CallTool | Finish


class AgentDecider(Protocol):
    def decide(self, situation: Situation) -> AgentAction: ...


@dataclass(frozen=True)
class RuntimeParts:
    runner: ToolRunner
    decider: AgentDecider
    maximum_steps: int = MAXIMUM_STEPS
    trace: RunTrace = field(default_factory=NoTrace)


@dataclass(frozen=True)
class RuntimeOutcome:
    summary: str
    results: tuple[ToolResult, ...]
    steps: int
    stopped_early: bool


class AgentRuntime:
    def __init__(self, parts: RuntimeParts) -> None:
        self._parts = parts

    def run(self, task: AgentTask) -> RuntimeOutcome:
        history: list[ToolResult] = []
        self._parts.trace.record(RunEvent(TASK_STEP, task.description))
        for step in range(self._parts.maximum_steps):
            action = self._parts.decider.decide(Situation(task, tuple(history)))
            if isinstance(action, Finish):
                self._parts.trace.record(RunEvent(FINISHED_STEP, action.summary))
                return RuntimeOutcome(action.summary, tuple(history), step, False)
            history.append(self._recorded(action.invocation))
        summary = "the agent reached its step limit"
        self._parts.trace.record(RunEvent(STOPPED_STEP, summary))
        return RuntimeOutcome(summary, tuple(history), self._parts.maximum_steps, True)

    def _recorded(self, invocation: ToolInvocation) -> ToolResult:
        result = self._parts.runner.invoke(invocation)
        self._parts.trace.record(RunEvent(invocation.name, _detail_of(result)))
        return result


def _detail_of(result: ToolResult) -> str:
    if not result.lines:
        return ""
    return result.lines[0]
