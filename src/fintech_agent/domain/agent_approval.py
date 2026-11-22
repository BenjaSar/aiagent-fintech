import hashlib
from dataclasses import dataclass

from .agent_definition import AUTHORITY_FREE_TYPES, AgentDefinition
from .agent_definition_document import definition_digest
from .approval_display import DisplayCatalog, display_of, format_units
from .policy_approval import (
    ApprovalLimits,
    ApprovalScreen,
    PolicyPresentation,
    describe_policy,
    policy_digest,
)
from .session_policy import SessionPolicy
from .tool_catalog import CatalogTool, ToolCatalog

FINGERPRINT_BYTES = 16
FINGERPRINT_GROUP = 8
HUNDRED = 100
INSTRUCTION_CAVEAT = (
    "The instructions above are text this agent is given. They grant it nothing: the "
    "limits below are the whole of what it may do."
)
NO_BUDGET_TEXT = "none - this agent holds no account and can move no value"
NO_AUTHORITY = (
    "This agent holds no authority. It has no account, no session key and no "
    "spending policy, and approving it grants nothing that can move value. It "
    "can only read, through the tools listed above."
)


class AgentApprovalError(Exception):
    pass


@dataclass(frozen=True)
class ApprovalBinding:
    definition_digest: str
    policy_digest: str
    group_public_key: bytes


@dataclass(frozen=True)
class AgentPresentation:
    definition: AgentDefinition
    policy: SessionPolicy | None
    group_public_key: bytes
    tool_catalog: ToolCatalog
    display_catalog: DisplayCatalog
    limits: ApprovalLimits
    now: int


@dataclass(frozen=True)
class ToolLine:
    identifier: str
    kind: str
    summary: str


@dataclass(frozen=True)
class AgentApprovalScreen:
    definition: AgentDefinition
    model_text: str
    budget_text: str
    instruction_caveat: str
    tool_lines: tuple[ToolLine, ...]
    binding: ApprovalBinding
    fingerprint: str
    policy_screen: ApprovalScreen | None

    @property
    def required_acknowledgements(self) -> frozenset[str]:
        if self.policy_screen is None:
            return frozenset()
        return self.policy_screen.required_acknowledgements

    @property
    def holds_authority(self) -> bool:
        return self.policy_screen is not None

    @property
    def short_fingerprint(self) -> str:
        groups = [
            self.fingerprint[start : start + FINGERPRINT_GROUP]
            for start in range(0, len(self.fingerprint), FINGERPRINT_GROUP)
        ]
        return " ".join(groups)


@dataclass(frozen=True)
class AgentAcknowledgement:
    fingerprint: str
    warning_keys: frozenset[str]
    approver: str
    approved_at: int


@dataclass(frozen=True)
class ApprovedAgent:
    definition: AgentDefinition
    policy: SessionPolicy | None
    binding: ApprovalBinding
    fingerprint: str
    approver: str
    approved_at: int


def agent_fingerprint(binding: ApprovalBinding) -> str:
    material = (
        bytes.fromhex(binding.definition_digest)
        + bytes.fromhex(binding.policy_digest)
        + binding.group_public_key
    )
    return hashlib.sha256(material).digest()[:FINGERPRINT_BYTES].hex()


def describe_agent(presentation: AgentPresentation) -> AgentApprovalScreen:
    definition = presentation.definition
    if definition.agent_type in AUTHORITY_FREE_TYPES:
        return _reader_screen(presentation)
    _require_session_key(presentation)
    policy = _require_policy(presentation)
    _require_the_policy_belongs_to_the_definition(definition, policy)
    binding = ApprovalBinding(
        definition_digest(definition),
        policy_digest(policy),
        presentation.group_public_key,
    )
    return AgentApprovalScreen(
        definition=definition,
        model_text=_model_text(definition),
        budget_text=_budget_text(presentation),
        instruction_caveat=INSTRUCTION_CAVEAT,
        tool_lines=_tool_lines(presentation),
        binding=binding,
        fingerprint=agent_fingerprint(binding),
        policy_screen=describe_policy(_policy_presentation(presentation, policy)),
    )


def _reader_screen(presentation: AgentPresentation) -> AgentApprovalScreen:
    definition = presentation.definition
    if presentation.policy is not None or presentation.group_public_key:
        raise AgentApprovalError(
            f"{definition.agent_type.value} agents are approved without a policy "
            "or a session key"
        )
    binding = ApprovalBinding(definition_digest(definition), "", b"")
    return AgentApprovalScreen(
        definition=definition,
        model_text=_model_text(definition),
        budget_text=NO_BUDGET_TEXT,
        instruction_caveat=INSTRUCTION_CAVEAT,
        tool_lines=_tool_lines(presentation),
        binding=binding,
        fingerprint=agent_fingerprint(binding),
        policy_screen=None,
    )


def approve_agent(
    screen: AgentApprovalScreen, acknowledgement: AgentAcknowledgement
) -> ApprovedAgent:
    if acknowledgement.fingerprint != screen.fingerprint:
        raise AgentApprovalError(
            "the approval is for another agent, policy or session key"
        )
    if acknowledgement.warning_keys != screen.required_acknowledgements:
        raise AgentApprovalError("every warning must be acknowledged, and only those")
    if acknowledgement.approver != screen.definition.owner:
        raise AgentApprovalError(
            "only the owner named in the definition can approve this agent"
        )
    if acknowledgement.approved_at <= 0:
        raise AgentApprovalError("an approval needs the time it was given")
    return ApprovedAgent(
        screen.definition,
        None if screen.policy_screen is None else screen.policy_screen.policy,
        screen.binding,
        screen.fingerprint,
        acknowledgement.approver,
        acknowledgement.approved_at,
    )


def _require_session_key(presentation: AgentPresentation) -> None:
    if not presentation.group_public_key:
        raise AgentApprovalError("the session key is missing")


def _require_policy(presentation: AgentPresentation) -> SessionPolicy:
    policy = presentation.policy
    if policy is None:
        raise AgentApprovalError("an agent that can move value needs a policy")
    return policy


def _require_the_policy_belongs_to_the_definition(
    definition: AgentDefinition, policy: SessionPolicy
) -> None:
    if policy.rules != definition.assets:
        raise AgentApprovalError("the policy's caps are not the definition's")
    if policy.window_seconds != definition.window_seconds:
        raise AgentApprovalError("the policy's window is not the definition's")
    if policy.valid_until != definition.valid_until:
        raise AgentApprovalError("the policy's end time is not the definition's")
    if not policy.payments_only:
        raise AgentApprovalError("a stage one agent needs a payments-only policy")


def _policy_presentation(
    presentation: AgentPresentation, policy: SessionPolicy
) -> PolicyPresentation:
    return PolicyPresentation(
        policy,
        presentation.display_catalog,
        presentation.limits,
        presentation.now,
    )


def _model_text(definition: AgentDefinition) -> str:
    model = definition.model
    whole, hundredths = divmod(model.temperature_hundredths, HUNDRED)
    return (
        f"{model.identifier}, temperature {whole}.{hundredths:02d}, up to "
        f"{model.maximum_output_tokens} output tokens"
    )


def _budget_text(presentation: AgentPresentation) -> str:
    budget = presentation.definition.budget
    if budget is None:
        return NO_BUDGET_TEXT
    display = display_of(presentation.display_catalog, budget.asset)
    if display is None:
        return f"{budget.total} raw units of {budget.asset}"
    return f"{format_units(budget.total, display.decimals)} {display.symbol}"


def _tool_lines(presentation: AgentPresentation) -> tuple[ToolLine, ...]:
    tools = presentation.tool_catalog.resolve(presentation.definition.tools)
    return tuple(_tool_line(tool) for tool in tools)


def _tool_line(tool: CatalogTool) -> ToolLine:
    return ToolLine(tool.identifier, tool.kind.value, tool.summary)
