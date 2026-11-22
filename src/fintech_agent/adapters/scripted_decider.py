from fintech_agent.application.agent_runtime import (
    AgentAction,
    CallTool,
    Finish,
    PaymentTask,
    Situation,
)
from fintech_agent.application.agent_tools import (
    GET_BALANCE,
    GET_PAYMENT_STATUS,
    LIST_PAYEES,
    PAY_PAYEE,
    ToolInvocation,
)

PAID = "the payment was made"
REFUSED = "the payment was refused"
ALREADY = "the payment had already been made"
REPORTED = "the report was written"
UNREPORTED = "the report could not be written"


class DeciderError(Exception):
    pass


class ScriptedPaymentDecider:
    def decide(self, situation: Situation) -> AgentAction:
        task = _payment_task(situation)
        steps = len(situation.history)
        if steps == 0:
            return CallTool(ToolInvocation(LIST_PAYEES, {}))
        if steps == 1:
            return CallTool(ToolInvocation(GET_BALANCE, {"asset": task.asset}))
        if steps == 2:
            return CallTool(self._payment(task))
        return Finish(_summary(situation))

    def _payment(self, task: PaymentTask) -> ToolInvocation:
        return ToolInvocation(
            PAY_PAYEE,
            {
                "payee": task.payee,
                "asset": task.asset,
                "amount": str(task.amount),
                "key": task.idempotency_key,
            },
        )


class ScriptedReportDecider:
    def decide(self, situation: Situation) -> AgentAction:
        steps = len(situation.history)
        if steps == 0:
            return CallTool(ToolInvocation(LIST_PAYEES, {}))
        if steps == 1:
            key = situation.task.idempotency_key
            return CallTool(ToolInvocation(GET_PAYMENT_STATUS, {"key": key}))
        return Finish(_report(situation))


def _payment_task(situation: Situation) -> PaymentTask:
    task = situation.task
    if not isinstance(task, PaymentTask):
        raise DeciderError("this decider needs a payment task")
    return task


def _report(situation: Situation) -> str:
    payees, status = situation.history[0], situation.history[1]
    failed = next((held for held in (payees, status) if held.failed), None)
    if failed is not None:
        return f"{UNREPORTED}: {failed.lines[0]}"
    return f"{REPORTED}: payees {len(payees.lines)}, {status.lines[0]}"


def _summary(situation: Situation) -> str:
    payment = situation.history[-1]
    if payment.failed:
        return f"{REFUSED}: {payment.lines[0]}"
    if any("already" in line for line in payment.lines):
        return ALREADY
    return PAID
