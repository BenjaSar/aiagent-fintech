from dataclasses import replace
from typing import Any

import pytest

from fintech_agent.domain.policy_transition import (
    HookChange,
    HookChangeKind,
    PolicyTransition,
    TransitionError,
    hook_changes,
)
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule
from fintech_agent.domain.user_operation_hash import HashingDomain
from tests.policy_fixtures import (
    ENTRY_POINT,
    ETHER,
    HOOK,
    RECIPIENT,
    STRANGER,
    TARGET,
    TOKEN,
    TOKEN_CAP,
    build_policy,
)


def changes(**overrides: Any) -> tuple[HookChange, ...]:
    base = build_policy()
    return hook_changes(PolicyTransition(base, replace(base, **overrides)))


def test_an_identical_policy_needs_no_hook_change() -> None:
    assert changes() == ()


def test_a_new_session_window_alone_needs_no_hook_change() -> None:
    base = build_policy()
    assert (
        changes(valid_after=base.valid_until, valid_until=base.valid_until + 99) == ()
    )


def test_added_and_removed_targets_become_allow_and_revoke_calls() -> None:
    result = changes(allowed_targets=frozenset({STRANGER}))
    assert result == (
        HookChange(HookChangeKind.REVOKE_TARGET, TARGET),
        HookChange(HookChangeKind.ALLOW_TARGET, STRANGER),
    )


def test_changed_caps_become_set_cap_calls_and_removed_assets_become_zero_caps() -> (
    None
):
    result = changes(rules=(AssetRule(NATIVE_ASSET, 2 * ETHER),))
    assert result == (
        HookChange(HookChangeKind.SET_CAP, TOKEN, 0),
        HookChange(HookChangeKind.SET_CAP, NATIVE_ASSET, 2 * ETHER),
    )


def test_an_unchanged_cap_produces_no_call() -> None:
    result = changes(
        rules=(AssetRule(NATIVE_ASSET, ETHER), AssetRule(TOKEN, TOKEN_CAP + 1))
    )
    assert result == (HookChange(HookChangeKind.SET_CAP, TOKEN, TOKEN_CAP + 1),)


def test_removals_come_before_additions_so_an_asset_can_change_role() -> None:
    result = changes(
        allowed_targets=frozenset({TARGET, TOKEN}),
        rules=(AssetRule(NATIVE_ASSET, ETHER),),
    )
    kinds = [change.kind for change in result]
    assert kinds == [HookChangeKind.SET_CAP, HookChangeKind.ALLOW_TARGET]
    assert result[0] == HookChange(HookChangeKind.SET_CAP, TOKEN, 0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"account": STRANGER},
        {"validator": STRANGER},
        {"hook": STRANGER},
        {"domain": HashingDomain(1, ENTRY_POINT)},
        {"window_seconds": 3_600},
    ],
)
def test_a_rotation_cannot_move_the_anchors_or_change_the_window(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(TransitionError):
        changes(**overrides)


def test_the_hook_address_is_part_of_the_anchor() -> None:
    assert build_policy().hook == HOOK


def test_added_and_removed_payees_become_allow_and_revoke_calls() -> None:
    base = replace(
        build_policy(), payments_only=True, allowed_payees=frozenset({TARGET})
    )
    following = replace(base, allowed_payees=frozenset({RECIPIENT}))
    assert hook_changes(PolicyTransition(base, following)) == (
        HookChange(HookChangeKind.REVOKE_PAYEE, TARGET),
        HookChange(HookChangeKind.ALLOW_PAYEE, RECIPIENT),
    )


def test_payee_changes_follow_the_target_and_cap_changes() -> None:
    base = replace(
        build_policy(), payments_only=True, allowed_payees=frozenset({TARGET})
    )
    following = replace(
        base,
        allowed_targets=frozenset({STRANGER}),
        allowed_payees=frozenset({TARGET, STRANGER}),
    )
    kinds = [change.kind for change in hook_changes(PolicyTransition(base, following))]
    assert kinds == [
        HookChangeKind.REVOKE_TARGET,
        HookChangeKind.ALLOW_TARGET,
        HookChangeKind.ALLOW_PAYEE,
    ]


def test_a_rotation_cannot_change_the_payments_only_flag() -> None:
    base = build_policy()
    with pytest.raises(TransitionError, match="payments-only"):
        hook_changes(PolicyTransition(base, replace(base, payments_only=True)))
