from dataclasses import dataclass
from typing import Protocol

from fintech_agent.application.fleet_ledger import FleetLedger
from fintech_agent.domain.fleet_identity import require_agent_identifier


class RetirementError(Exception):
    pass


class SessionTeardown(Protocol):
    def uninstall_validator(self) -> None: ...

    def installed_key(self) -> bytes: ...


class AllowanceSweep(Protocol):
    def balance(self) -> int: ...

    def sweep(self) -> int: ...


class EnclaveSet(Protocol):
    def stop(self) -> None: ...

    def reachable(self) -> bool: ...


@dataclass(frozen=True)
class RetirementParts:
    teardown: SessionTeardown
    sweep: AllowanceSweep
    enclaves: EnclaveSet
    ledger: FleetLedger


@dataclass(frozen=True)
class RetirementOutcome:
    agent_identifier: str
    swept: int
    freed: int


class RetireAgent:
    def __init__(self, parts: RetirementParts) -> None:
        self._parts = parts

    def execute(self, agent_identifier: str) -> RetirementOutcome:
        require_agent_identifier(agent_identifier)
        parts = self._parts
        before = parts.ledger.remaining
        parts.teardown.uninstall_validator()
        swept = parts.sweep.sweep()
        parts.enclaves.stop()
        self._record(agent_identifier, swept)
        self._require_torn_down(agent_identifier)
        return RetirementOutcome(
            agent_identifier, swept, parts.ledger.remaining - before
        )

    def _record(self, agent_identifier: str, swept: int) -> None:
        ledger = self._parts.ledger
        ledger.retire(agent_identifier)
        if swept:
            ledger.record_sweep(agent_identifier, swept)

    def _require_torn_down(self, agent_identifier: str) -> None:
        parts = self._parts
        if parts.teardown.installed_key():
            raise RetirementError(f"{agent_identifier} still holds a session key")
        if parts.sweep.balance():
            raise RetirementError(
                f"{agent_identifier} still holds {parts.sweep.balance()} of its "
                "allowance"
            )
        if parts.enclaves.reachable():
            raise RetirementError(f"{agent_identifier} still has reachable enclaves")
        if not parts.ledger.member(agent_identifier).retired:
            raise RetirementError(f"{agent_identifier} is still a serving member")
