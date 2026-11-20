import json

import pytest

from fintech_agent.adapters.in_memory import InMemorySealedStorage
from fintech_agent.application.policy_authorizer import (
    POLICY_NAME,
    AuthorizerParts,
    PolicyAuthorizer,
)
from fintech_agent.application.ports import (
    AuthorizationRequest,
    EnclaveRefusedError,
)
from fintech_agent.application.spend_ledger import (
    LEDGER_NAME,
    MAXIMUM_EVENTS_PER_ASSET,
    Charge,
    SpendLedger,
)
from fintech_agent.domain.session_policy import NATIVE_ASSET, Movement
from fintech_agent.domain.user_operation import UserOperation
from fintech_agent.domain.user_operation_hash import user_operation_hash
from tests.policy_fixtures import (
    ETHER,
    NOW,
    RECIPIENT,
    STRANGER,
    TARGET,
    TOKEN,
    TOKEN_CAP,
    TRANSFER,
    build_operation,
    build_policy,
    single,
    token_call,
)

WINDOW = 86_400


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def build_authorizer(
    storage: InMemorySealedStorage | None = None, clock: Clock | None = None
) -> PolicyAuthorizer:
    parts = AuthorizerParts(storage or InMemorySealedStorage(), clock or Clock(NOW))
    return PolicyAuthorizer(parts)


def installed_authorizer(clock: Clock | None = None) -> PolicyAuthorizer:
    authorizer = build_authorizer(clock=clock)
    authorizer.issue(build_policy())
    return authorizer


def request_for(operation: UserOperation) -> AuthorizationRequest:
    return AuthorizationRequest(
        user_operation_hash(operation, build_policy().domain), operation
    )


def spend(amount: int) -> AuthorizationRequest:
    return request_for(build_operation(single(TARGET, amount)))


def refusal_text(authorizer: PolicyAuthorizer, request: AuthorizationRequest) -> str:
    with pytest.raises(EnclaveRefusedError) as refusal:
        authorizer.authorize(request)
    return str(refusal.value)


def test_an_enclave_without_a_policy_refuses_everything() -> None:
    authorizer = build_authorizer()

    assert "no session policy" in refusal_text(authorizer, spend(0))


def test_a_policy_can_be_issued_once_only() -> None:
    authorizer = installed_authorizer()

    with pytest.raises(EnclaveRefusedError, match="already installed"):
        authorizer.issue(build_policy())


def test_an_issued_policy_survives_a_restart() -> None:
    storage = InMemorySealedStorage()
    first = build_authorizer(storage)
    first.issue(build_policy())

    restarted = build_authorizer(storage)

    restarted.authorize(spend(0))
    with pytest.raises(EnclaveRefusedError, match="already installed"):
        restarted.issue(build_policy())


def test_a_compliant_operation_is_authorized() -> None:
    installed_authorizer().authorize(spend(ETHER // 2))


def test_a_bare_hash_without_an_operation_is_refused() -> None:
    authorizer = installed_authorizer()
    message = spend(0).message

    assert "no user operation" in refusal_text(
        authorizer, AuthorizationRequest(message, None)
    )


def test_a_hash_that_belongs_to_another_operation_is_refused() -> None:
    authorizer = installed_authorizer()
    honest = spend(0).message
    harmful = build_operation(single(STRANGER, 0))

    assert "does not match" in refusal_text(
        authorizer, AuthorizationRequest(honest, harmful)
    )


def test_a_policy_violation_is_refused_with_its_reason() -> None:
    authorizer = installed_authorizer()

    assert "not allowed" in refusal_text(
        authorizer, request_for(build_operation(single(STRANGER)))
    )


def test_an_expired_session_is_refused_by_the_enclave_clock() -> None:
    clock = Clock(NOW)
    authorizer = installed_authorizer(clock)
    clock.now = build_policy().valid_until

    assert "time window" in refusal_text(authorizer, spend(0))


def test_spends_add_up_against_the_rolling_cap() -> None:
    authorizer = installed_authorizer()
    authorizer.authorize(spend(ETHER * 6 // 10))

    assert "rolling cap" in refusal_text(authorizer, spend(ETHER * 5 // 10))
    authorizer.authorize(spend(ETHER * 4 // 10))
    assert "rolling cap" in refusal_text(authorizer, spend(1))


def test_the_same_operation_is_counted_once_when_signing_is_retried() -> None:
    authorizer = installed_authorizer()
    request = spend(ETHER * 6 // 10)

    authorizer.authorize(request)
    authorizer.authorize(request)

    authorizer.authorize(spend(ETHER * 4 // 10))


def test_the_spend_window_releases_value_after_a_day() -> None:
    clock = Clock(NOW)
    policy = build_policy()
    authorizer = build_authorizer(clock=clock)
    authorizer.issue(
        type(policy)(**{**policy.__dict__, "valid_until": NOW + 10 * WINDOW})
    )
    authorizer.authorize(spend(ETHER))
    clock.now = NOW + WINDOW - 1
    assert "rolling cap" in refusal_text(authorizer, spend(1))

    clock.now = NOW + WINDOW
    authorizer.authorize(spend(1))


def test_token_transfers_count_against_the_token_cap() -> None:
    authorizer = installed_authorizer()
    first = build_operation(
        single(TOKEN, 0, token_call(TRANSFER, RECIPIENT, 700 * 10**6))
    )
    second = build_operation(
        single(TOKEN, 0, token_call(TRANSFER, RECIPIENT, 400 * 10**6))
    )

    authorizer.authorize(request_for(first))
    assert "rolling cap" in refusal_text(authorizer, request_for(second))


def charge_of(amount: int, name: bytes, now: int) -> Charge:
    return Charge(name, (Movement(NATIVE_ASSET, amount),), now)


def test_the_ledger_reloads_what_it_sealed() -> None:
    storage = InMemorySealedStorage()
    SpendLedger(storage).charge(build_policy(), charge_of(5, b"a" * 32, NOW))

    reloaded = SpendLedger(storage)

    assert reloaded.spent(NATIVE_ASSET, NOW, WINDOW) == 5
    assert LEDGER_NAME in storage._entries


def test_a_refused_charge_records_nothing_for_any_asset() -> None:
    storage = InMemorySealedStorage()
    ledger = SpendLedger(storage)
    movements = (Movement(NATIVE_ASSET, 1), Movement(TOKEN, TOKEN_CAP + 1))

    with pytest.raises(EnclaveRefusedError):
        ledger.charge(build_policy(), Charge(b"b" * 32, movements, NOW))

    assert ledger.spent(NATIVE_ASSET, NOW, WINDOW) == 0
    assert LEDGER_NAME not in storage._entries


def test_the_window_holds_a_bounded_number_of_events() -> None:
    ledger = SpendLedger(InMemorySealedStorage())
    for index in range(MAXIMUM_EVENTS_PER_ASSET):
        ledger.charge(build_policy(), charge_of(1, index.to_bytes(32, "big"), NOW))

    with pytest.raises(EnclaveRefusedError, match="full"):
        ledger.charge(build_policy(), charge_of(1, b"z" * 32, NOW))


def test_the_policy_is_stored_as_a_document() -> None:
    storage = InMemorySealedStorage()
    build_authorizer(storage).issue(build_policy())

    assert json.loads(storage.unseal(POLICY_NAME))["account"] == build_policy().account
