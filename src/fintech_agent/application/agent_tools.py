from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from fintech_agent.application.payment_journal import (
    PaymentEntry,
    PaymentJournal,
    PaymentJournalError,
)
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.payment_intent import PaymentIntent, PaymentIntentError
from fintech_agent.domain.tool_catalog import ToolCatalog, ToolCatalogError

LIST_PAYEES = "list_payees"
GET_BALANCE = "get_balance"
GET_PAYMENT_STATUS = "get_payment_status"
PAY_PAYEE = "pay_payee"
UNLABELLED = "(unlabelled)"


class PaymentRefusedError(ValueError):
    pass


@dataclass(frozen=True)
class PaymentOutcome:
    settled: bool
    detail: str


@dataclass(frozen=True)
class ToolInvocation:
    name: str
    arguments: Mapping[str, str]


@dataclass(frozen=True)
class ToolResult:
    name: str
    lines: tuple[str, ...]
    failed: bool = False


class BalanceReader(Protocol):
    def balance_of(self, asset: str) -> int: ...


class IntentSink(Protocol):
    def submit(self, intent: PaymentIntent) -> PaymentOutcome: ...


@dataclass(frozen=True)
class RuntimeContext:
    definition: AgentDefinition
    payees: Mapping[str, str]
    labels: Mapping[str, str]
    balances: BalanceReader
    sink: IntentSink
    journal: PaymentJournal
    catalog: ToolCatalog


class ToolRunner:
    def __init__(self, context: RuntimeContext) -> None:
        self._context = context
        self._handlers: Mapping[str, Callable[[ToolInvocation], ToolResult]] = {
            LIST_PAYEES: self._list_payees,
            GET_BALANCE: self._get_balance,
            GET_PAYMENT_STATUS: self._get_payment_status,
            PAY_PAYEE: self._pay_payee,
        }

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        refusal = self._refusal(invocation.name)
        if refusal is not None:
            return refusal
        return self._handlers[invocation.name](invocation)

    def _refusal(self, name: str) -> ToolResult | None:
        try:
            self._context.catalog.tool(name)
        except ToolCatalogError as unknown:
            return _failed(name, str(unknown))
        if name not in self._context.definition.tools:
            return _failed(name, "this agent was not given that tool")
        if name not in self._handlers:
            return _failed(name, "this build does not implement that tool")
        return None

    def _list_payees(self, invocation: ToolInvocation) -> ToolResult:
        context = self._context
        lines = tuple(
            f"{identifier} {address} {context.labels.get(address, UNLABELLED)}"
            for identifier, address in sorted(context.payees.items())
        )
        return ToolResult(invocation.name, lines or ("no payee is approved",))

    def _get_balance(self, invocation: ToolInvocation) -> ToolResult:
        asset = invocation.arguments.get("asset", "")
        if asset not in self._declared_assets():
            return _failed(invocation.name, "that asset is not in this agent's limits")
        amount = self._context.balances.balance_of(asset)
        return ToolResult(invocation.name, (f"{asset} {amount}",))

    def _get_payment_status(self, invocation: ToolInvocation) -> ToolResult:
        key = invocation.arguments.get("key", "")
        try:
            entry = self._context.journal.entry(key)
        except PaymentJournalError as unknown:
            return _failed(invocation.name, str(unknown))
        return ToolResult(
            invocation.name, (f"{key} {entry.state.value} {entry.detail}",)
        )

    def _pay_payee(self, invocation: ToolInvocation) -> ToolResult:
        try:
            intent = self._intent(invocation)
        except (PaymentIntentError, PaymentRefusedError) as refusal:
            return _failed(invocation.name, str(refusal))
        return self._settle(invocation.name, intent)

    def _settle(self, name: str, intent: PaymentIntent) -> ToolResult:
        try:
            opened = self._context.journal.open(intent)
        except PaymentJournalError as clash:
            return _failed(name, str(clash))
        if opened.already_known:
            return _already_paid(name, opened.entry)
        outcome = self._context.sink.submit(intent)
        entry = self._record(intent, outcome)
        return ToolResult(
            name,
            (f"{intent.idempotency_key} {entry.state.value} {entry.detail}",),
            failed=not outcome.settled,
        )

    def _record(self, intent: PaymentIntent, outcome: PaymentOutcome) -> PaymentEntry:
        journal = self._context.journal
        if outcome.settled:
            return journal.settle(intent.idempotency_key, outcome.detail)
        return journal.refuse(intent.idempotency_key, outcome.detail)

    def _intent(self, invocation: ToolInvocation) -> PaymentIntent:
        arguments = invocation.arguments
        identifier = arguments.get("payee", "")
        address = self._context.payees.get(identifier)
        if address is None:
            raise PaymentRefusedError(f"{identifier} is not a payee this agent may pay")
        asset = arguments.get("asset", "")
        if asset not in self._declared_assets():
            raise PaymentRefusedError("that asset is not in this agent's limits")
        return PaymentIntent(
            arguments.get("key", ""), address, asset, _amount(arguments)
        )

    def _declared_assets(self) -> frozenset[str]:
        return self._context.definition.declared_assets


def _already_paid(name: str, entry: PaymentEntry) -> ToolResult:
    return ToolResult(
        name,
        (
            f"{entry.intent.idempotency_key} was already {entry.state.value}",
            "no second payment was made",
        ),
    )


def _amount(arguments: Mapping[str, str]) -> int:
    raw = arguments.get("amount", "")
    if not raw.isdecimal():
        raise PaymentRefusedError("the amount must be a whole number of units")
    return int(raw)


def _failed(name: str, detail: str) -> ToolResult:
    return ToolResult(name, (detail,), failed=True)
