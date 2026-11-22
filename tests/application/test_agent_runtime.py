from dataclasses import dataclass, field

import pytest

from fintech_agent.adapters.scripted_decider import (
    ALREADY,
    PAID,
    REFUSED,
    REPORTED,
    UNREPORTED,
    DeciderError,
    ScriptedPaymentDecider,
    ScriptedReportDecider,
)
from fintech_agent.application.agent_runtime import (
    AgentAction,
    AgentRuntime,
    CallTool,
    Finish,
    PaymentTask,
    ReportTask,
    RuntimeParts,
    Situation,
)
from fintech_agent.application.agent_tools import (
    GET_BALANCE,
    GET_PAYMENT_STATUS,
    LIST_PAYEES,
    PAY_PAYEE,
    PaymentOutcome,
    ToolInvocation,
)
from fintech_agent.application.interaction_trace import (
    AgentTrace,
    InteractionTrace,
    TraceParts,
    reconstruct,
)
from fintech_agent.application.payment_journal import PaymentState
from fintech_agent.domain.session_policy import NATIVE_ASSET
from tests.application.test_agent_tools import PAYMENT_KEY, Fixture, build
from tests.definition_fixtures import PAYEE
from tests.policy_fixtures import ETHER, TARGET

AMOUNT = ETHER // 10
AGENT = "agent-one"


def task(**overrides: object) -> PaymentTask:
    fields: dict[str, object] = {
        "description": "Pay the approved supplier for invoice 1.",
        "payee": PAYEE,
        "asset": NATIVE_ASSET,
        "amount": AMOUNT,
        "idempotency_key": PAYMENT_KEY,
    }
    fields.update(overrides)
    return PaymentTask(**fields)  # type: ignore[arg-type]


def runtime(fixture: Fixture, steps: int = 8) -> AgentRuntime:
    return AgentRuntime(
        RuntimeParts(fixture.runner, ScriptedPaymentDecider(), maximum_steps=steps)
    )


def traced_runtime(fixture: Fixture, trace: AgentTrace) -> AgentRuntime:
    return AgentRuntime(
        RuntimeParts(fixture.runner, ScriptedPaymentDecider(), trace=trace)
    )


@dataclass
class LoopingDecider:
    calls: int = 0

    def decide(self, situation: Situation) -> AgentAction:
        self.calls += 1
        return CallTool(ToolInvocation(LIST_PAYEES, {}))


@dataclass
class OneShotDecider:
    invocations: list[ToolInvocation] = field(default_factory=list)

    def decide(self, situation: Situation) -> AgentAction:
        if situation.history:
            return Finish("done")
        asked = situation.task
        assert isinstance(asked, PaymentTask)
        invocation = ToolInvocation(
            PAY_PAYEE,
            {
                "payee": asked.payee,
                "asset": asked.asset,
                "amount": str(asked.amount),
                "key": asked.idempotency_key,
            },
        )
        self.invocations.append(invocation)
        return CallTool(invocation)


def test_the_agent_looks_up_its_payees_its_balance_and_then_pays() -> None:
    fixture = build()
    outcome = runtime(fixture).run(task())
    assert outcome.summary == PAID
    assert [result.name for result in outcome.results] == [
        LIST_PAYEES,
        "get_balance",
        PAY_PAYEE,
    ]
    assert not outcome.stopped_early
    assert [intent.amount for intent in fixture.sink.submitted] == [AMOUNT]
    assert fixture.journal.entry(PAYMENT_KEY).state is PaymentState.SETTLED


def test_the_payee_identifier_becomes_the_approved_address() -> None:
    fixture = build()
    runtime(fixture).run(task())
    assert [intent.payee for intent in fixture.sink.submitted] == [TARGET]


def test_running_the_same_task_again_pays_nothing_more() -> None:
    fixture = build()
    runtime(fixture).run(task())
    second = runtime(fixture).run(task())
    assert second.summary == ALREADY
    assert len(fixture.sink.submitted) == 1


def test_a_task_naming_an_unapproved_payee_reaches_no_sink() -> None:
    fixture = build()
    outcome = runtime(fixture).run(task(payee="stranger"))
    assert outcome.summary.startswith(REFUSED)
    assert fixture.sink.submitted == []
    assert fixture.journal.keys == ()


def test_a_refused_payment_is_reported_as_refused() -> None:
    fixture = build()
    fixture.sink.outcome = PaymentOutcome(False, "the hook refused it")
    outcome = runtime(fixture).run(task())
    assert outcome.summary.startswith(REFUSED)
    assert fixture.journal.entry(PAYMENT_KEY).state is PaymentState.REFUSED


def test_the_loop_is_bounded() -> None:
    fixture = build()
    decider = LoopingDecider()
    outcome = AgentRuntime(RuntimeParts(fixture.runner, decider, maximum_steps=3)).run(
        task()
    )
    assert outcome.stopped_early
    assert outcome.steps == 3
    assert decider.calls == 3
    assert fixture.sink.submitted == []


def test_a_decider_that_pays_at_once_still_goes_through_the_tools() -> None:
    fixture = build()
    decider = OneShotDecider()
    outcome = AgentRuntime(RuntimeParts(fixture.runner, decider)).run(task())
    assert outcome.summary == "done"
    assert len(decider.invocations) == 1
    assert [intent.amount for intent in fixture.sink.submitted] == [AMOUNT]


def test_the_runtime_returns_what_the_tools_said() -> None:
    fixture = build()
    outcome = runtime(fixture).run(task())
    assert outcome.results[0].lines[0].startswith(PAYEE)
    assert outcome.results[2].lines[0].startswith(PAYMENT_KEY)


def test_a_traced_run_records_the_task_every_tool_and_the_summary() -> None:
    fixture = build()
    trace = InteractionTrace(lambda: 1)

    traced_runtime(fixture, AgentTrace(TraceParts(trace, AGENT))).run(task())

    assert [span.step for span in reconstruct(trace.spans)] == [
        "task",
        LIST_PAYEES,
        GET_BALANCE,
        PAY_PAYEE,
        "finished",
    ]


def test_a_traced_run_names_the_agent_and_keeps_one_chain() -> None:
    fixture = build()
    trace = InteractionTrace(lambda: 1)

    traced_runtime(fixture, AgentTrace(TraceParts(trace, AGENT))).run(task())

    assert {span.agent_identifier for span in trace.spans} == {AGENT}
    assert [span.parent for span in trace.spans] == ["", "s1", "s2", "s3", "s4"]


def test_a_traced_run_records_the_outcome_of_each_tool() -> None:
    fixture = build()
    trace = InteractionTrace(lambda: 1)

    traced_runtime(fixture, AgentTrace(TraceParts(trace, AGENT))).run(task())

    payment = next(span for span in trace.spans if span.step == PAY_PAYEE)
    assert payment.detail


def test_a_run_that_reaches_its_step_limit_is_recorded_as_stopped() -> None:
    fixture = build()
    trace = InteractionTrace(lambda: 1)
    parts = RuntimeParts(
        fixture.runner,
        LoopingDecider(),
        maximum_steps=2,
        trace=AgentTrace(TraceParts(trace, AGENT)),
    )

    AgentRuntime(parts).run(task())

    assert [span.step for span in trace.spans][-1] == "stopped"


def test_a_reader_reports_that_it_found_nothing() -> None:
    fixture = build()
    runtime = AgentRuntime(RuntimeParts(fixture.runner, ScriptedReportDecider()))

    outcome = runtime.run(ReportTask("Report on a payment.", PAYEE, PAYMENT_KEY))

    assert outcome.summary.startswith(UNREPORTED)
    assert [result.name for result in outcome.results] == [
        LIST_PAYEES,
        GET_PAYMENT_STATUS,
    ]


def test_a_report_names_what_the_reader_found() -> None:
    fixture = build()
    AgentRuntime(RuntimeParts(fixture.runner, ScriptedPaymentDecider())).run(task())

    reporting = AgentRuntime(RuntimeParts(fixture.runner, ScriptedReportDecider()))
    outcome = reporting.run(ReportTask("Report on a payment.", PAYEE, PAYMENT_KEY))

    assert outcome.summary.startswith(REPORTED)
    assert PAYMENT_KEY in outcome.summary


def test_the_payment_decider_refuses_a_report_task() -> None:
    fixture = build()
    runtime = AgentRuntime(RuntimeParts(fixture.runner, ScriptedPaymentDecider()))

    with pytest.raises(DeciderError, match="payment task"):
        runtime.run(ReportTask("Report on a payment.", PAYEE, PAYMENT_KEY))
