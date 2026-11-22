from .agent_approval import NO_AUTHORITY, AgentApprovalScreen
from .policy_approval import ApprovalScreen

RULE = "-" * 64


def render_text(screen: ApprovalScreen) -> str:
    lines = [
        "APPROVE AN AGENT SESSION",
        RULE,
        f"Account:  {screen.policy.account}",
        f"Starts:   {screen.starts_at}",
        f"Ends:     {screen.ends_at}  ({screen.session_length})",
        f"Spending is counted over a rolling {screen.window_length}.",
        "",
        "The agent MAY:",
        *_bullets(screen.permitted),
        "",
        "Spending limits:",
        *_limits(screen),
        f"  Most it may pay in gas per operation: {screen.gas_cap_text}",
    ]
    if screen.sponsors:
        lines.append("  Gas may be sponsored by: " + ", ".join(screen.sponsors))
    if screen.payee_lines:
        lines += ["", "It may pay only these addresses:", *_payees(screen)]
    lines += ["", "The agent can NEVER:", *_bullets(screen.never)]
    lines += ["", "Keep in mind:", *_bullets(screen.caveats)]
    if screen.warnings:
        lines += ["", "Please read before approving:"]
        lines += [f"  ! {warning.text}" for warning in screen.warnings]
    lines += ["", screen.control]
    lines += ["", f"Policy fingerprint: {screen.short_digest}", RULE]
    return "\n".join(lines)


def _payees(screen: ApprovalScreen) -> list[str]:
    return [
        f"  {payee.label or '(unlabelled)'}  {payee.address}"
        for payee in screen.payee_lines
    ]


def _bullets(statements: tuple[str, ...]) -> list[str]:
    return [f"  - {statement}" for statement in statements]


def _limits(screen: ApprovalScreen) -> list[str]:
    if not screen.asset_lines:
        return ["  (none)"]
    return [
        f"  {line.cap_text} per window, up to {line.session_maximum_text} over the "
        "whole session"
        for line in screen.asset_lines
    ]


def render_agent_text(screen: AgentApprovalScreen) -> str:
    definition = screen.definition
    lines = [
        "APPROVE A NEW AGENT",
        RULE,
        f"Name:      {definition.name}",
        f"Purpose:   {definition.purpose}",
        f"Model:     {screen.model_text}",
        f"Risk tier: {definition.risk_tier}",
        f"Budget:    {_budget_line(screen)}",
        f"Provenance: {definition.provenance_identifier}",
        "",
        "It is given these tools and no others:",
        *_tools(screen),
        "",
        "Its instructions:",
        *_instructions(definition.system_prompt),
        f"  ! {screen.instruction_caveat}",
        "",
        f"Agent fingerprint: {screen.short_fingerprint}",
        RULE,
        "",
        *_authority(screen),
    ]
    return "\n".join(lines)


def _budget_line(screen: AgentApprovalScreen) -> str:
    if not screen.holds_authority:
        return screen.budget_text
    return f"{screen.budget_text} funded into its own account"


def _authority(screen: AgentApprovalScreen) -> list[str]:
    policy_screen = screen.policy_screen
    if policy_screen is None:
        return ["THIS AGENT HOLDS NO AUTHORITY", RULE, f"  ! {NO_AUTHORITY}", RULE]
    return [render_text(policy_screen)]


def _tools(screen: AgentApprovalScreen) -> list[str]:
    return [
        f"  {line.identifier} ({line.kind}) - {line.summary}"
        for line in screen.tool_lines
    ]


def _instructions(prompt: str) -> list[str]:
    return [f"  {line}" for line in prompt.splitlines() or [""]]
