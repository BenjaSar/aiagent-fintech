from dataclasses import dataclass
from typing import Protocol

from fintech_agent.application.payee_registry import PayeeRegistry, PayeeRegistryError
from fintech_agent.domain.agent_definition import (
    AUTHORITY_FREE_TYPES,
    SCHEMA_VERSION,
    AgentDefinition,
    AgentDefinitionError,
    ModelChoice,
)
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.session_policy import AssetRule
from fintech_agent.domain.task_specification import TaskSpecification
from fintech_agent.domain.tool_catalog import (
    ToolCatalog,
    ToolCatalogError,
    authority_tools,
    carries_payment_authority,
)

MAXIMUM_COST_MICROS = 250_000
MAXIMUM_TOKENS = 200_000


class SynthesisError(ValueError):
    pass


class CostExceededError(SynthesisError):
    pass


@dataclass(frozen=True)
class GenerationCost:
    input_tokens: int
    output_tokens: int
    currency_micros: int

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.currency_micros) < 0:
            raise SynthesisError("a cost cannot be negative")


@dataclass(frozen=True)
class SynthesisLimits:
    maximum_currency_micros: int = MAXIMUM_COST_MICROS
    maximum_tokens: int = MAXIMUM_TOKENS


@dataclass(frozen=True)
class Proposal:
    name: str
    purpose: str
    system_prompt: str
    tools: tuple[str, ...]
    payees: tuple[str, ...]
    assets: tuple[AssetRule, ...]
    valid_after: int
    valid_until: int
    risk_tier: int
    model: ModelChoice
    cost: GenerationCost


@dataclass(frozen=True)
class SynthesisRequest:
    specification: TaskSpecification
    catalog: ToolCatalog
    registry: PayeeRegistry
    limits: SynthesisLimits


@dataclass(frozen=True)
class Candidate:
    definition: AgentDefinition
    definition_digest: str
    model: ModelChoice
    cost: GenerationCost


class CandidateProposer(Protocol):
    def propose(self, specification: TaskSpecification) -> Proposal: ...


def synthesize(request: SynthesisRequest, proposer: CandidateProposer) -> Candidate:
    proposal = proposer.propose(request.specification)
    _require_affordable(proposal.cost, request.limits)
    _require_pinned_model(proposal, request.specification)
    _require_allowed_tools(proposal, request)
    _require_no_authority_tool(proposal, request)
    _require_allowed_payees(proposal, request)
    _require_narrowed_limits(proposal, request.specification)
    definition = _definition_of(proposal, request.specification)
    return Candidate(
        definition, definition_digest(definition), proposal.model, proposal.cost
    )


def repeated_digests(
    request: SynthesisRequest, proposer: CandidateProposer, runs: int
) -> tuple[str, ...]:
    if runs <= 0:
        raise SynthesisError("ask for at least one run")
    return tuple(synthesize(request, proposer).definition_digest for _ in range(runs))


def _definition_of(
    proposal: Proposal, specification: TaskSpecification
) -> AgentDefinition:
    try:
        return AgentDefinition(
            schema_version=SCHEMA_VERSION,
            agent_type=specification.agent_type,
            name=proposal.name,
            purpose=proposal.purpose,
            model=specification.model,
            system_prompt=proposal.system_prompt,
            tools=proposal.tools,
            payees=proposal.payees,
            assets=proposal.assets,
            window_seconds=specification.window_seconds,
            valid_after=proposal.valid_after,
            valid_until=proposal.valid_until,
            budget=specification.budget,
            risk_tier=proposal.risk_tier,
            owner=specification.owner,
            provenance_identifier=specification.provenance_identifier,
        )
    except AgentDefinitionError as refusal:
        raise SynthesisError(
            f"the proposal is not a definition: {refusal}"
        ) from refusal


def _require_affordable(cost: GenerationCost, limits: SynthesisLimits) -> None:
    if cost.currency_micros > limits.maximum_currency_micros:
        raise CostExceededError(
            f"the generation cost {cost.currency_micros} is over the ceiling of "
            f"{limits.maximum_currency_micros}"
        )
    if cost.input_tokens + cost.output_tokens > limits.maximum_tokens:
        raise CostExceededError("the generation used more tokens than allowed")


def _require_pinned_model(proposal: Proposal, specification: TaskSpecification) -> None:
    if proposal.model != specification.model:
        raise SynthesisError("the proposal did not use the model the owner pinned")


def _require_allowed_tools(proposal: Proposal, request: SynthesisRequest) -> None:
    specification = request.specification
    outside = set(proposal.tools) - set(specification.tools)
    if outside:
        raise SynthesisError(f"{min(outside)} is not a tool this task allows")
    try:
        tools = request.catalog.resolve(proposal.tools)
    except ToolCatalogError as refusal:
        raise SynthesisError(str(refusal)) from refusal
    if specification.agent_type in AUTHORITY_FREE_TYPES:
        return
    if not carries_payment_authority(tools):
        raise SynthesisError("a payment agent needs a tool that can pay")


def _require_no_authority_tool(proposal: Proposal, request: SynthesisRequest) -> None:
    specification = request.specification
    if specification.agent_type not in AUTHORITY_FREE_TYPES:
        return
    held = authority_tools(request.catalog.resolve(proposal.tools))
    if held:
        raise SynthesisError(
            f"{held[0].identifier} can move value, which "
            f"{specification.agent_type.value} agents may not hold"
        )


def _require_allowed_payees(proposal: Proposal, request: SynthesisRequest) -> None:
    outside = set(proposal.payees) - set(request.specification.payees)
    if outside:
        raise SynthesisError(f"{min(outside)} is not a payee this task allows")
    _require_registered(proposal.payees, request.registry)


def _require_registered(payees: tuple[str, ...], registry: PayeeRegistry) -> None:
    try:
        registry.resolve(payees)
    except PayeeRegistryError as refusal:
        raise SynthesisError(str(refusal)) from refusal


def _require_narrowed_limits(
    proposal: Proposal, specification: TaskSpecification
) -> None:
    for rule in proposal.assets:
        _require_narrowed_cap(rule, specification)
    if proposal.valid_after < specification.valid_after:
        raise SynthesisError("the proposal starts before the task allows")
    if proposal.valid_until > specification.valid_until:
        raise SynthesisError("the proposal ends after the task allows")
    if proposal.risk_tier > specification.maximum_risk_tier:
        raise SynthesisError("the proposal claims a higher risk tier than allowed")


def _require_narrowed_cap(rule: AssetRule, specification: TaskSpecification) -> None:
    ceiling = specification.cap_of(rule.asset)
    if ceiling is None:
        raise SynthesisError(f"{rule.asset} has no ceiling in this task")
    if rule.cap > ceiling:
        raise SynthesisError(
            f"the cap for {rule.asset} is above the ceiling of {ceiling}"
        )
