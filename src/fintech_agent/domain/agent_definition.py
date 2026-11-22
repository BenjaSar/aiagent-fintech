import re
from dataclasses import dataclass
from enum import Enum

from .plain_text import is_plain_text
from .session_policy import AssetRule, is_normalized_address

SCHEMA_VERSION = 1
AGENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9 ._-]{1,63}")
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{1,63}")
MODEL_IDENTIFIER = re.compile(r"[a-z][a-z0-9.\-]{1,63}")
MAXIMUM_PURPOSE = 256
MAXIMUM_SYSTEM_PROMPT = 4096
MAXIMUM_TEMPERATURE_HUNDREDTHS = 200
MAXIMUM_OUTPUT_TOKENS = 32_768
MAXIMUM_ASSETS = 8
MAXIMUM_PAYEES = 32
MAXIMUM_TOOLS = 16
LOWEST_RISK_TIER = 1
HIGHEST_RISK_TIER = 3


class AgentDefinitionError(ValueError):
    pass


class AgentType(Enum):
    PAYMENT = "payment"
    ANALYSIS = "analysis"


AUTHORITY_FREE_TYPES = frozenset({AgentType.ANALYSIS})


@dataclass(frozen=True)
class ModelChoice:
    identifier: str
    temperature_hundredths: int
    maximum_output_tokens: int

    def __post_init__(self) -> None:
        if not MODEL_IDENTIFIER.fullmatch(self.identifier):
            raise AgentDefinitionError("the model identifier is malformed")
        if not 0 <= self.temperature_hundredths <= MAXIMUM_TEMPERATURE_HUNDREDTHS:
            raise AgentDefinitionError("the temperature is out of range")
        if not 0 < self.maximum_output_tokens <= MAXIMUM_OUTPUT_TOKENS:
            raise AgentDefinitionError("the output token limit is out of range")


@dataclass(frozen=True)
class Budget:
    asset: str
    total: int

    def __post_init__(self) -> None:
        if not is_normalized_address(self.asset):
            raise AgentDefinitionError("the budget asset must be a lowercase address")
        if self.total <= 0:
            raise AgentDefinitionError("the budget must be positive")


@dataclass(frozen=True)
class AgentDefinition:
    schema_version: int
    agent_type: AgentType
    name: str
    purpose: str
    model: ModelChoice
    system_prompt: str
    tools: tuple[str, ...]
    payees: tuple[str, ...]
    assets: tuple[AssetRule, ...]
    window_seconds: int
    valid_after: int
    valid_until: int
    budget: Budget | None
    risk_tier: int
    owner: str
    provenance_identifier: str

    def __post_init__(self) -> None:
        self._check_identity()
        self._check_text()
        self._check_capabilities()
        self._check_limits()

    @property
    def declared_assets(self) -> frozenset[str]:
        return frozenset(rule.asset for rule in self.assets)

    def _check_identity(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise AgentDefinitionError(
                f"this build understands schema version {SCHEMA_VERSION} only"
            )
        if not AGENT_NAME.fullmatch(self.name):
            raise AgentDefinitionError("the name must be short plain ASCII text")
        if not is_normalized_address(self.owner):
            raise AgentDefinitionError("the owner must be a lowercase address")
        if not IDENTIFIER.fullmatch(self.provenance_identifier):
            raise AgentDefinitionError("the provenance identifier is malformed")

    def _check_text(self) -> None:
        if not 0 < len(self.purpose) <= MAXIMUM_PURPOSE:
            raise AgentDefinitionError("the purpose must be short text")
        if not 0 < len(self.system_prompt) <= MAXIMUM_SYSTEM_PROMPT:
            raise AgentDefinitionError("the system prompt must be bounded text")
        if not is_plain_text(self.purpose) or not is_plain_text(self.system_prompt):
            raise AgentDefinitionError(
                "the purpose and the system prompt must be plain text"
            )

    def _check_capabilities(self) -> None:
        if not 0 < len(self.tools) <= MAXIMUM_TOOLS:
            raise AgentDefinitionError("an agent needs a few named tools")
        if len(set(self.tools)) != len(self.tools):
            raise AgentDefinitionError("the tools must be named once each")
        if len(self.payees) > MAXIMUM_PAYEES:
            raise AgentDefinitionError("there are too many payees")
        if len(set(self.payees)) != len(self.payees):
            raise AgentDefinitionError("the payees must be named once each")
        if not all(IDENTIFIER.fullmatch(payee) for payee in self.payees):
            raise AgentDefinitionError("a payee is named by its registry identifier")

    def _check_limits(self) -> None:
        if self.window_seconds <= 0 or self.valid_until <= self.valid_after:
            raise AgentDefinitionError("the window and the session must be non-empty")
        if not LOWEST_RISK_TIER <= self.risk_tier <= HIGHEST_RISK_TIER:
            raise AgentDefinitionError("the risk tier is out of range")
        if self.agent_type in AUTHORITY_FREE_TYPES:
            self._check_without_authority()
            return
        self._check_with_authority()

    def _check_without_authority(self) -> None:
        if self.payees or self.assets or self.budget is not None:
            raise AgentDefinitionError(
                f"{self.agent_type.value} agents declare no payee, asset or budget"
            )

    def _check_with_authority(self) -> None:
        budget = self.budget
        if budget is None:
            raise AgentDefinitionError("an agent that can move value needs a budget")
        if not 0 < len(self.assets) <= MAXIMUM_ASSETS:
            raise AgentDefinitionError("an agent needs between one and eight assets")
        if len(self.declared_assets) != len(self.assets):
            raise AgentDefinitionError("each asset carries one cap")
        if any(rule.cap <= 0 for rule in self.assets):
            raise AgentDefinitionError("every cap must be positive")
        if not all(is_normalized_address(rule.asset) for rule in self.assets):
            raise AgentDefinitionError("every asset must be a lowercase address")
        if budget.asset not in self.declared_assets:
            raise AgentDefinitionError(
                "the budget names an asset the agent cannot move"
            )
