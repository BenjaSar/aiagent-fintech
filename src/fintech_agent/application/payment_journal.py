from dataclasses import dataclass, replace
from enum import Enum

from fintech_agent.domain.payment_intent import PaymentIntent


class PaymentJournalError(ValueError):
    pass


class PaymentState(Enum):
    PENDING = "pending"
    SETTLED = "settled"
    REFUSED = "refused"


@dataclass(frozen=True)
class PaymentEntry:
    intent: PaymentIntent
    state: PaymentState
    detail: str


@dataclass(frozen=True)
class OpenedPayment:
    entry: PaymentEntry
    already_known: bool


class PaymentJournal:
    def __init__(self) -> None:
        self._entries: dict[str, PaymentEntry] = {}

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def open(self, intent: PaymentIntent) -> OpenedPayment:
        known = self._entries.get(intent.idempotency_key)
        if known is None:
            entry = PaymentEntry(intent, PaymentState.PENDING, "opened")
            self._entries[intent.idempotency_key] = entry
            return OpenedPayment(entry, False)
        if known.intent != intent:
            raise PaymentJournalError(
                f"{intent.idempotency_key} was already used for another payment"
            )
        return OpenedPayment(known, True)

    def settle(self, idempotency_key: str, detail: str) -> PaymentEntry:
        return self._close(idempotency_key, PaymentState.SETTLED, detail)

    def refuse(self, idempotency_key: str, detail: str) -> PaymentEntry:
        return self._close(idempotency_key, PaymentState.REFUSED, detail)

    def entry(self, idempotency_key: str) -> PaymentEntry:
        known = self._entries.get(idempotency_key)
        if known is None:
            raise PaymentJournalError(f"{idempotency_key} is not in the journal")
        return known

    def settled_total(self, asset: str) -> int:
        return sum(
            entry.intent.amount
            for entry in self._entries.values()
            if entry.state is PaymentState.SETTLED and entry.intent.asset == asset
        )

    def _close(
        self, idempotency_key: str, state: PaymentState, detail: str
    ) -> PaymentEntry:
        known = self.entry(idempotency_key)
        if known.state is not PaymentState.PENDING:
            raise PaymentJournalError(
                f"{idempotency_key} is already {known.state.value}"
            )
        closed = replace(known, state=state, detail=detail)
        self._entries[idempotency_key] = closed
        return closed
