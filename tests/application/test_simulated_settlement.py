from dataclasses import replace

from fintech_agent.adapters.in_memory import InMemorySealedStorage
from fintech_agent.application.policy_derivation import derive_session_policy
from fintech_agent.application.simulated_settlement import (
    SETTLED_DETAIL,
    SHORT_DETAIL,
    SimulatedSettlement,
    SimulationParts,
)
from fintech_agent.domain.payment_intent import PaymentIntent
from fintech_agent.domain.session_policy import NATIVE_ASSET, SessionPolicy
from tests.definition_fixtures import build_definition, build_request
from tests.policy_fixtures import ETHER, NOW, STRANGER, TARGET

BALANCE = ETHER
KEY = "settlement-0001"


def policy() -> SessionPolicy:
    return derive_session_policy(build_request(build_definition()))


def settlement(
    balance: int = BALANCE, now: int = NOW, limits: SessionPolicy | None = None
) -> SimulatedSettlement:
    return SimulatedSettlement(
        SimulationParts(
            limits or policy(),
            InMemorySealedStorage(),
            {NATIVE_ASSET: balance},
            now,
        )
    )


def intent(amount: int, payee: str = TARGET, key: str = KEY) -> PaymentIntent:
    return PaymentIntent(key, payee, NATIVE_ASSET, amount)


def test_a_payment_inside_the_policy_settles_and_moves_the_balance() -> None:
    simulation = settlement()
    outcome = simulation.submit(intent(ETHER // 10))
    assert outcome.settled
    assert outcome.detail == SETTLED_DETAIL
    assert simulation.balance_of(NATIVE_ASSET) == BALANCE - ETHER // 10
    assert simulation.received(TARGET) == ETHER // 10


def test_a_payment_above_the_balance_is_refused_and_moves_nothing() -> None:
    simulation = settlement(balance=ETHER // 100)
    outcome = simulation.submit(intent(ETHER // 50))
    assert not outcome.settled
    assert outcome.detail == SHORT_DETAIL
    assert simulation.balance_of(NATIVE_ASSET) == ETHER // 100
    assert simulation.received(TARGET) == 0


def test_a_payee_outside_the_policy_is_refused_by_the_enclave_check() -> None:
    simulation = settlement()
    outcome = simulation.submit(intent(ETHER // 10, payee=STRANGER))
    assert not outcome.settled
    assert "not allowed" in outcome.detail
    assert simulation.balance_of(NATIVE_ASSET) == BALANCE


def test_a_payment_above_the_cap_is_refused_by_the_enclave_check() -> None:
    simulation = settlement(balance=10 * ETHER)
    outcome = simulation.submit(intent(2 * ETHER))
    assert not outcome.settled
    assert "cap" in outcome.detail
    assert simulation.received(TARGET) == 0


def test_the_rolling_window_accumulates_across_payments() -> None:
    simulation = settlement(balance=10 * ETHER)
    first = simulation.submit(intent(ETHER, key="settlement-0001"))
    second = simulation.submit(intent(ETHER, key="settlement-0002"))
    assert first.settled
    assert not second.settled
    assert "cap" in second.detail
    assert simulation.received(TARGET) == ETHER


def test_a_payment_after_the_session_is_refused() -> None:
    limits = policy()
    simulation = settlement(now=limits.valid_until, limits=limits)
    outcome = simulation.submit(intent(ETHER // 10))
    assert not outcome.settled
    assert "time window" in outcome.detail


def test_each_payment_takes_its_own_nonce() -> None:
    simulation = settlement(balance=10 * ETHER)
    small = ETHER // 10
    assert simulation.submit(intent(small, key="settlement-0001")).settled
    assert simulation.submit(intent(small, key="settlement-0002")).settled
    assert simulation.received(TARGET) == 2 * small


def test_a_policy_that_allows_nothing_refuses_everything() -> None:
    empty = replace(policy(), allowed_payees=frozenset(), allowed_targets=frozenset())
    simulation = settlement(limits=empty)
    assert not simulation.submit(intent(ETHER // 10)).settled
