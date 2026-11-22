from dataclasses import dataclass, replace

from fintech_agent.domain.fleet_identity import require_agent_identifier
from fintech_agent.domain.session_policy import is_normalized_address


class FleetLedgerError(ValueError):
    pass


class FleetBudgetError(FleetLedgerError):
    pass


@dataclass(frozen=True)
class FleetBudget:
    asset: str
    approved_total: int

    def __post_init__(self) -> None:
        if not is_normalized_address(self.asset):
            raise FleetLedgerError("the asset must be a lowercase 0x address")
        if self.approved_total <= 0:
            raise FleetLedgerError("the approved total must be positive")


@dataclass(frozen=True)
class FleetMember:
    agent_identifier: str
    account: str
    funded: int
    retired: bool = False


class FleetLedger:
    def __init__(self, budget: FleetBudget) -> None:
        self._budget = budget
        self._members: dict[str, FleetMember] = {}

    @property
    def approved_total(self) -> int:
        return self._budget.approved_total

    @property
    def total_funded(self) -> int:
        return sum(member.funded for member in self._members.values())

    @property
    def remaining(self) -> int:
        return self.approved_total - self.total_funded

    @property
    def active(self) -> tuple[FleetMember, ...]:
        live = [member for member in self._members.values() if not member.retired]
        return tuple(sorted(live, key=lambda member: member.agent_identifier))

    def admit(self, member: FleetMember) -> None:
        self._require_admissible(member)
        if member.funded > self.remaining:
            raise FleetBudgetError(
                f"funding {member.agent_identifier} with {member.funded} would exceed "
                f"the approved total of {self.approved_total}"
            )
        self._members[member.agent_identifier] = member

    def retire(self, agent_identifier: str) -> None:
        member = self.member(agent_identifier)
        self._members[agent_identifier] = replace(member, retired=True)

    def record_sweep(self, agent_identifier: str, returned: int) -> None:
        member = self.member(agent_identifier)
        if not member.retired:
            raise FleetLedgerError("only a retired agent's funds can be swept back")
        if not 0 < returned <= member.funded:
            raise FleetLedgerError("the swept amount is outside what was funded")
        self._members[agent_identifier] = replace(
            member, funded=member.funded - returned
        )

    def member(self, agent_identifier: str) -> FleetMember:
        member = self._members.get(agent_identifier)
        if member is None:
            raise FleetLedgerError(f"{agent_identifier} is not in the fleet")
        return member

    def _require_admissible(self, member: FleetMember) -> None:
        require_agent_identifier(member.agent_identifier)
        if not is_normalized_address(member.account):
            raise FleetLedgerError("the account must be a lowercase 0x address")
        if member.funded <= 0:
            raise FleetLedgerError("a member must be funded with a positive amount")
        if member.retired:
            raise FleetLedgerError("a retired member cannot be admitted")
        if member.agent_identifier in self._members:
            raise FleetLedgerError(f"{member.agent_identifier} is already in the fleet")
        if any(known.account == member.account for known in self._members.values()):
            raise FleetLedgerError(f"{member.account} already belongs to another agent")
