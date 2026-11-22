from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from fintech_agent.domain.authority_order import AuthorityOrderError

from .agent_lifecycle import LifecycleError
from .session_schedule import RENEWAL_LEAD_SECONDS, ScheduleError

ESCALATIONS = (LifecycleError, AuthorityOrderError)


class DueSessions(Protocol):
    def due(self, lead_seconds: int) -> tuple[str, ...]: ...


class SessionRenewer(Protocol):
    def renew(self, agent_identifier: str) -> None: ...


@dataclass(frozen=True)
class SchedulerParts:
    schedule: DueSessions
    renewer: SessionRenewer
    pause: Callable[[int], None]


@dataclass(frozen=True)
class ScheduleRun:
    ticks: int
    interval_seconds: int
    lead_seconds: int = RENEWAL_LEAD_SECONDS

    def __post_init__(self) -> None:
        if self.ticks <= 0:
            raise ScheduleError("a scheduler run needs at least one tick")
        if self.interval_seconds < 0:
            raise ScheduleError("the interval between ticks cannot be negative")
        if self.lead_seconds <= 0:
            raise ScheduleError("the renewal lead must be positive")


@dataclass(frozen=True)
class RenewalAttempt:
    agent_identifier: str
    reason: str


@dataclass(frozen=True)
class Escalation:
    agent_identifier: str
    reason: str


@dataclass(frozen=True)
class TickReport:
    renewed: tuple[str, ...]
    escalated: tuple[Escalation, ...]


class RenewalScheduler:
    def __init__(self, parts: SchedulerParts) -> None:
        self._parts = parts

    def tick(self, lead_seconds: int = RENEWAL_LEAD_SECONDS) -> TickReport:
        attempts = [
            self._attempt(identifier)
            for identifier in self._parts.schedule.due(lead_seconds)
        ]
        return TickReport(
            tuple(
                attempt.agent_identifier for attempt in attempts if not attempt.reason
            ),
            tuple(
                Escalation(attempt.agent_identifier, attempt.reason)
                for attempt in attempts
                if attempt.reason
            ),
        )

    def run(self, schedule: ScheduleRun) -> TickReport:
        renewed: list[str] = []
        escalated: list[Escalation] = []
        for _ in range(schedule.ticks):
            report = self.tick(schedule.lead_seconds)
            renewed.extend(report.renewed)
            escalated.extend(report.escalated)
            self._parts.pause(schedule.interval_seconds)
        return TickReport(tuple(renewed), tuple(escalated))

    def _attempt(self, agent_identifier: str) -> RenewalAttempt:
        try:
            self._parts.renewer.renew(agent_identifier)
        except ESCALATIONS as escalation:
            return RenewalAttempt(agent_identifier, str(escalation))
        return RenewalAttempt(agent_identifier, "")
