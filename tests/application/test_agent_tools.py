from dataclasses import dataclass, field

import pytest

from fintech_agent.application.agent_tools import (
    GET_BALANCE,
    GET_PAYMENT_STATUS,
    LIST_PAYEES,
    PAY_PAYEE,
    PaymentOutcome,
    RuntimeContext,
    ToolInvocation,
    ToolResult,
    ToolRunner,
)
from fintech_agent.application.payment_journal import PaymentJournal, PaymentState
from fintech_agent.application.policy_derivation import derive_session_policy
from fintech_agent.domain.agent_approval import (
    AgentAcknowledgement,
    AgentPresentation,
    ApprovedAgent,
    approve_agent,
    describe_agent,
)
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.approval_display import AssetDisplay, DisplayCatalog
from fintech_agent.domain.payment_intent import PaymentIntent
from fintech_agent.domain.policy_approval import ApprovalLimits
from fintech_agent.domain.session_policy import NATIVE_ASSET
from fintech_agent.domain.tool_catalog import (
    STAGE_ONE_PAYMENT_CATALOG,
    CatalogTool,
    ToolCatalog,
    ToolKind,
)
from tests.definition_fixtures import (
    OWNER,
    PAYEE,
    PAYEE_LABEL,
    build_definition,
    build_registry,
    build_request,
)
from tests.policy_fixtures import ETHER, NOW, TARGET, TOKEN

SESSION_KEY = bytes.fromhex("02" + "33" * 32)
PAYMENT_KEY = "invoice-0001"
BALANCE = ETHER // 2
STAGE_ONE_PAYMENT_CATALOG_TOOLS = tuple(
    STAGE_ONE_PAYMENT_CATALOG.tool(identifier)
    for identifier in STAGE_ONE_PAYMENT_CATALOG.identifiers
)


@dataclass
class RecordingSink:
    outcome: PaymentOutcome = field(
        default_factory=lambda: PaymentOutcome(True, "included")
    )
    submitted: list[PaymentIntent] = field(default_factory=list)

    def submit(self, intent: PaymentIntent) -> PaymentOutcome:
        self.submitted.append(intent)
        return self.outcome


@dataclass
class FixedBalances:
    amount: int = BALANCE
    asked: list[str] = field(default_factory=list)

    def balance_of(self, asset: str) -> int:
        self.asked.append(asset)
        return self.amount


def approve(definition: AgentDefinition) -> ApprovedAgent:
    policy = derive_session_policy(build_request(definition))
    screen = describe_agent(
        AgentPresentation(
            definition,
            policy,
            SESSION_KEY,
            STAGE_ONE_PAYMENT_CATALOG,
            DisplayCatalog(
                AssetDisplay("ETH", 18), {}, build_registry().labels_by_address
            ),
            ApprovalLimits(),
            NOW,
        )
    )
    return approve_agent(
        screen,
        AgentAcknowledgement(
            screen.fingerprint, screen.required_acknowledgements, OWNER, NOW + 1
        ),
    )


@dataclass
class Fixture:
    runner: ToolRunner
    sink: RecordingSink
    balances: FixedBalances
    journal: PaymentJournal


def build(definition: AgentDefinition | None = None) -> Fixture:
    sink = RecordingSink()
    balances = FixedBalances()
    journal = PaymentJournal()
    context = RuntimeContext(
        approve(definition or build_definition()).definition,
        {PAYEE: TARGET},
        {TARGET: PAYEE_LABEL},
        balances,
        sink,
        journal,
        STAGE_ONE_PAYMENT_CATALOG,
    )
    return Fixture(ToolRunner(context), sink, balances, journal)


def pay(fixture: Fixture, **overrides: str) -> ToolResult:
    arguments = {
        "payee": PAYEE,
        "asset": NATIVE_ASSET,
        "amount": str(ETHER // 10),
        "key": PAYMENT_KEY,
    }
    arguments.update(overrides)
    return fixture.runner.invoke(ToolInvocation(PAY_PAYEE, arguments))


def test_the_payee_list_names_the_approved_payees_with_their_labels() -> None:
    result = build().runner.invoke(ToolInvocation(LIST_PAYEES, {}))
    assert result.lines == (f"{PAYEE} {TARGET} {PAYEE_LABEL}",)
    assert not result.failed


def test_the_balance_is_read_for_a_declared_asset_only() -> None:
    fixture = build()
    result = fixture.runner.invoke(ToolInvocation(GET_BALANCE, {"asset": NATIVE_ASSET}))
    assert result.lines == (f"{NATIVE_ASSET} {BALANCE}",)
    assert fixture.balances.asked == [NATIVE_ASSET]
    refused = fixture.runner.invoke(ToolInvocation(GET_BALANCE, {"asset": TOKEN}))
    assert refused.failed
    assert fixture.balances.asked == [NATIVE_ASSET]


def test_a_payment_to_an_approved_payee_reaches_the_sink_once() -> None:
    fixture = build()
    result = pay(fixture)
    assert not result.failed
    assert [intent.payee for intent in fixture.sink.submitted] == [TARGET]
    assert fixture.journal.entry(PAYMENT_KEY).state is PaymentState.SETTLED


def test_the_same_key_never_pays_twice() -> None:
    fixture = build()
    pay(fixture)
    again = pay(fixture)
    assert not again.failed
    assert "already settled" in again.lines[0]
    assert "no second payment" in again.lines[1]
    assert len(fixture.sink.submitted) == 1


def test_one_key_cannot_be_reused_for_another_payment() -> None:
    fixture = build()
    pay(fixture)
    clash = pay(fixture, amount=str(ETHER // 5))
    assert clash.failed
    assert "already used for another payment" in clash.lines[0]
    assert len(fixture.sink.submitted) == 1


def test_a_payee_the_agent_may_not_pay_never_reaches_the_sink() -> None:
    fixture = build()
    result = pay(fixture, payee="stranger")
    assert result.failed
    assert "not a payee this agent may pay" in result.lines[0]
    assert fixture.sink.submitted == []
    assert fixture.journal.keys == ()


def test_an_asset_outside_the_limits_never_reaches_the_sink() -> None:
    fixture = build()
    result = pay(fixture, asset=TOKEN)
    assert result.failed
    assert fixture.sink.submitted == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "0"},
        {"amount": "-1"},
        {"amount": "one"},
        {"amount": ""},
        {"key": "short"},
        {"key": "Invoice-0001"},
        {"key": ""},
    ],
)
def test_a_malformed_payment_never_reaches_the_sink(overrides: dict[str, str]) -> None:
    fixture = build()
    assert pay(fixture, **overrides).failed
    assert fixture.sink.submitted == []


def test_a_refused_payment_is_recorded_as_refused() -> None:
    fixture = build()
    fixture.sink.outcome = PaymentOutcome(False, "the hook refused it")
    result = pay(fixture)
    assert result.failed
    assert fixture.journal.entry(PAYMENT_KEY).state is PaymentState.REFUSED
    again = pay(fixture)
    assert "already refused" in again.lines[0]
    assert len(fixture.sink.submitted) == 1


def test_the_payment_status_can_be_read_back() -> None:
    fixture = build()
    pay(fixture)
    result = fixture.runner.invoke(
        ToolInvocation(GET_PAYMENT_STATUS, {"key": PAYMENT_KEY})
    )
    assert PaymentState.SETTLED.value in result.lines[0]
    unknown = fixture.runner.invoke(
        ToolInvocation(GET_PAYMENT_STATUS, {"key": "invoice-9999"})
    )
    assert unknown.failed


def test_a_tool_outside_the_catalog_is_refused() -> None:
    result = build().runner.invoke(ToolInvocation("drain_account", {}))
    assert result.failed
    assert "not in the tool catalog" in result.lines[0]


def test_a_catalogued_tool_the_agent_was_not_given_is_refused() -> None:
    fixture = build(build_definition(tools=("list_payees", "pay_payee")))
    result = fixture.runner.invoke(ToolInvocation(GET_BALANCE, {"asset": NATIVE_ASSET}))
    assert result.failed
    assert "was not given that tool" in result.lines[0]
    assert fixture.balances.asked == []


def test_a_tool_the_catalog_names_but_this_build_lacks_is_refused() -> None:
    extra = CatalogTool("read_ledger", ToolKind.READ, "Read a ledger this build lacks.")
    catalog = ToolCatalog((*STAGE_ONE_PAYMENT_CATALOG_TOOLS, extra))
    definition = build_definition(tools=("list_payees", "pay_payee", "read_ledger"))
    context = RuntimeContext(
        definition,
        {PAYEE: TARGET},
        {TARGET: PAYEE_LABEL},
        FixedBalances(),
        RecordingSink(),
        PaymentJournal(),
        catalog,
    )
    result = ToolRunner(context).invoke(ToolInvocation("read_ledger", {}))
    assert result.failed
    assert "does not implement that tool" in result.lines[0]
