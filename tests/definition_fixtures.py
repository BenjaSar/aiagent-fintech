from dataclasses import replace
from typing import Any

from fintech_agent.application.payee_registry import ApprovedPayee, PayeeRegistry
from fintech_agent.application.policy_derivation import (
    DeploymentParameters,
    DerivationRequest,
)
from fintech_agent.domain.agent_definition import (
    SCHEMA_VERSION,
    AgentDefinition,
    AgentType,
    Budget,
    ModelChoice,
)
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from fintech_agent.domain.user_operation_hash import HashingDomain
from tests.policy_fixtures import (
    ACCOUNT,
    ENTRY_POINT,
    ETHER,
    HOOK,
    NOW,
    PAYMASTER,
    TARGET,
    VALIDATOR,
)

OWNER = "0x" + "0a" * 20
PAYEE = "vault"
PAYEE_LABEL = "DeFi Yield Vault"
APPROVED_AT = NOW - 10_000
WINDOW_SECONDS = 86_400
CLOCK_SKEW_SECONDS = 300
MAXIMUM_GAS_COST = 10**17
SESSION_SECONDS = 86_400


def build_definition(**overrides: Any) -> AgentDefinition:
    definition = AgentDefinition(
        schema_version=SCHEMA_VERSION,
        agent_type=AgentType.PAYMENT,
        name="Supplier Payments",
        purpose="Pay approved suppliers from the operating budget.",
        model=ModelChoice("claude-sonnet-5-5", 0, 1024),
        system_prompt="Pay only approved payees and never exceed a cap.",
        tools=("list_payees", "get_balance", "get_payment_status", "pay_payee"),
        payees=(PAYEE,),
        assets=(AssetRule(NATIVE_ASSET, ETHER),),
        window_seconds=WINDOW_SECONDS,
        valid_after=NOW,
        valid_until=NOW + SESSION_SECONDS,
        budget=Budget(NATIVE_ASSET, ETHER // 2),
        risk_tier=1,
        owner=OWNER,
        provenance_identifier="generation-1",
    )
    return replace(definition, **overrides) if overrides else definition


def build_reader_definition(**overrides: Any) -> AgentDefinition:
    definition = AgentDefinition(
        schema_version=SCHEMA_VERSION,
        agent_type=AgentType.ANALYSIS,
        name="Payment Reports",
        purpose="Report on the payments the fleet has made.",
        model=ModelChoice("claude-sonnet-5-5", 0, 1024),
        system_prompt="Read only what your tools give you and report it plainly.",
        tools=("list_payees", "get_payment_status"),
        payees=(),
        assets=(),
        window_seconds=WINDOW_SECONDS,
        valid_after=NOW,
        valid_until=NOW + SESSION_SECONDS,
        budget=None,
        risk_tier=1,
        owner=OWNER,
        provenance_identifier="generation-1",
    )
    return replace(definition, **overrides) if overrides else definition


def build_registry() -> PayeeRegistry:
    registry = PayeeRegistry()
    registry.approve(ApprovedPayee(PAYEE, TARGET, PAYEE_LABEL, OWNER, APPROVED_AT))
    return registry


def build_deployment(**overrides: Any) -> DeploymentParameters:
    deployment = DeploymentParameters(
        domain=HashingDomain(31337, ENTRY_POINT),
        account=ACCOUNT,
        validator=VALIDATOR,
        hook=HOOK,
        allowed_paymasters=frozenset({PAYMASTER}),
        maximum_gas_cost=MAXIMUM_GAS_COST,
        window_seconds=WINDOW_SECONDS,
        clock_skew_seconds=CLOCK_SKEW_SECONDS,
    )
    return replace(deployment, **overrides) if overrides else deployment


def build_request(
    definition: AgentDefinition | None = None,
    deployment: DeploymentParameters | None = None,
    registry: PayeeRegistry | None = None,
) -> DerivationRequest:
    return DerivationRequest(
        definition or build_definition(),
        deployment or build_deployment(),
        STAGE_ONE_PAYMENT_CATALOG,
        registry or build_registry(),
    )
