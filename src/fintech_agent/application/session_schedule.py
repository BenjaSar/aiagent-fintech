from dataclasses import dataclass, replace

from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.authority_order import AuthorityChange, require_renewal
from fintech_agent.domain.fleet_identity import require_agent_identifier
from fintech_agent.domain.tool_catalog import ToolCatalog

RENEWAL_LEAD_SECONDS = 3_600


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class ScheduledSession:
    agent_identifier: str
    definition: AgentDefinition


@dataclass(frozen=True)
class ScheduleQuery:
    sessions: tuple[ScheduledSession, ...]
    now: int
    lead_seconds: int = RENEWAL_LEAD_SECONDS

    def __post_init__(self) -> None:
        if self.now <= 0:
            raise ScheduleError("the schedule needs the time it is read at")
        if self.lead_seconds <= 0:
            raise ScheduleError("the renewal lead must be positive")
        identifiers = [session.agent_identifier for session in self.sessions]
        for identifier in identifiers:
            require_agent_identifier(identifier)
        if len(set(identifiers)) != len(identifiers):
            raise ScheduleError("a schedule holds each agent once")


@dataclass(frozen=True)
class RenewalRequest:
    definition: AgentDefinition
    catalog: ToolCatalog
    now: int


def due_for_renewal(query: ScheduleQuery) -> tuple[str, ...]:
    return tuple(
        sorted(
            session.agent_identifier
            for session in query.sessions
            if session.definition.valid_until - query.now <= query.lead_seconds
        )
    )


def renewal_of(request: RenewalRequest) -> AgentDefinition:
    definition = request.definition
    if request.now <= 0:
        raise ScheduleError("a renewal needs the time it is made at")
    length = definition.valid_until - definition.valid_after
    renewed = replace(
        definition, valid_after=request.now, valid_until=request.now + length
    )
    require_renewal(AuthorityChange(definition, renewed, request.catalog))
    return renewed
