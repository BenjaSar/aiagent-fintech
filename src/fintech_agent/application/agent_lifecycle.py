from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum

from fintech_agent.application.agent_synthesis import Candidate
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.authority_order import (
    AuthorityChange,
    AuthorityWidenedError,
    require_renewal,
    widenings,
)
from fintech_agent.domain.fleet_identity import require_agent_identifier
from fintech_agent.domain.tool_catalog import ToolCatalog

MAXIMUM_ATTEMPTS = 3
MAXIMUM_COST_MICROS = 500_000


class LifecycleError(Exception):
    pass


class IterationBudgetError(LifecycleError):
    pass


class LifecycleState(Enum):
    GENERATED = "generated"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    ACTIVE = "active"
    REFUSED = "refused"
    RETIRED = "retired"


ALLOWED_MOVES: Mapping[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.GENERATED: frozenset(
        {LifecycleState.EVALUATED, LifecycleState.REFUSED}
    ),
    LifecycleState.EVALUATED: frozenset(
        {LifecycleState.APPROVED, LifecycleState.REFUSED}
    ),
    LifecycleState.APPROVED: frozenset({LifecycleState.ACTIVE, LifecycleState.REFUSED}),
    LifecycleState.ACTIVE: frozenset({LifecycleState.RETIRED}),
    LifecycleState.REFUSED: frozenset({LifecycleState.GENERATED}),
    LifecycleState.RETIRED: frozenset(),
}


@dataclass(frozen=True)
class IterationLimits:
    maximum_attempts: int = MAXIMUM_ATTEMPTS
    maximum_cost_micros: int = MAXIMUM_COST_MICROS

    def __post_init__(self) -> None:
        if self.maximum_attempts <= 0:
            raise LifecycleError("a loop needs at least one attempt")
        if self.maximum_cost_micros <= 0:
            raise LifecycleError("a loop needs a positive cost ceiling")


@dataclass(frozen=True)
class LifecycleParts:
    limits: IterationLimits
    catalog: ToolCatalog


@dataclass(frozen=True)
class AgentStanding:
    agent_identifier: str
    state: LifecycleState
    attempts: int
    cost_micros: int
    definition: AgentDefinition


class AgentLifecycle:
    def __init__(self, parts: LifecycleParts) -> None:
        self._parts = parts
        self._standings: dict[str, AgentStanding] = {}

    @property
    def limits(self) -> IterationLimits:
        return self._parts.limits

    def standing_of(self, agent_identifier: str) -> AgentStanding:
        standing = self._standings.get(agent_identifier)
        if standing is None:
            raise LifecycleError(f"{agent_identifier} has no lifecycle")
        return standing

    def begin(self, agent_identifier: str, candidate: Candidate) -> AgentStanding:
        require_agent_identifier(agent_identifier)
        if agent_identifier in self._standings:
            raise LifecycleError(f"{agent_identifier} already has a lifecycle")
        standing = AgentStanding(
            agent_identifier,
            LifecycleState.GENERATED,
            1,
            candidate.cost.currency_micros,
            candidate.definition,
        )
        self._require_affordable(standing)
        return self._store(standing)

    def iterate(self, agent_identifier: str, candidate: Candidate) -> AgentStanding:
        previous = self.standing_of(agent_identifier)
        self._require_attempt_left(previous)
        self._require_no_widening(previous, candidate)
        standing = replace(
            self._moved(previous, LifecycleState.GENERATED),
            attempts=previous.attempts + 1,
            cost_micros=previous.cost_micros + candidate.cost.currency_micros,
            definition=candidate.definition,
        )
        self._require_affordable(standing)
        return self._store(standing)

    def evaluated(self, agent_identifier: str) -> AgentStanding:
        return self._advance(agent_identifier, LifecycleState.EVALUATED)

    def approved(self, agent_identifier: str) -> AgentStanding:
        return self._advance(agent_identifier, LifecycleState.APPROVED)

    def activated(self, agent_identifier: str) -> AgentStanding:
        return self._advance(agent_identifier, LifecycleState.ACTIVE)

    def refused(self, agent_identifier: str) -> AgentStanding:
        return self._advance(agent_identifier, LifecycleState.REFUSED)

    def retired(self, agent_identifier: str) -> AgentStanding:
        return self._advance(agent_identifier, LifecycleState.RETIRED)

    def renewed(
        self, agent_identifier: str, definition: AgentDefinition
    ) -> AgentStanding:
        standing = self.standing_of(agent_identifier)
        if standing.state is not LifecycleState.ACTIVE:
            raise LifecycleError(
                f"{agent_identifier} is {standing.state.value}, so there is no "
                "session to renew"
            )
        require_renewal(self._change(standing, definition))
        return self._store(replace(standing, definition=definition))

    def _advance(self, agent_identifier: str, state: LifecycleState) -> AgentStanding:
        return self._store(self._moved(self.standing_of(agent_identifier), state))

    def _moved(self, standing: AgentStanding, state: LifecycleState) -> AgentStanding:
        if state not in ALLOWED_MOVES[standing.state]:
            raise LifecycleError(
                f"{standing.agent_identifier} cannot move from "
                f"{standing.state.value} to {state.value}"
            )
        return replace(standing, state=state)

    def _store(self, standing: AgentStanding) -> AgentStanding:
        self._standings[standing.agent_identifier] = standing
        return standing

    def _require_attempt_left(self, standing: AgentStanding) -> None:
        allowed = self._parts.limits.maximum_attempts
        if standing.attempts >= allowed:
            raise IterationBudgetError(
                f"{standing.agent_identifier} has used all {allowed} attempts"
            )

    def _require_affordable(self, standing: AgentStanding) -> None:
        ceiling = self._parts.limits.maximum_cost_micros
        if standing.cost_micros > ceiling:
            raise IterationBudgetError(
                f"{standing.agent_identifier} has spent {standing.cost_micros} micros "
                f"of the {ceiling} allowed"
            )

    def _require_no_widening(
        self, standing: AgentStanding, candidate: Candidate
    ) -> None:
        widened = widenings(self._change(standing, candidate.definition))
        if widened:
            raise AuthorityWidenedError(
                f"an iteration of {standing.agent_identifier} would widen its "
                "authority: " + "; ".join(widened)
            )

    def _change(
        self, standing: AgentStanding, definition: AgentDefinition
    ) -> AuthorityChange:
        return AuthorityChange(standing.definition, definition, self._parts.catalog)
