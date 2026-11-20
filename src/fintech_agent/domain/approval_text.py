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
    lines += ["", "The agent can NEVER:", *_bullets(screen.never)]
    lines += ["", "Keep in mind:", *_bullets(screen.caveats)]
    if screen.warnings:
        lines += ["", "Please read before approving:"]
        lines += [f"  ! {warning.text}" for warning in screen.warnings]
    lines += ["", screen.control]
    lines += ["", f"Policy fingerprint: {screen.short_digest}", RULE]
    return "\n".join(lines)


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
