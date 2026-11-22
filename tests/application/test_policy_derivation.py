import pytest

from fintech_agent.application.payee_registry import ApprovedPayee, PayeeRegistry
from fintech_agent.application.policy_derivation import (
    DerivationError,
    derive_session_policy,
)
from fintech_agent.domain.approval_display import AssetDisplay, DisplayCatalog
from fintech_agent.domain.policy_approval import (
    ApprovalLimits,
    ApprovalRefusedError,
    PolicyPresentation,
    WarningCode,
    describe_policy,
    policy_digest,
)
from fintech_agent.domain.session_policy import (
    NATIVE_ASSET,
    AssetRule,
    PolicyViolationError,
    check_operation,
)
from tests.definition_fixtures import (
    APPROVED_AT,
    CLOCK_SKEW_SECONDS,
    MAXIMUM_GAS_COST,
    OWNER,
    PAYEE,
    PAYEE_LABEL,
    SESSION_SECONDS,
    WINDOW_SECONDS,
    build_definition,
    build_deployment,
    build_registry,
    build_request,
)
from tests.policy_fixtures import (
    ACCOUNT,
    ETHER,
    HOOK,
    NOW,
    PAYMASTER,
    RECIPIENT,
    STRANGER,
    TARGET,
    TOKEN,
    VALIDATOR,
    build_operation,
    single,
)

UNLIMITED_CAP = 2**200


def test_the_derived_policy_takes_its_limits_from_the_definition() -> None:
    policy = derive_session_policy(build_request())
    assert policy.account == ACCOUNT
    assert policy.validator == VALIDATOR
    assert policy.hook == HOOK
    assert policy.rules == (AssetRule(NATIVE_ASSET, ETHER),)
    assert policy.allowed_paymasters == frozenset({PAYMASTER})
    assert policy.maximum_gas_cost == MAXIMUM_GAS_COST
    assert policy.window_seconds == WINDOW_SECONDS
    assert policy.valid_until == NOW + SESSION_SECONDS


def test_the_derived_policy_is_a_payments_only_policy_over_the_approved_payees() -> (
    None
):
    policy = derive_session_policy(build_request())
    assert policy.payments_only
    assert policy.allowed_payees == frozenset({TARGET})
    assert policy.allowed_targets == frozenset({TARGET})


def test_the_session_starts_a_clock_skew_margin_early() -> None:
    policy = derive_session_policy(build_request())
    assert policy.valid_after == NOW - CLOCK_SKEW_SECONDS


def test_the_start_never_goes_below_zero() -> None:
    definition = build_definition(valid_after=0, valid_until=SESSION_SECONDS)
    assert derive_session_policy(build_request(definition)).valid_after == 0


def test_the_same_definition_derives_the_same_policy_every_time() -> None:
    first = derive_session_policy(build_request())
    second = derive_session_policy(build_request())
    assert first == second
    assert policy_digest(first) == policy_digest(second)


def test_an_uncatalogued_tool_is_refused() -> None:
    definition = build_definition(tools=("pay_payee", "drain_account"))
    with pytest.raises(DerivationError, match="not in the tool catalog"):
        derive_session_policy(build_request(definition))


def test_an_agent_without_a_paying_tool_is_refused() -> None:
    definition = build_definition(tools=("list_payees", "get_balance"))
    with pytest.raises(DerivationError, match="needs a tool that can pay"):
        derive_session_policy(build_request(definition))


def test_a_payee_outside_the_registry_is_refused() -> None:
    definition = build_definition(payees=(PAYEE, "stranger"))
    with pytest.raises(DerivationError, match="not an approved payee"):
        derive_session_policy(build_request(definition))


def test_an_agent_without_a_payee_is_refused() -> None:
    definition = build_definition(payees=())
    with pytest.raises(DerivationError, match="at least one approved payee"):
        derive_session_policy(build_request(definition))


def test_a_window_the_hook_does_not_count_is_refused() -> None:
    definition = build_definition(window_seconds=3_600)
    with pytest.raises(DerivationError, match="counts windows of 86400 seconds"):
        derive_session_policy(build_request(definition))


def test_a_payee_that_is_also_a_capped_asset_is_refused() -> None:
    registry = PayeeRegistry()
    registry.approve(ApprovedPayee(PAYEE, TOKEN, PAYEE_LABEL, OWNER, APPROVED_AT))
    definition = build_definition(
        assets=(AssetRule(NATIVE_ASSET, ETHER), AssetRule(TOKEN, ETHER))
    )
    with pytest.raises(DerivationError, match="both a payee and a capped asset"):
        derive_session_policy(build_request(definition, registry=registry))


def test_a_token_payee_becomes_a_payee_but_never_a_target() -> None:
    registry = build_registry()
    registry.approve(
        ApprovedPayee("acme", RECIPIENT, "Acme Supplies", OWNER, APPROVED_AT)
    )
    definition = build_definition(
        payees=(PAYEE, "acme"),
        assets=(AssetRule(NATIVE_ASSET, ETHER), AssetRule(TOKEN, 1_000)),
    )
    policy = derive_session_policy(build_request(definition, registry=registry))
    assert policy.allowed_payees == frozenset({TARGET, RECIPIENT})
    assert TOKEN not in policy.allowed_targets


def test_the_derived_policy_enforces_payments_when_the_enclaves_check_it() -> None:
    policy = derive_session_policy(build_request())
    assert check_operation(policy, build_operation(single(TARGET, 1)), NOW) != ()
    with pytest.raises(PolicyViolationError, match="not allowed"):
        check_operation(policy, build_operation(single(STRANGER, 1)), NOW)
    with pytest.raises(PolicyViolationError, match="cannot carry call data"):
        check_operation(policy, build_operation(single(TARGET, 1, b"\x01")), NOW)


def test_the_derived_policy_passes_the_approval_screen_with_registry_labels() -> None:
    registry = build_registry()
    policy = derive_session_policy(build_request(registry=registry))
    catalog = DisplayCatalog(AssetDisplay("ETH", 18), {}, registry.labels_by_address)
    screen = describe_policy(PolicyPresentation(policy, catalog, ApprovalLimits(), NOW))
    assert [line.label for line in screen.payee_lines] == [PAYEE_LABEL]
    assert not any(
        key.startswith(WarningCode.UNLABELLED_PAYEE.value)
        for key in screen.required_acknowledgements
    )
    assert "Pay only the 1 approved payee" in " ".join(screen.permitted)


def test_a_derived_policy_the_screen_refuses_is_refused_with_its_reason() -> None:
    definition = build_definition(
        assets=(AssetRule(NATIVE_ASSET, UNLIMITED_CAP),),
        budget=build_definition().budget,
    )
    policy = derive_session_policy(build_request(definition))
    catalog = DisplayCatalog(AssetDisplay("ETH", 18))
    with pytest.raises(ApprovalRefusedError, match="effectively unlimited"):
        describe_policy(PolicyPresentation(policy, catalog, ApprovalLimits(), NOW))


def test_the_deployment_decides_the_gas_and_the_paymasters() -> None:
    deployment = build_deployment(
        maximum_gas_cost=1, allowed_paymasters=frozenset({STRANGER})
    )
    policy = derive_session_policy(build_request(deployment=deployment))
    assert policy.maximum_gas_cost == 1
    assert policy.allowed_paymasters == frozenset({STRANGER})
