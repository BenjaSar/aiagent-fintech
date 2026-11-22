from dataclasses import dataclass
from enum import Enum

from .session_policy import AssetRule, SessionPolicy

NO_CAP = 0


class TransitionError(ValueError):
    pass


class HookChangeKind(Enum):
    ALLOW_TARGET = "allowTarget(address)"
    REVOKE_TARGET = "revokeTarget(address)"
    SET_CAP = "setCap(address,uint256)"
    ALLOW_PAYEE = "allowPayee(address)"
    REVOKE_PAYEE = "revokePayee(address)"


@dataclass(frozen=True)
class HookChange:
    kind: HookChangeKind
    subject: str
    cap: int = NO_CAP


@dataclass(frozen=True)
class PolicyTransition:
    previous: SessionPolicy
    following: SessionPolicy


def hook_changes(transition: PolicyTransition) -> tuple[HookChange, ...]:
    previous, following = transition.previous, transition.following
    _require_same_anchor(transition)
    removed = sorted(previous.allowed_targets - following.allowed_targets)
    added = sorted(following.allowed_targets - previous.allowed_targets)
    caps = _cap_changes(previous.rules, following.rules)
    return (
        *(HookChange(HookChangeKind.REVOKE_TARGET, target) for target in removed),
        *(change for change in caps if change.cap == NO_CAP),
        *(change for change in caps if change.cap != NO_CAP),
        *(HookChange(HookChangeKind.ALLOW_TARGET, target) for target in added),
        *_payee_changes(transition),
    )


def _require_same_anchor(transition: PolicyTransition) -> None:
    previous, following = transition.previous, transition.following
    if (
        previous.domain != following.domain
        or previous.account != following.account
        or previous.validator != following.validator
        or previous.hook != following.hook
    ):
        raise TransitionError(
            "a rotation keeps the same chain, account, validator and hook"
        )
    if previous.window_seconds != following.window_seconds:
        raise TransitionError("the rolling window is fixed by the hook")
    if previous.payments_only != following.payments_only:
        raise TransitionError(
            "a rotation cannot turn a payments-only policy into another kind"
        )


def _payee_changes(transition: PolicyTransition) -> tuple[HookChange, ...]:
    previous, following = transition.previous, transition.following
    removed = sorted(previous.allowed_payees - following.allowed_payees)
    added = sorted(following.allowed_payees - previous.allowed_payees)
    return (
        *(HookChange(HookChangeKind.REVOKE_PAYEE, payee) for payee in removed),
        *(HookChange(HookChangeKind.ALLOW_PAYEE, payee) for payee in added),
    )


def _cap_changes(
    previous: tuple[AssetRule, ...], following: tuple[AssetRule, ...]
) -> tuple[HookChange, ...]:
    old = {rule.asset: rule.cap for rule in previous}
    new = {rule.asset: rule.cap for rule in following}
    changes = [
        HookChange(HookChangeKind.SET_CAP, asset, NO_CAP)
        for asset in sorted(old.keys() - new.keys())
    ]
    changes += [
        HookChange(HookChangeKind.SET_CAP, asset, cap)
        for asset, cap in sorted(new.items())
        if old.get(asset) != cap
    ]
    return tuple(changes)
