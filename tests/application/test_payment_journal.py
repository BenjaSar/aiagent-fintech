import pytest

from fintech_agent.application.payment_journal import (
    PaymentJournal,
    PaymentJournalError,
    PaymentState,
)
from fintech_agent.domain.payment_intent import PaymentIntent, PaymentIntentError
from fintech_agent.domain.session_policy import NATIVE_ASSET
from tests.policy_fixtures import ETHER, RECIPIENT, TARGET, TOKEN

KEY = "invoice-0001"
OTHER_KEY = "invoice-0002"


def intent(**overrides: object) -> PaymentIntent:
    fields: dict[str, object] = {
        "idempotency_key": KEY,
        "payee": TARGET,
        "asset": NATIVE_ASSET,
        "amount": ETHER // 10,
    }
    fields.update(overrides)
    return PaymentIntent(**fields)  # type: ignore[arg-type]


def test_an_opened_payment_starts_pending_and_is_new() -> None:
    journal = PaymentJournal()
    opened = journal.open(intent())
    assert not opened.already_known
    assert opened.entry.state is PaymentState.PENDING
    assert journal.keys == (KEY,)


def test_the_same_intent_twice_is_the_same_payment() -> None:
    journal = PaymentJournal()
    journal.open(intent())
    opened = journal.open(intent())
    assert opened.already_known
    assert journal.keys == (KEY,)


def test_one_key_for_two_different_payments_is_an_error() -> None:
    journal = PaymentJournal()
    journal.open(intent())
    for difference in ({"amount": ETHER}, {"payee": RECIPIENT}, {"asset": TOKEN}):
        with pytest.raises(PaymentJournalError, match="another payment"):
            journal.open(intent(**difference))


def test_a_payment_settles_or_is_refused_once() -> None:
    journal = PaymentJournal()
    journal.open(intent())
    settled = journal.settle(KEY, "included")
    assert settled.state is PaymentState.SETTLED
    assert settled.detail == "included"
    with pytest.raises(PaymentJournalError, match="already settled"):
        journal.settle(KEY, "included again")
    with pytest.raises(PaymentJournalError, match="already settled"):
        journal.refuse(KEY, "too late")


def test_a_refused_payment_cannot_then_settle() -> None:
    journal = PaymentJournal()
    journal.open(intent())
    journal.refuse(KEY, "the hook refused it")
    assert journal.entry(KEY).state is PaymentState.REFUSED
    with pytest.raises(PaymentJournalError, match="already refused"):
        journal.settle(KEY, "included")


def test_an_unknown_key_is_reported() -> None:
    journal = PaymentJournal()
    with pytest.raises(PaymentJournalError, match="not in the journal"):
        journal.entry(KEY)
    with pytest.raises(PaymentJournalError, match="not in the journal"):
        journal.settle(KEY, "included")


def test_only_settled_payments_of_that_asset_are_totalled() -> None:
    journal = PaymentJournal()
    journal.open(intent())
    journal.settle(KEY, "included")
    journal.open(intent(idempotency_key=OTHER_KEY, amount=ETHER // 5))
    assert journal.settled_total(NATIVE_ASSET) == ETHER // 10
    journal.settle(OTHER_KEY, "included")
    assert journal.settled_total(NATIVE_ASSET) == ETHER // 10 + ETHER // 5
    assert journal.settled_total(TOKEN) == 0
    journal.open(intent(idempotency_key="invoice-0003"))
    journal.refuse("invoice-0003", "refused")
    assert journal.settled_total(NATIVE_ASSET) == ETHER // 10 + ETHER // 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"idempotency_key": "short"},
        {"idempotency_key": "Invoice-0001"},
        {"idempotency_key": "invoice_0001"},
        {"idempotency_key": "i" * 65},
        {"payee": TARGET.upper().replace("0X", "0x")},
        {"payee": "0xabc"},
        {"asset": "not-an-address"},
        {"amount": 0},
        {"amount": -1},
    ],
)
def test_a_malformed_intent_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(PaymentIntentError):
        intent(**overrides)
