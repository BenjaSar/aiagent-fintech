from dataclasses import replace

import pytest

from fintech_agent.domain.approval_display import (
    AssetDisplay,
    DisplayCatalog,
    DisplayCatalogError,
    format_duration,
    format_units,
)
from fintech_agent.domain.approval_text import render_text
from fintech_agent.domain.policy_approval import (
    Acknowledgement,
    ApprovalLimits,
    ApprovalRefusedError,
    ApprovalScreen,
    Narrowing,
    NarrowingError,
    PolicyPresentation,
    WarningCode,
    approve,
    describe_policy,
    narrow,
    policy_digest,
)
from fintech_agent.domain.session_policy import NATIVE_ASSET, AssetRule, SessionPolicy
from tests.policy_fixtures import (
    ETHER,
    NOW,
    PAYMASTER,
    STRANGER,
    TARGET,
    TOKEN,
    TOKEN_CAP,
    build_policy,
)

DAY = 86_400
LABELLED = DisplayCatalog(
    AssetDisplay("ETH", 18),
    {TOKEN: AssetDisplay("USDT", 6)},
    {TARGET: "DeFi Yield Vault"},
)


def present(
    policy: SessionPolicy | None = None,
    catalog: DisplayCatalog = LABELLED,
    limits: ApprovalLimits | None = None,
) -> ApprovalScreen:
    return describe_policy(
        PolicyPresentation(
            policy or build_policy(), catalog, limits or ApprovalLimits(), NOW
        )
    )


def refused(
    policy: SessionPolicy, reason: str, catalog: DisplayCatalog = LABELLED
) -> None:
    with pytest.raises(ApprovalRefusedError, match=reason):
        present(policy, catalog)


def test_the_screen_states_caps_time_and_targets() -> None:
    screen = present()
    assert [line.cap_text for line in screen.asset_lines] == ["1 ETH", "1,000 USDT"]
    assert screen.session_length == "33 minutes 20 seconds"
    assert screen.window_length == "1 day"
    assert screen.gas_cap_text == "0.1 ETH"
    assert [(line.address, line.label) for line in screen.target_lines] == [
        (TARGET, "DeFi Yield Vault")
    ]
    assert screen.sponsors == (PAYMASTER,)


def test_the_whole_session_maximum_counts_every_window() -> None:
    policy = replace(
        build_policy(), valid_after=NOW, valid_until=NOW + 3 * DAY, window_seconds=DAY
    )
    lines = {line.asset: line for line in present(policy).asset_lines}
    assert lines[TOKEN].session_maximum_text == "3,000 USDT"
    partial = replace(policy, valid_until=NOW + 2 * DAY + 1)
    lines = {line.asset: line for line in present(partial).asset_lines}
    assert lines[TOKEN].session_maximum_text == "3,000 USDT"


def test_the_permitted_actions_say_that_functions_are_not_limited() -> None:
    text = " ".join(present().permitted)
    assert "any function, with any data" in text
    assert "does not limit which functions" in text
    assert "DeFi Yield Vault" in text
    assert "to any address" in text
    assert "Approvals count against the same cap" in text


def test_native_currency_is_described_as_attached_to_allowed_contracts() -> None:
    text = " ".join(present().permitted)
    assert "attached to calls to the allowed contracts" in text
    policy = replace(build_policy(), allowed_targets=frozenset())
    assert "nowhere" in " ".join(present(policy).permitted)


def test_a_policy_that_grants_nothing_says_so() -> None:
    policy = replace(build_policy(), allowed_targets=frozenset(), rules=())
    assert present(policy).permitted[0].startswith("Nothing")


def test_the_safeguards_caveat_and_control_statements_are_always_shown() -> None:
    text = render_text(present())
    assert "Change its own limits" in text
    assert "earlier, outside these caps" in text
    assert "owner key alone" in text


@pytest.mark.parametrize("cap", [2**128, 2**256 - 1, 2**255])
def test_an_effectively_unlimited_cap_cannot_be_approved(cap: int) -> None:
    policy = replace(build_policy(), rules=(AssetRule(TOKEN, cap),))
    refused(policy, "effectively unlimited")


def test_a_large_but_finite_cap_can_be_approved() -> None:
    policy = replace(build_policy(), rules=(AssetRule(TOKEN, 2**128 - 1),))
    assert present(policy).asset_lines[0].verified


def test_a_known_token_allowed_as_a_target_without_a_cap_is_refused() -> None:
    policy = replace(
        build_policy(),
        allowed_targets=frozenset({TARGET, TOKEN}),
        rules=(AssetRule(NATIVE_ASSET, ETHER),),
    )
    refused(policy, "approve unlimited spending")


def test_a_known_token_with_a_cap_may_also_be_listed_as_a_target() -> None:
    policy = replace(build_policy(), allowed_targets=frozenset({TARGET, TOKEN}))
    present(policy)


def test_a_session_that_has_ended_is_refused() -> None:
    policy = replace(build_policy(), valid_until=NOW)
    refused(policy, "already ended")


def test_a_session_longer_than_the_maximum_is_refused() -> None:
    policy = replace(build_policy(), valid_until=NOW + 31 * DAY)
    refused(policy, "longer than the maximum")
    limits = ApprovalLimits(maximum_session_seconds=60 * DAY)
    assert present(policy, limits=limits).session_length.startswith("31 days")


def test_an_unlabelled_target_needs_a_specific_acknowledgement() -> None:
    catalog = DisplayCatalog(AssetDisplay("ETH", 18), {TOKEN: AssetDisplay("USDT", 6)})
    screen = present(catalog=catalog)
    keys = screen.required_acknowledgements
    assert f"{WarningCode.UNLABELLED_TARGET.value}:{TARGET}" in keys
    assert "approve unlimited spending" in " ".join(w.text for w in screen.warnings)


def test_an_unknown_token_is_shown_in_raw_units_and_flagged() -> None:
    catalog = DisplayCatalog(AssetDisplay("ETH", 18), {}, {TARGET: "DeFi Yield Vault"})
    screen = present(catalog=catalog)
    token_line = next(line for line in screen.asset_lines if line.asset == TOKEN)
    assert not token_line.verified
    assert token_line.cap_text == f"{TOKEN_CAP} raw units"
    assert (
        f"{WarningCode.UNVERIFIED_ASSET.value}:{TOKEN}"
        in screen.required_acknowledgements
    )


def test_native_value_to_targets_and_long_sessions_are_flagged() -> None:
    policy = replace(build_policy(), valid_until=NOW + 8 * DAY)
    keys = present(policy).required_acknowledgements
    assert f"{WarningCode.NATIVE_VALUE_TO_TARGETS.value}:{NATIVE_ASSET}" in keys
    assert f"{WarningCode.LONG_SESSION.value}:session" in keys
    assert not any("long-session" in key for key in present().required_acknowledgements)


def test_approval_requires_the_policy_digest_and_exactly_the_warnings() -> None:
    screen = present()
    keys = screen.required_acknowledgements
    approved = approve(screen, Acknowledgement(screen.policy_digest, keys))
    assert approved.policy == screen.policy
    with pytest.raises(ApprovalRefusedError, match="different policy"):
        approve(screen, Acknowledgement("0" * 64, keys))
    with pytest.raises(ApprovalRefusedError, match="every warning"):
        approve(screen, Acknowledgement(screen.policy_digest, frozenset()))
    with pytest.raises(ApprovalRefusedError, match="every warning"):
        approve(screen, Acknowledgement(screen.policy_digest, keys | {"made-up:x"}))


def test_the_digest_changes_with_any_policy_field() -> None:
    base = build_policy()
    variants = [
        replace(base, valid_until=base.valid_until + 1),
        replace(base, rules=(AssetRule(TOKEN, TOKEN_CAP + 1),)),
        replace(base, allowed_targets=frozenset({STRANGER})),
        replace(base, window_seconds=base.window_seconds + 1),
        replace(base, maximum_gas_cost=base.maximum_gas_cost + 1),
    ]
    digests = {policy_digest(policy) for policy in variants} | {policy_digest(base)}
    assert len(digests) == len(variants) + 1


def test_narrowing_can_lower_caps_remove_targets_and_shorten_the_session() -> None:
    base = build_policy()
    narrowed = narrow(
        base,
        Narrowing(
            lowered_caps=(AssetRule(TOKEN, TOKEN_CAP // 2),),
            removed_assets=frozenset({NATIVE_ASSET}),
            removed_targets=frozenset({TARGET}),
            earlier_end=base.valid_until - 10,
        ),
    )
    assert narrowed.cap_of(TOKEN) == TOKEN_CAP // 2
    assert narrowed.cap_of(NATIVE_ASSET) is None
    assert narrowed.allowed_targets == frozenset()
    assert narrowed.valid_until == base.valid_until - 10
    assert policy_digest(narrowed) != policy_digest(base)


@pytest.mark.parametrize(
    "narrowing",
    [
        Narrowing(lowered_caps=(AssetRule(TOKEN, TOKEN_CAP),)),
        Narrowing(lowered_caps=(AssetRule(TOKEN, TOKEN_CAP + 1),)),
        Narrowing(lowered_caps=(AssetRule(STRANGER, 1),)),
        Narrowing(lowered_caps=(AssetRule(TOKEN, 0),)),
        Narrowing(removed_assets=frozenset({STRANGER})),
        Narrowing(removed_targets=frozenset({STRANGER})),
        Narrowing(earlier_end=build_policy().valid_until + 1),
        Narrowing(earlier_end=build_policy().valid_after),
    ],
)
def test_narrowing_can_never_widen_a_policy(narrowing: Narrowing) -> None:
    with pytest.raises(NarrowingError):
        narrow(build_policy(), narrowing)


def test_a_narrowed_policy_is_presented_and_approved_afresh() -> None:
    base = build_policy()
    narrowed = narrow(base, Narrowing(lowered_caps=(AssetRule(TOKEN, 5 * 10**6),)))
    screen = present(narrowed)
    assert screen.asset_lines[1].cap_text == "5 USDT"
    stale = Acknowledgement(policy_digest(base), screen.required_acknowledgements)
    with pytest.raises(ApprovalRefusedError, match="different policy"):
        approve(screen, stale)


def test_the_text_shows_every_decision_relevant_fact() -> None:
    text = render_text(present())
    for expected in (
        "1,000 USDT per window",
        "DeFi Yield Vault",
        TARGET,
        "Ends:",
        "rolling 1 day",
        "0.1 ETH",
        "can NEVER",
        "Policy fingerprint:",
        PAYMASTER,
    ):
        assert expected in text


def test_the_text_never_shows_raw_call_data() -> None:
    assert "0x095ea7b3" not in render_text(present())


def test_the_text_lists_warnings_and_handles_an_empty_policy() -> None:
    catalog = DisplayCatalog(AssetDisplay("ETH", 18))
    text = render_text(present(catalog=catalog))
    assert "Please read before approving" in text
    empty = replace(build_policy(), allowed_targets=frozenset(), rules=())
    rendered = render_text(present(empty))
    assert "(none)" in rendered
    assert "Please read" not in rendered


def test_the_fingerprint_is_a_prefix_of_the_digest() -> None:
    screen = present()
    assert screen.short_digest.replace(" ", "") == screen.policy_digest[:32]


@pytest.mark.parametrize(
    ("amount", "decimals", "expected"),
    [
        (0, 6, "0"),
        (1, 6, "0.000001"),
        (1_500_000, 6, "1.5"),
        (10**9, 6, "1,000"),
        (7, 0, "7"),
    ],
)
def test_amounts_are_formatted_exactly(
    amount: int, decimals: int, expected: str
) -> None:
    assert format_units(amount, decimals) == expected


def test_durations_are_formatted() -> None:
    assert format_duration(0) == "0 seconds"
    assert format_duration(DAY) == "1 day"
    assert format_duration(DAY + 3600 * 2 + 61) == "1 day 2 hours 1 minute 1 second"


@pytest.mark.parametrize("symbol", ["", "US DT", "USD\u200bT", "A" * 13, "US$"])
def test_a_symbol_must_be_plain(symbol: str) -> None:
    with pytest.raises(DisplayCatalogError):
        AssetDisplay(symbol, 6)


def test_decimals_and_labels_and_addresses_are_validated() -> None:
    with pytest.raises(DisplayCatalogError):
        AssetDisplay("USDT", 37)
    with pytest.raises(DisplayCatalogError):
        AssetDisplay("USDT", -1)
    native = AssetDisplay("ETH", 18)
    with pytest.raises(DisplayCatalogError):
        DisplayCatalog(native, labels={TARGET: "Vault\nSafe"})
    with pytest.raises(DisplayCatalogError):
        DisplayCatalog(native, labels={TARGET: "Vault \u202e"})
    with pytest.raises(DisplayCatalogError):
        DisplayCatalog(native, labels={TARGET.upper().replace("0X", "0x"): "Vault"})


def payment_screen(
    payees: frozenset[str] = frozenset({TARGET}),
    catalog: DisplayCatalog = LABELLED,
) -> ApprovalScreen:
    policy = replace(build_policy(), payments_only=True, allowed_payees=payees)
    return present(policy, catalog=catalog)


def test_a_payment_screen_promises_payments_only() -> None:
    text = " ".join(payment_screen().permitted)
    assert "with no call data attached" in text
    assert "Pay only the 1 approved payee listed below" in text
    assert "cannot approve" in text
    assert "any function, with any data" not in text


def test_a_payment_screen_lists_its_payees() -> None:
    screen = payment_screen()
    assert [(line.address, line.label) for line in screen.payee_lines] == [
        (TARGET, "DeFi Yield Vault")
    ]
    assert not any("payee" in key for key in screen.required_acknowledgements)


def test_an_unlabelled_payee_needs_an_acknowledgement() -> None:
    screen = payment_screen(frozenset({TARGET, STRANGER}))
    assert f"{WarningCode.UNLABELLED_PAYEE.value}:{STRANGER}" in (
        screen.required_acknowledgements
    )
    assert "digit by digit" in " ".join(warning.text for warning in screen.warnings)


def test_a_payment_screen_does_not_warn_about_a_target_that_is_not_a_payee() -> None:
    catalog = DisplayCatalog(AssetDisplay("ETH", 18), {TOKEN: AssetDisplay("USDT", 6)})
    keys = payment_screen(frozenset({TARGET}), catalog).required_acknowledgements
    assert not any(key.startswith(WarningCode.UNLABELLED_TARGET.value) for key in keys)
    assert f"{WarningCode.UNLABELLED_PAYEE.value}:{TARGET}" in keys


def test_a_payments_only_policy_without_payees_grants_nothing() -> None:
    screen = payment_screen(frozenset())
    assert screen.permitted == (
        "Nothing: a payments-only session needs both a limit and a payee.",
    )
    assert screen.payee_lines == ()


def test_the_payment_text_shows_the_payees() -> None:
    text = render_text(payment_screen())
    assert "It may pay only these addresses:" in text
    assert TARGET in text
    assert "DeFi Yield Vault" in text


def test_a_payment_policy_and_an_ordinary_one_have_different_digests() -> None:
    ordinary = present().policy_digest
    assert payment_screen().policy_digest != ordinary


def test_a_payment_screen_counts_its_payees_in_words() -> None:
    assert "the 1 approved payee listed below" in " ".join(payment_screen().permitted)
    two = payment_screen(frozenset({TARGET, STRANGER}))
    assert "the 2 approved payees listed below" in " ".join(two.permitted)


def test_a_payment_screen_replaces_the_allowance_caveat() -> None:
    screen = payment_screen()
    caveats = " ".join(screen.caveats)
    assert "cannot create an allowance" in caveats
    assert "allowance you gave it earlier" not in caveats
    assert "can receive your money" in caveats
    assert "allowance you gave it earlier" in " ".join(present().caveats)


def test_a_payment_screen_does_not_warn_about_value_attached_to_calls() -> None:
    keys = payment_screen().required_acknowledgements
    assert not any(
        key.startswith(WarningCode.NATIVE_VALUE_TO_TARGETS.value) for key in keys
    )
    ordinary = present().required_acknowledgements
    assert any(
        key.startswith(WarningCode.NATIVE_VALUE_TO_TARGETS.value) for key in ordinary
    )
