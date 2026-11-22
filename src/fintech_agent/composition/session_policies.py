from dataclasses import dataclass

from fintech_agent.composition.drill_toolbox import (
    ENTRY_POINT,
    ZERO_ADDRESS,
    DrillToolbox,
)
from fintech_agent.domain.session_policy import AssetRule, SessionPolicy
from fintech_agent.domain.user_operation_hash import HashingDomain

WINDOW_SECONDS = 86_400
MAXIMUM_GAS_COST = 10**17


@dataclass(frozen=True)
class PolicyParts:
    account: str
    native_cap: int
    valid_until: int


def build_session_policy(toolbox: DrillToolbox, parts: PolicyParts) -> SessionPolicy:
    contracts = toolbox.contracts
    return SessionPolicy(
        domain=HashingDomain(toolbox.chain_id(), ENTRY_POINT.lower()),
        account=parts.account.lower(),
        validator=contracts.validator.lower(),
        hook=contracts.policy_hook.lower(),
        allowed_targets=frozenset({contracts.target.lower()}),
        rules=(AssetRule(ZERO_ADDRESS, parts.native_cap),),
        allowed_paymasters=frozenset({contracts.paymaster.lower()}),
        allowed_payees=frozenset(),
        valid_after=0,
        valid_until=parts.valid_until,
        window_seconds=WINDOW_SECONDS,
        maximum_gas_cost=MAXIMUM_GAS_COST,
        payments_only=False,
    )
