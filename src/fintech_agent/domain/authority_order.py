from dataclasses import dataclass, replace

from .agent_definition import AgentDefinition
from .tool_catalog import AUTHORITY_KINDS, ToolCatalog, ToolCatalogError


class AuthorityOrderError(ValueError):
    pass


class AuthorityWidenedError(AuthorityOrderError):
    pass


@dataclass(frozen=True)
class AuthorityChange:
    previous: AgentDefinition
    following: AgentDefinition
    catalog: ToolCatalog


def widenings(change: AuthorityChange) -> tuple[str, ...]:
    return (
        *_added_payees(change),
        *_added_assets(change),
        *_raised_caps(change),
        *_added_paying_tools(change),
        *_longer_reach(change),
        *_larger_budget(change),
        *_changed_owner(change),
    )


def is_renewal(change: AuthorityChange) -> bool:
    previous, following = change.previous, change.following
    if widenings(change):
        return False
    if following.valid_after < previous.valid_after:
        return False
    unchanged = replace(
        following,
        valid_after=previous.valid_after,
        valid_until=previous.valid_until,
    )
    return unchanged == previous


def require_renewal(change: AuthorityChange) -> None:
    widened = widenings(change)
    if widened:
        raise AuthorityWidenedError(
            "a renewal cannot widen authority: " + "; ".join(widened)
        )
    if not is_renewal(change):
        raise AuthorityOrderError(
            "a renewal carries the same authority with a later window only"
        )


def _added_payees(change: AuthorityChange) -> tuple[str, ...]:
    added = set(change.following.payees) - set(change.previous.payees)
    return tuple(
        f"{payee} is a payee the previous authority did not hold"
        for payee in sorted(added)
    )


def _added_assets(change: AuthorityChange) -> tuple[str, ...]:
    added = change.following.declared_assets - change.previous.declared_assets
    return tuple(
        f"{asset} is an asset the previous authority did not hold"
        for asset in sorted(added)
    )


def _raised_caps(change: AuthorityChange) -> tuple[str, ...]:
    before = _caps_of(change.previous)
    return tuple(
        f"the cap for {asset} rises from {before[asset]} to {cap}"
        for asset, cap in sorted(_caps_of(change.following).items())
        if asset in before and cap > before[asset]
    )


def _caps_of(definition: AgentDefinition) -> dict[str, int]:
    return {rule.asset: rule.cap for rule in definition.assets}


def _added_paying_tools(change: AuthorityChange) -> tuple[str, ...]:
    catalog = change.catalog
    added = _paying_tools(change.following, catalog) - _paying_tools(
        change.previous, catalog
    )
    return tuple(
        f"{tool} is a paying tool the previous authority did not hold"
        for tool in sorted(added)
    )


def _paying_tools(definition: AgentDefinition, catalog: ToolCatalog) -> frozenset[str]:
    try:
        resolved = catalog.resolve(definition.tools)
    except ToolCatalogError as refusal:
        raise AuthorityOrderError(str(refusal)) from refusal
    return frozenset(
        tool.identifier for tool in resolved if tool.kind in AUTHORITY_KINDS
    )


def _longer_reach(change: AuthorityChange) -> tuple[str, ...]:
    previous, following = change.previous, change.following
    reasons = list(_changed_window(change))
    before = previous.valid_until - previous.valid_after
    after = following.valid_until - following.valid_after
    if after > before:
        reasons.append(f"the session runs {after} seconds instead of {before}")
    if following.risk_tier > previous.risk_tier:
        reasons.append(
            f"the risk tier rises from {previous.risk_tier} to {following.risk_tier}"
        )
    return tuple(reasons)


def _changed_window(change: AuthorityChange) -> tuple[str, ...]:
    before = change.previous.window_seconds
    after = change.following.window_seconds
    if before == after:
        return ()
    return (f"the rolling window changes from {before} to {after} seconds",)


def _larger_budget(change: AuthorityChange) -> tuple[str, ...]:
    previous, following = change.previous.budget, change.following.budget
    if following is None:
        return ()
    if previous is None:
        return (f"it gains a budget of {following.total} {following.asset}",)
    if following.asset != previous.asset:
        return (f"the budget moves from {previous.asset} to {following.asset}",)
    if following.total > previous.total:
        return (f"the budget rises from {previous.total} to {following.total}",)
    return ()


def _changed_owner(change: AuthorityChange) -> tuple[str, ...]:
    previous, following = change.previous.owner, change.following.owner
    if previous == following:
        return ()
    return (f"the owner changes from {previous} to {following}",)
