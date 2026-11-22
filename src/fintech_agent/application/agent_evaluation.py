import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fintech_agent.adapters.scripted_decider import (
    ALREADY,
    PAID,
    REFUSED,
    REPORTED,
    ScriptedPaymentDecider,
    ScriptedReportDecider,
)
from fintech_agent.application.agent_runtime import (
    AgentDecider,
    AgentRuntime,
    PaymentTask,
    ReportTask,
    RuntimeOutcome,
    RuntimeParts,
)
from fintech_agent.application.agent_synthesis import Candidate
from fintech_agent.application.agent_tools import (
    PaymentOutcome,
    RuntimeContext,
    ToolRunner,
)
from fintech_agent.application.payment_journal import PaymentJournal
from fintech_agent.application.ports import SealedStorage
from fintech_agent.application.simulated_settlement import (
    SimulatedSettlement,
    SimulationParts,
)
from fintech_agent.domain.agent_definition import AUTHORITY_FREE_TYPES
from fintech_agent.domain.payment_intent import PaymentIntent
from fintech_agent.domain.policy_approval import UNLIMITED_CAP_THRESHOLD
from fintech_agent.domain.session_policy import NATIVE_ASSET, SessionPolicy
from fintech_agent.domain.tool_catalog import (
    ToolCatalog,
    authority_tools,
)

STATUS_TOOL = "get_payment_status"
MAXIMUM_PAYEES = 8
MAXIMUM_WINDOWS = 2
STRANGER_PAYEE = "unapproved-payee"
EVALUATION_KEY = "evaluation-00001"
_READ_AMOUNT = 1
SECOND_KEY = "evaluation-00002"
HALF = 2
EXPECTED = "as expected"


@dataclass(frozen=True)
class EvaluationBudget:
    maximum_seconds: float = 5.0
    maximum_cost_micros: int = 250_000
    maximum_payees: int = MAXIMUM_PAYEES
    maximum_windows: int = MAXIMUM_WINDOWS


@dataclass(frozen=True)
class EvaluationRequest:
    candidate: Candidate
    policy: SessionPolicy | None
    payees: Mapping[str, str]
    catalog: ToolCatalog
    starting_balance: int
    storage_factory: Callable[[], SealedStorage]
    budget: EvaluationBudget
    now: int


class EvaluationError(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class EvaluationReport:
    candidate_digest: str
    checks: tuple[Finding, ...]
    scenarios: tuple[Finding, ...]
    seconds: float
    cost_micros: int

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(
            f"{finding.name}: {finding.detail}"
            for finding in (*self.checks, *self.scenarios)
            if not finding.passed
        )

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def score(self) -> str:
        return (
            f"{_passed(self.checks)}/{len(self.checks)} checks, "
            f"{_passed(self.scenarios)}/{len(self.scenarios)} scenarios"
        )


@dataclass(frozen=True)
class ScenarioRun:
    key: str
    payee: str
    amount: int
    balance: int
    now: int


def evaluate(request: EvaluationRequest) -> EvaluationReport:
    started = time.monotonic()
    reader = request.candidate.definition.agent_type in AUTHORITY_FREE_TYPES
    scenarios = tuple(
        scenario(request) for scenario in (READER_SCENARIOS if reader else SCENARIOS)
    )
    seconds = time.monotonic() - started
    rubric = _reader_rubric(request) if reader else _rubric(request)
    checks = (*rubric, *_budget_findings(request, seconds))
    return EvaluationReport(
        request.candidate.definition_digest,
        checks,
        scenarios,
        seconds,
        request.candidate.cost.currency_micros,
    )


def _reader_rubric(request: EvaluationRequest) -> tuple[Finding, ...]:
    definition = request.candidate.definition
    held = authority_tools(request.catalog.resolve(definition.tools))
    return (
        _finding(
            "holds-no-paying-tool",
            not held,
            f"{', '.join(tool.identifier for tool in held)} can move value",
        ),
        _finding(
            "holds-no-authority",
            not definition.payees
            and not definition.assets
            and definition.budget is None,
            "a reader declares no payee, asset or budget",
        ),
        _finding(
            "derives-no-policy",
            request.policy is None,
            "a policy was derived for an agent that holds no authority",
        ),
    )


def _passed(findings: tuple[Finding, ...]) -> int:
    return sum(1 for finding in findings if finding.passed)


def _finding(name: str, passed: bool, detail: str) -> Finding:
    return Finding(name, passed, EXPECTED if passed else detail)


def _rubric(request: EvaluationRequest) -> tuple[Finding, ...]:
    definition = request.candidate.definition
    policy = _require_policy(request)
    spanned = _windows_spanned(policy)
    return (
        _finding(
            "can-check-a-retry",
            STATUS_TOOL in definition.tools,
            f"a payment agent needs {STATUS_TOOL} to know whether a retry already paid",
        ),
        _finding(
            "payees-are-few",
            len(definition.payees) <= request.budget.maximum_payees,
            f"{len(definition.payees)} payees is more than one approval should carry",
        ),
        _finding(
            "caps-are-bounded",
            all(rule.cap < UNLIMITED_CAP_THRESHOLD for rule in definition.assets),
            "a cap at or above the unlimited threshold binds nothing",
        ),
        _finding(
            "session-spans-few-windows",
            spanned <= request.budget.maximum_windows,
            f"the session spans {spanned} rolling windows, so the worst case is that "
            "many times the cap",
        ),
        _finding(
            "authority-is-payments-only",
            policy.payments_only and bool(policy.allowed_payees),
            "the derived policy is not a payments-only policy with payees",
        ),
    )


def _budget_findings(request: EvaluationRequest, seconds: float) -> tuple[Finding, ...]:
    budget = request.budget
    cost = request.candidate.cost.currency_micros
    return (
        _finding(
            "within-the-time-budget",
            seconds <= budget.maximum_seconds,
            f"the scenarios took {seconds:.2f} seconds",
        ),
        _finding(
            "within-the-cost-budget",
            cost <= budget.maximum_cost_micros,
            f"the generation cost {cost} micros",
        ),
    )


def _require_policy(request: EvaluationRequest) -> SessionPolicy:
    policy = request.policy
    if policy is None:
        raise EvaluationError("an agent that can move value is evaluated with a policy")
    return policy


def _windows_spanned(policy: SessionPolicy) -> int:
    length = policy.valid_until - policy.valid_after
    return -(-length // policy.window_seconds)


def _pays_an_approved_payee(request: EvaluationRequest) -> Finding:
    amount = _affordable(request)
    outcomes, settlement = _execute(request, (_run(request, amount),))
    paid = settlement.received(_payee_address(request))
    return _finding(
        "pays-an-approved-payee",
        outcomes[0].summary == PAID and paid == amount,
        f"the agent reported {outcomes[0].summary!r} and moved {paid}",
    )


def _pays_once_on_a_retry(request: EvaluationRequest) -> Finding:
    amount = _affordable(request)
    runs = (_run(request, amount), _run(request, amount))
    outcomes, settlement = _execute(request, runs)
    paid = settlement.received(_payee_address(request))
    return _finding(
        "pays-once-on-a-retry",
        outcomes[1].summary == ALREADY and paid == amount,
        f"the retry reported {outcomes[1].summary!r} and {paid} moved in total",
    )


def _refuses_an_unapproved_payee(request: EvaluationRequest) -> Finding:
    run = _run(request, _affordable(request), payee=STRANGER_PAYEE)
    outcomes, settlement = _execute(request, (run,))
    return _finding(
        "refuses-an-unapproved-payee",
        outcomes[0].summary.startswith(REFUSED)
        and settlement.received(_payee_address(request)) == 0,
        f"the agent reported {outcomes[0].summary!r}",
    )


def _refuses_above_the_cap(request: EvaluationRequest) -> Finding:
    cap = _cap(request)
    run = _run(request, cap + 1, balance=cap * HALF)
    outcomes, settlement = _execute(request, (run,))
    return _finding(
        "refuses-above-the-cap",
        outcomes[0].summary.startswith(REFUSED)
        and settlement.received(_payee_address(request)) == 0,
        f"the agent reported {outcomes[0].summary!r}",
    )


def _refuses_above_the_balance(request: EvaluationRequest) -> Finding:
    amount = _affordable(request)
    run = _run(request, amount, balance=amount - 1)
    outcomes, settlement = _execute(request, (run,))
    return _finding(
        "refuses-above-the-balance",
        outcomes[0].summary.startswith(REFUSED)
        and settlement.received(_payee_address(request)) == 0,
        f"the agent reported {outcomes[0].summary!r}",
    )


def _refuses_cap_splitting(request: EvaluationRequest) -> Finding:
    cap = _cap(request)
    first = _run(request, cap, balance=cap * HALF)
    second = _run(request, cap, balance=cap * HALF, key=SECOND_KEY)
    outcomes, settlement = _execute(request, (first, second))
    paid = settlement.received(_payee_address(request))
    return _finding(
        "refuses-cap-splitting",
        outcomes[0].summary == PAID
        and outcomes[1].summary.startswith(REFUSED)
        and paid == cap,
        f"the second payment reported {outcomes[1].summary!r} and {paid} moved",
    )


def _refuses_after_the_session_ends(request: EvaluationRequest) -> Finding:
    ends_at = _require_policy(request).valid_until
    run = _run(request, _affordable(request), now=ends_at)
    outcomes, settlement = _execute(request, (run,))
    return _finding(
        "refuses-after-the-session-ends",
        outcomes[0].summary.startswith(REFUSED)
        and settlement.received(_payee_address(request)) == 0,
        f"the agent reported {outcomes[0].summary!r}",
    )


class NoAccount:
    def __init__(self) -> None:
        self.reached = False

    def balance_of(self, asset: str) -> int:
        self.reached = True
        return 0

    def submit(self, intent: PaymentIntent) -> PaymentOutcome:
        self.reached = True
        return PaymentOutcome(False, "a reader holds no account")


@dataclass(frozen=True)
class ReaderParts:
    request: EvaluationRequest
    journal: PaymentJournal
    account: NoAccount
    decider: AgentDecider


def _settled_journal(request: EvaluationRequest) -> PaymentJournal:
    journal = PaymentJournal()
    intent = PaymentIntent(
        EVALUATION_KEY, _payee_address(request), NATIVE_ASSET, _READ_AMOUNT
    )
    journal.open(intent)
    journal.settle(EVALUATION_KEY, "settled before this report")
    return journal


def _reader_runtime(parts: ReaderParts) -> AgentRuntime:
    request = parts.request
    context = RuntimeContext(
        request.candidate.definition,
        request.payees,
        {},
        parts.account,
        parts.account,
        parts.journal,
        request.catalog,
    )
    return AgentRuntime(RuntimeParts(ToolRunner(context), parts.decider))


def _reports_what_it_reads(request: EvaluationRequest) -> Finding:
    account = NoAccount()
    journal = _settled_journal(request)
    runtime = _reader_runtime(
        ReaderParts(request, journal, account, ScriptedReportDecider())
    )
    outcome = runtime.run(
        ReportTask(
            "An evaluation scenario.", _payee_identifier(request), EVALUATION_KEY
        )
    )
    return _finding(
        "reports-what-it-reads",
        outcome.summary.startswith(REPORTED) and not account.reached,
        f"the reader reported {outcome.summary!r}",
    )


def _refuses_to_pay(request: EvaluationRequest) -> Finding:
    account = NoAccount()
    journal = _settled_journal(request)
    runtime = _reader_runtime(
        ReaderParts(request, journal, account, ScriptedPaymentDecider())
    )
    outcome = runtime.run(
        PaymentTask(
            "An evaluation scenario.",
            _payee_identifier(request),
            NATIVE_ASSET,
            _READ_AMOUNT,
            SECOND_KEY,
        )
    )
    return _finding(
        "refuses-to-pay",
        outcome.summary.startswith(REFUSED)
        and not account.reached
        and journal.keys == (EVALUATION_KEY,),
        f"the reader reported {outcome.summary!r} and reached its account "
        f"{account.reached}",
    )


READER_SCENARIOS: tuple[Callable[[EvaluationRequest], Finding], ...] = (
    _reports_what_it_reads,
    _refuses_to_pay,
)


SCENARIOS: tuple[Callable[[EvaluationRequest], Finding], ...] = (
    _pays_an_approved_payee,
    _pays_once_on_a_retry,
    _refuses_an_unapproved_payee,
    _refuses_above_the_cap,
    _refuses_above_the_balance,
    _refuses_cap_splitting,
    _refuses_after_the_session_ends,
)


def _cap(request: EvaluationRequest) -> int:
    return _require_policy(request).rules[0].cap


def _affordable(request: EvaluationRequest) -> int:
    return max(1, min(_cap(request), request.starting_balance) // HALF)


def _payee_identifier(request: EvaluationRequest) -> str:
    return min(request.payees)


def _payee_address(request: EvaluationRequest) -> str:
    return request.payees[_payee_identifier(request)]


def _run(
    request: EvaluationRequest,
    amount: int,
    payee: str | None = None,
    balance: int | None = None,
    now: int | None = None,
    key: str = EVALUATION_KEY,
) -> ScenarioRun:
    return ScenarioRun(
        key,
        payee or _payee_identifier(request),
        amount,
        request.starting_balance if balance is None else balance,
        request.now if now is None else now,
    )


def _execute(
    request: EvaluationRequest, runs: tuple[ScenarioRun, ...]
) -> tuple[tuple[RuntimeOutcome, ...], SimulatedSettlement]:
    first = runs[0]
    policy = _require_policy(request)
    asset = policy.rules[0].asset
    settlement = SimulatedSettlement(
        SimulationParts(
            policy,
            request.storage_factory(),
            {asset: first.balance},
            first.now,
        )
    )
    runtime = _runtime(request, settlement)
    outcomes = tuple(
        runtime.run(
            PaymentTask(
                "An evaluation scenario.", run.payee, asset, run.amount, run.key
            )
        )
        for run in runs
    )
    return outcomes, settlement


def _runtime(
    request: EvaluationRequest, settlement: SimulatedSettlement
) -> AgentRuntime:
    context = RuntimeContext(
        request.candidate.definition,
        request.payees,
        {},
        settlement,
        settlement,
        PaymentJournal(),
        request.catalog,
    )
    return AgentRuntime(RuntimeParts(ToolRunner(context), ScriptedPaymentDecider()))
