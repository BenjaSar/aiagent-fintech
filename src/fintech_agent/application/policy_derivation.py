from dataclasses import dataclass

from fintech_agent.application.payee_registry import (
    ApprovedPayee,
    PayeeRegistry,
    PayeeRegistryError,
)
from fintech_agent.domain.agent_definition import (
    AUTHORITY_FREE_TYPES,
    AgentDefinition,
    AgentType,
)
from fintech_agent.domain.session_policy import SessionPolicy
from fintech_agent.domain.tool_catalog import (
    CatalogTool,
    ToolCatalog,
    ToolCatalogError,
    carries_payment_authority,
)
from fintech_agent.domain.user_operation_hash import HashingDomain


class DerivationError(ValueError):
    pass


@dataclass(frozen=True)
class DeploymentParameters:
    domain: HashingDomain
    account: str
    validator: str
    hook: str
    allowed_paymasters: frozenset[str]
    maximum_gas_cost: int
    window_seconds: int
    clock_skew_seconds: int


@dataclass(frozen=True)
class DerivationRequest:
    definition: AgentDefinition
    deployment: DeploymentParameters
    catalog: ToolCatalog
    registry: PayeeRegistry


def derive_session_policy(request: DerivationRequest) -> SessionPolicy:
    definition = request.definition
    deployment = request.deployment
    payees = _resolved_payees(request)
    addresses = frozenset(payee.address for payee in payees)
    _require_payment_agent(request, payees)
    _require_window(request)
    _require_distinct_roles(definition, addresses)
    return SessionPolicy(
        domain=deployment.domain,
        account=deployment.account,
        validator=deployment.validator,
        hook=deployment.hook,
        allowed_targets=addresses,
        rules=definition.assets,
        allowed_paymasters=deployment.allowed_paymasters,
        allowed_payees=addresses,
        valid_after=max(0, definition.valid_after - deployment.clock_skew_seconds),
        valid_until=definition.valid_until,
        window_seconds=deployment.window_seconds,
        maximum_gas_cost=deployment.maximum_gas_cost,
        payments_only=True,
    )


def _resolved_payees(request: DerivationRequest) -> tuple[ApprovedPayee, ...]:
    try:
        return request.registry.resolve(request.definition.payees)
    except PayeeRegistryError as failure:
        raise DerivationError(str(failure)) from failure


def _require_payment_agent(
    request: DerivationRequest, payees: tuple[ApprovedPayee, ...]
) -> None:
    definition = request.definition
    if definition.agent_type in AUTHORITY_FREE_TYPES:
        raise DerivationError(
            f"{definition.agent_type.value} agents hold no authority, so no "
            "session policy is derived for them"
        )
    if definition.agent_type is not AgentType.PAYMENT:
        raise DerivationError("only payment agents derive a session policy")
    if not carries_payment_authority(_resolved_tools(request)):
        raise DerivationError("a payment agent needs a tool that can pay")
    if not payees:
        raise DerivationError("a payment agent needs at least one approved payee")


def _resolved_tools(request: DerivationRequest) -> tuple[CatalogTool, ...]:
    try:
        return request.catalog.resolve(request.definition.tools)
    except ToolCatalogError as failure:
        raise DerivationError(str(failure)) from failure


def _require_window(request: DerivationRequest) -> None:
    definition, deployment = request.definition, request.deployment
    if definition.window_seconds != deployment.window_seconds:
        raise DerivationError(
            f"the hook counts windows of {deployment.window_seconds} seconds, "
            f"not {definition.window_seconds}"
        )


def _require_distinct_roles(
    definition: AgentDefinition, addresses: frozenset[str]
) -> None:
    both = addresses & definition.declared_assets
    if both:
        raise DerivationError(f"{min(both)} cannot be both a payee and a capped asset")
