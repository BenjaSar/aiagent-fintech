from collections.abc import Mapping
from dataclasses import dataclass, field

from fintech_agent.adapters.foundry_toolkit import FoundryError
from fintech_agent.adapters.scripted_decider import ScriptedPaymentDecider
from fintech_agent.application.agent_evaluation import NoAccount
from fintech_agent.application.agent_runtime import (
    AgentDecider,
    AgentRuntime,
    RuntimeParts,
)
from fintech_agent.application.agent_tools import (
    PaymentOutcome,
    RuntimeContext,
    ToolRunner,
)
from fintech_agent.application.interaction_trace import NoTrace, RunTrace
from fintech_agent.application.payment_journal import PaymentJournal
from fintech_agent.application.sign_user_operation import SigningFailedError
from fintech_agent.composition.drill_toolbox import (
    ZERO_ADDRESS,
    AccountContext,
    DrillFailureError,
    DrillToolbox,
    ExecutionRequest,
)
from fintech_agent.composition.fleet_provisioning import ProvisionedAgent
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.payment_intent import PaymentIntent
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG

SINK_FAILURES = (FoundryError, SigningFailedError, DrillFailureError)


@dataclass(frozen=True)
class SinkParts:
    toolbox: DrillToolbox
    context: AccountContext


@dataclass(frozen=True)
class RuntimeSetup:
    toolbox: DrillToolbox
    agent: ProvisionedAgent
    payees: Mapping[str, str]
    labels: Mapping[str, str]
    journal: PaymentJournal
    trace: RunTrace = field(default_factory=NoTrace)


class DrillBalanceReader:
    def __init__(self, parts: SinkParts) -> None:
        self._parts = parts

    def balance_of(self, asset: str) -> int:
        if asset != ZERO_ADDRESS:
            raise DrillFailureError(f"this drill reads no balance for {asset}")
        return self._parts.toolbox.native_balance(self._parts.context.account)


class DrillIntentSink:
    def __init__(self, parts: SinkParts) -> None:
        self._parts = parts

    def submit(self, intent: PaymentIntent) -> PaymentOutcome:
        with self._parts.toolbox.acting_as(self._parts.context):
            return self._pay(intent)

    def _pay(self, intent: PaymentIntent) -> PaymentOutcome:
        toolbox = self._parts.toolbox
        before = toolbox.native_balance(intent.payee)
        refusal = self._submit(intent)
        if refusal is not None:
            return refusal
        if toolbox.native_balance(intent.payee) != before + intent.amount:
            return PaymentOutcome(False, "the payment did not reach the payee")
        return PaymentOutcome(True, "included on chain")

    def _submit(self, intent: PaymentIntent) -> PaymentOutcome | None:
        toolbox = self._parts.toolbox
        call_data = toolbox.session_execute_data(
            ExecutionRequest(intent.payee, b"", intent.amount)
        )
        try:
            toolbox.submit_directly(
                toolbox.quorum_signed(toolbox.direct_session_operation(call_data))
            )
        except SINK_FAILURES as refusal:
            return PaymentOutcome(False, str(refusal))
        return None


@dataclass(frozen=True)
class ReaderSetup:
    definition: AgentDefinition
    payees: Mapping[str, str]
    labels: Mapping[str, str]
    journal: PaymentJournal
    account: NoAccount
    decider: AgentDecider


def build_reader_runtime(setup: ReaderSetup) -> AgentRuntime:
    context = RuntimeContext(
        setup.definition,
        setup.payees,
        setup.labels,
        setup.account,
        setup.account,
        setup.journal,
        STAGE_ONE_PAYMENT_CATALOG,
    )
    return AgentRuntime(RuntimeParts(ToolRunner(context), setup.decider))


def build_runtime(setup: RuntimeSetup) -> AgentRuntime:
    parts = SinkParts(setup.toolbox, setup.agent.context)
    context = RuntimeContext(
        setup.agent.approved.definition,
        setup.payees,
        setup.labels,
        DrillBalanceReader(parts),
        DrillIntentSink(parts),
        setup.journal,
        STAGE_ONE_PAYMENT_CATALOG,
    )
    return AgentRuntime(
        RuntimeParts(ToolRunner(context), ScriptedPaymentDecider(), trace=setup.trace)
    )
