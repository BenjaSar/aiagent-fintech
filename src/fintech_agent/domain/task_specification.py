from dataclasses import dataclass

from .agent_definition import (
    AUTHORITY_FREE_TYPES,
    HIGHEST_RISK_TIER,
    IDENTIFIER,
    LOWEST_RISK_TIER,
    MAXIMUM_ASSETS,
    MAXIMUM_PAYEES,
    MAXIMUM_PURPOSE,
    MAXIMUM_TOOLS,
    AgentType,
    Budget,
    ModelChoice,
)
from .plain_text import is_plain_text
from .session_policy import AssetRule, is_normalized_address


class TaskSpecificationError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSpecification:
    identifier: str
    agent_type: AgentType
    purpose: str
    model: ModelChoice
    tools: tuple[str, ...]
    payees: tuple[str, ...]
    assets: tuple[AssetRule, ...]
    window_seconds: int
    valid_after: int
    valid_until: int
    budget: Budget | None
    maximum_risk_tier: int
    owner: str
    provenance_identifier: str

    def __post_init__(self) -> None:
        self._check_identity()
        self._check_capabilities()
        self._check_limits()

    @property
    def declared_assets(self) -> frozenset[str]:
        return frozenset(rule.asset for rule in self.assets)

    def cap_of(self, asset: str) -> int | None:
        for rule in self.assets:
            if rule.asset == asset:
                return rule.cap
        return None

    def _check_identity(self) -> None:
        if not IDENTIFIER.fullmatch(self.identifier):
            raise TaskSpecificationError("the identifier is malformed")
        if not IDENTIFIER.fullmatch(self.provenance_identifier):
            raise TaskSpecificationError("the provenance identifier is malformed")
        if not is_normalized_address(self.owner):
            raise TaskSpecificationError("the owner must be a lowercase address")
        if not 0 < len(self.purpose) <= MAXIMUM_PURPOSE:
            raise TaskSpecificationError("the purpose must be short text")
        if not is_plain_text(self.purpose):
            raise TaskSpecificationError("the purpose must be plain text")

    def _check_capabilities(self) -> None:
        if not 0 < len(self.tools) <= MAXIMUM_TOOLS:
            raise TaskSpecificationError("name a few tools the agent may be given")
        if len(set(self.tools)) != len(self.tools):
            raise TaskSpecificationError("name each tool once")
        if len(set(self.payees)) != len(self.payees):
            raise TaskSpecificationError("name each payee once")
        if not all(IDENTIFIER.fullmatch(payee) for payee in self.payees):
            raise TaskSpecificationError("a payee is named by its registry identifier")
        if self.agent_type in AUTHORITY_FREE_TYPES:
            return
        if not 0 < len(self.payees) <= MAXIMUM_PAYEES:
            raise TaskSpecificationError("name a few payees the agent may pay")

    def _check_limits(self) -> None:
        if self.window_seconds <= 0 or self.valid_until <= self.valid_after:
            raise TaskSpecificationError("the window and the session must be non-empty")
        if not LOWEST_RISK_TIER <= self.maximum_risk_tier <= HIGHEST_RISK_TIER:
            raise TaskSpecificationError("the highest risk tier is out of range")
        if self.agent_type in AUTHORITY_FREE_TYPES:
            self._check_without_authority()
            return
        self._check_with_authority()

    def _check_without_authority(self) -> None:
        if self.payees or self.assets or self.budget is not None:
            raise TaskSpecificationError(
                f"{self.agent_type.value} tasks set no payee, ceiling or budget"
            )

    def _check_with_authority(self) -> None:
        budget = self.budget
        if budget is None:
            raise TaskSpecificationError("a task that can move value needs a budget")
        if not 0 < len(self.assets) <= MAXIMUM_ASSETS:
            raise TaskSpecificationError("set a ceiling for one to eight assets")
        if len(self.declared_assets) != len(self.assets):
            raise TaskSpecificationError("each asset carries one ceiling")
        if any(rule.cap <= 0 for rule in self.assets):
            raise TaskSpecificationError("every ceiling must be positive")
        if not all(is_normalized_address(rule.asset) for rule in self.assets):
            raise TaskSpecificationError("every asset must be a lowercase address")
        if budget.asset not in self.declared_assets:
            raise TaskSpecificationError("the budget names an asset without a ceiling")
