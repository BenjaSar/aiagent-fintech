from dataclasses import replace

import pytest

from fintech_agent.domain.session_call import Execution
from fintech_agent.domain.session_policy import (
    NATIVE_ASSET,
    AssetRule,
    Movement,
    PolicyViolationError,
    check_operation,
)
from fintech_agent.domain.session_policy_document import (
    policy_from_document,
    policy_to_document,
)
from tests.policy_fixtures import (
    ACCOUNT,
    APPROVE,
    ENTRY_POINT,
    ETHER,
    HOOK,
    NOW,
    PAYMASTER,
    RECIPIENT,
    SESSION_NONCE,
    STRANGER,
    TARGET,
    TOKEN,
    TOKEN_CAP,
    TRANSFER,
    batch,
    build_operation,
    build_policy,
    single,
    token_call,
    with_paymaster,
)


def check(call_data: bytes, now: int = NOW) -> tuple[Movement, ...]:
    return check_operation(build_policy(), build_operation(call_data), now)


def refused(call_data: bytes, reason: str) -> None:
    with pytest.raises(PolicyViolationError, match=reason):
        check(call_data)


def test_a_call_to_an_allowed_target_without_value_moves_nothing() -> None:
    assert check(single(TARGET, 0, b"\x01\x02")) == ()


def test_native_value_to_an_allowed_target_is_a_native_movement() -> None:
    assert check(single(TARGET, 5)) == (Movement(NATIVE_ASSET, 5),)


def test_a_target_outside_the_allowlist_is_refused() -> None:
    refused(single(STRANGER), "not allowed")


@pytest.mark.parametrize("target", [ACCOUNT, NATIVE_ASSET, HOOK, ENTRY_POINT])
def test_infrastructure_targets_are_forbidden(target: str) -> None:
    refused(single(target), "forbidden")


def test_a_plain_execute_call_is_refused() -> None:
    plain = bytes.fromhex("e9ae5c53") + single(TARGET)[4:]

    refused(plain, "executeAsSession")


def test_native_value_is_refused_when_the_policy_has_no_native_cap() -> None:
    policy = replace(build_policy(), rules=(AssetRule(TOKEN, TOKEN_CAP),))

    with pytest.raises(PolicyViolationError, match="native value"):
        check_operation(policy, build_operation(single(TARGET, 1)), NOW)


def test_a_token_transfer_is_a_movement_of_the_token() -> None:
    call = single(TOKEN, 0, token_call(TRANSFER, RECIPIENT, 250))

    assert check(call) == (Movement(TOKEN, 250),)


def test_a_token_approval_counts_like_a_transfer() -> None:
    call = single(TOKEN, 0, token_call(APPROVE, RECIPIENT, 400))

    assert check(call) == (Movement(TOKEN, 400),)


def test_a_token_call_with_trailing_bytes_is_refused() -> None:
    call = single(TOKEN, 0, token_call(TRANSFER, RECIPIENT, 1) + b"\x00")

    refused(call, "only transfer and approve")


def test_a_token_call_with_another_selector_is_refused() -> None:
    call = single(TOKEN, 0, token_call(bytes.fromhex("23b872dd"), RECIPIENT, 1))

    refused(call, "only transfer and approve")


def test_a_token_call_with_native_value_is_refused() -> None:
    refused(single(TOKEN, 1, token_call(TRANSFER, RECIPIENT, 1)), "native value")


def test_a_token_call_with_a_dirty_address_word_is_refused() -> None:
    dirty = bytearray(token_call(TRANSFER, RECIPIENT, 1))
    dirty[4] = 1

    refused(single(TOKEN, 0, bytes(dirty)), "malformed address")


def test_a_batch_adds_movements_of_the_same_asset() -> None:
    call = batch(
        Execution(TARGET, 3, b""),
        Execution(TOKEN, 0, token_call(TRANSFER, RECIPIENT, 10)),
        Execution(TARGET, 4, b""),
        Execution(TOKEN, 0, token_call(APPROVE, RECIPIENT, 5)),
    )

    assert check(call) == (Movement(NATIVE_ASSET, 7), Movement(TOKEN, 15))


def test_one_forbidden_execution_refuses_the_whole_batch() -> None:
    call = batch(Execution(TARGET, 0, b""), Execution(ACCOUNT, 0, b""))

    refused(call, "forbidden")


def test_an_operation_for_another_account_is_refused() -> None:
    operation = replace(build_operation(single(TARGET)), sender=STRANGER)

    with pytest.raises(PolicyViolationError, match="account"):
        check_operation(build_policy(), operation, NOW)


def test_an_operation_that_creates_an_account_is_refused() -> None:
    operation = replace(build_operation(single(TARGET)), init_code=b"\x01" * 24)

    with pytest.raises(PolicyViolationError, match="create"):
        check_operation(build_policy(), operation, NOW)


def test_a_nonce_key_that_is_not_the_session_validator_is_refused() -> None:
    operation = replace(build_operation(single(TARGET)), nonce=0)

    with pytest.raises(PolicyViolationError, match="nonce key"):
        check_operation(build_policy(), operation, NOW)


def test_the_nonce_sequence_does_not_matter() -> None:
    operation = replace(build_operation(single(TARGET)), nonce=SESSION_NONCE + 41)

    assert check_operation(build_policy(), operation, NOW) == ()


def test_the_session_window_starts_inclusive_and_ends_exclusive() -> None:
    policy = build_policy()
    operation = build_operation(single(TARGET))

    assert check_operation(policy, operation, policy.valid_after) == ()
    assert check_operation(policy, operation, policy.valid_until - 1) == ()
    with pytest.raises(PolicyViolationError, match="time window"):
        check_operation(policy, operation, policy.valid_until)
    with pytest.raises(PolicyViolationError, match="time window"):
        check_operation(policy, operation, policy.valid_after - 1)


def test_an_allowed_paymaster_passes_and_another_is_refused() -> None:
    operation = build_operation(single(TARGET))

    assert (
        check_operation(build_policy(), with_paymaster(operation, PAYMASTER), NOW) == ()
    )
    with pytest.raises(PolicyViolationError, match="paymaster"):
        check_operation(build_policy(), with_paymaster(operation, STRANGER), NOW)


def test_gas_above_the_limit_is_refused() -> None:
    policy = replace(build_policy(), maximum_gas_cost=1)

    with pytest.raises(PolicyViolationError, match="gas"):
        check_operation(policy, build_operation(single(TARGET)), NOW)


def test_the_gas_of_a_paymaster_counts_toward_the_limit() -> None:
    operation = with_paymaster(build_operation(single(TARGET)), PAYMASTER)
    plain_cost = 550_000 * 3 * 10**9
    policy = replace(build_policy(), maximum_gas_cost=plain_cost)

    assert check_operation(policy, build_operation(single(TARGET)), NOW) == ()
    with pytest.raises(PolicyViolationError, match="gas"):
        check_operation(policy, operation, NOW)


def test_addresses_must_be_lowercase_hex() -> None:
    with pytest.raises(PolicyViolationError, match="lowercase"):
        replace(build_policy(), account=ACCOUNT.upper().replace("0X", "0x"))
    with pytest.raises(PolicyViolationError, match="lowercase"):
        replace(build_policy(), allowed_targets=frozenset({"0x1234"}))


def test_a_policy_with_repeated_rules_a_zero_cap_or_an_empty_window_is_refused() -> (
    None
):
    policy = build_policy()

    with pytest.raises(PolicyViolationError):
        replace(policy, rules=(AssetRule(TOKEN, 1), AssetRule(TOKEN, 2)))
    with pytest.raises(PolicyViolationError):
        replace(policy, rules=(AssetRule(TOKEN, 0),))
    with pytest.raises(PolicyViolationError):
        replace(policy, valid_until=policy.valid_after)


def test_a_policy_survives_its_document_form() -> None:
    policy = build_policy()

    assert policy_from_document(policy_to_document(policy)) == policy
    assert policy.cap_of(NATIVE_ASSET) == ETHER
