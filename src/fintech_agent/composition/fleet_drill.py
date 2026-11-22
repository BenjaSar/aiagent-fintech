from dataclasses import dataclass, replace
from pathlib import Path

from fintech_agent.adapters.foundry_toolkit import (
    ContractCall,
    FoundryError,
    FunctionCall,
)
from fintech_agent.adapters.http_enclave import HttpEnclaveGateway
from fintech_agent.adapters.scripted_decider import (
    ALREADY,
    PAID,
    REFUSED,
    REPORTED,
    ScriptedPaymentDecider,
    ScriptedReportDecider,
)
from fintech_agent.application.agent_development import Attempt, DevelopmentOutcome
from fintech_agent.application.agent_evaluation import STATUS_TOOL, NoAccount
from fintech_agent.application.agent_lifecycle import LifecycleState
from fintech_agent.application.agent_runtime import (
    AgentDecider,
    AgentRuntime,
    PaymentTask,
    ReportTask,
    RuntimeOutcome,
)
from fintech_agent.application.agent_tools import PAY_PAYEE
from fintech_agent.application.fleet_ledger import (
    FleetBudget,
    FleetBudgetError,
    FleetLedger,
    FleetMember,
)
from fintech_agent.application.halt_switch import HaltOrder, HaltScope
from fintech_agent.application.interaction_trace import (
    ROOT,
    AgentTrace,
    Crossing,
    InteractionTrace,
    Span,
    TraceParts,
    crossings,
    per_agent_order,
    reconstruct,
)
from fintech_agent.application.payment_journal import PaymentJournal
from fintech_agent.application.ports import EnclaveRefusedError
from fintech_agent.application.provenance_log import ProvenanceEvent
from fintech_agent.application.renewal_scheduler import ScheduleRun, TickReport
from fintech_agent.application.sign_user_operation import SigningFailedError
from fintech_agent.composition.drill_toolbox import (
    ETHER,
    VALIDATOR_MODULE_TYPE,
    ZERO_ADDRESS,
    AccountContext,
    DrillFailureError,
    DrillToolbox,
    ExecutionRequest,
    KeyedOperation,
    ModuleInstall,
    UnsignedRequest,
    is_rejection,
)
from fintech_agent.composition.fleet_provisioning import (
    PAYEE_IDENTIFIER,
    AgentPlan,
    AnalysisPlan,
    AuthoredAgent,
    DevelopmentCase,
    FleetProvisioning,
    LearningProposer,
    ProvisionedAgent,
)
from fintech_agent.composition.local_enclaves import (
    EnclaveSet,
    endpoints_of,
    tenant_is_reachable,
)
from fintech_agent.composition.rotation_local import (
    PID_FILE_NAME,
    FleetConfiguration,
)
from fintech_agent.composition.runtime_local import (
    ReaderSetup,
    RuntimeSetup,
    build_reader_runtime,
    build_runtime,
)
from fintech_agent.domain.agent_approval import (
    NO_AUTHORITY,
    ApprovalBinding,
    agent_fingerprint,
)
from fintech_agent.domain.agent_definition import AgentDefinition, AgentType
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.policy_approval import policy_digest
from fintech_agent.domain.user_operation import UserOperation

FIRST_AGENT = AgentPlan("agent-one", ETHER, ETHER // 4)
HALT_AGENT = AgentPlan("agent-halt", ETHER // 2, ETHER // 10)
ANALYSIS_AGENT = AnalysisPlan("agent-report")
LOOP_AGENT = AgentPlan("agent-loop", ETHER // 2, ETHER // 10)
WIDENING_AGENT = AgentPlan("agent-widen", ETHER // 2, ETHER // 10)
SECOND_AGENT = AgentPlan("agent-two", ETHER // 2, ETHER // 10)
UNFUNDABLE_AGENT = AgentPlan("agent-three", ETHER, ETHER // 5)
UNFUNDABLE_ACCOUNT = "0x" + "5c" * 20
UNAPPROVED_PAYEE = "0x" + "7a" * 20
APPROVED_TOTAL = ETHER // 2
SMALL_PAYMENT = ETHER // 20
OVER_BALANCE_PAYMENT = ETHER // 2
SECOND_PAYMENT = ETHER // 100
RUNTIME_PAYMENT = ETHER // 50
CAP_SIGNATURE = "capOf(address,address)(uint256)"
PAYEE_SIGNATURE = "isPayeeAllowed(address,address)(bool)"
RUNTIME_KEY = "drill-invoice-0001"
STRANGER_KEY = "drill-invoice-0002"
FOREIGN_ACCOUNT_REFUSAL = "not for the policy's account"
PAYMENT_REFUSALS = ("not allowed", "cannot carry call data")
EXPECTED_ATTEMPTS = 2
SHORT_LEAD_SECONDS = 3_600
FLEET_LEAD_SECONDS = 86_400
SCHEDULER_INTERVAL_SECONDS = 60
TRACE_FIRST_KEY = "drill-invoice-0101"
READER_KEY = "drill-invoice-0103"
TRACE_SECOND_KEY = "drill-invoice-0102"
TRACE_PAYMENT = ETHER // 100
EXPECTED_CROSSINGS = 2
EXPECTED_TENANTS = 3
ENCLAVE_PROCESSES = 3
UNAUTHORIZED = "unauthorized"
FIRST_ENCLAVE = 1
HALT_CHALLENGE = b"liveness"
FLEET_HALT_REASON = "an incident is open in the drill"
AGENT_HALT_REASON = "a review of this agent is open"
WIDENING_ESCALATION = "would widen its authority"
EXPECTED_EVENTS = [
    ProvenanceEvent.GENERATED,
    ProvenanceEvent.DERIVED,
    ProvenanceEvent.EVALUATED,
    ProvenanceEvent.APPROVED,
    ProvenanceEvent.INSTALLED,
]
RENEWED_EVENTS = [*EXPECTED_EVENTS, ProvenanceEvent.RENEWED]
RETIRED_EVENTS = [
    *EXPECTED_EVENTS,
    ProvenanceEvent.RENEWED,
    ProvenanceEvent.RETIRED,
]
ANALYSIS_EVENTS = [
    ProvenanceEvent.GENERATED,
    ProvenanceEvent.EVALUATED,
    ProvenanceEvent.APPROVED,
]
SPENDING_HEADING = "Spending limits:"


@dataclass(frozen=True)
class FleetSetup:
    fleet_configuration: FleetConfiguration


@dataclass(frozen=True)
class Observation:
    balance: int
    window_spend: int


@dataclass(frozen=True)
class Payment:
    agent: ProvisionedAgent
    amount: int


@dataclass(frozen=True)
class TracedRun:
    agent: ProvisionedAgent
    trace: InteractionTrace
    parent: str
    idempotency_key: str
    expected: str


class FleetChecks:
    def __init__(self, toolbox: DrillToolbox, setup: FleetSetup) -> None:
        self._toolbox = toolbox
        self._provisioning = FleetProvisioning(toolbox, setup.fleet_configuration)
        self._ledger = FleetLedger(FleetBudget(ZERO_ADDRESS, APPROVED_TOTAL))
        self._held: UserOperation | None = None
        self._journal = PaymentJournal()
        self._journals: dict[str, PaymentJournal] = {}

    def treasury_and_two_agents_are_provisioned(self) -> str:
        treasury = self._provisioning.provision_treasury()
        self._provision(FIRST_AGENT)
        self._provision(SECOND_AGENT)
        self._require_distinct_accounts(treasury)
        self._require_own_sessions()
        self._require_funded_as_approved()
        self._require_without_session(treasury)
        self._require_budget_is_bounded()
        return (
            "the treasury holds the funds and no session authority; two agents hold "
            "their own accounts, group keys and derived policies, funded with "
            f"{self._ledger.total_funded} of {APPROVED_TOTAL} wei approved"
        )

    def agents_cannot_use_each_others_authority(self) -> str:
        first, second = self._agent(FIRST_AGENT), self._agent(SECOND_AGENT)
        self._require_foreign_quorum_refused(first, second)
        self._require_foreign_signature_rejected(first, second)
        self._require_independent_windows(first, second)
        return (
            "the second agent's enclaves refuse to sign for the first account, a "
            "signature from their quorum is rejected on chain with AA24, and spending "
            "by one agent leaves the other's rolling window untouched"
        )

    def an_agent_cannot_spend_more_than_it_was_funded(self) -> str:
        agent = self._agent(FIRST_AGENT)
        before = self._observe(agent)
        self._require_over_balance_but_inside_cap(before)
        self._pay(Payment(agent, OVER_BALANCE_PAYMENT))
        self._require_unchanged(agent, before)
        self._pay(Payment(agent, SMALL_PAYMENT))
        self._require_spent(Payment(agent, SMALL_PAYMENT), before)
        return (
            f"the enclaves signed {OVER_BALANCE_PAYMENT} wei because it is inside the "
            f"cap, and the chain refused it because the account holds only "
            f"{before.balance} wei; nothing moved and the window stayed at "
            f"{before.window_spend}; a payment within the balance then executed"
        )

    def every_agent_runs_under_a_bound_approval(self) -> str:
        provenance = self._provisioning.provenance
        provenance.verify()
        for agent in self._both():
            self._require_bound(agent)
        self._require_approvals_are_not_interchangeable()
        return (
            "the provenance chain verifies; each agent has a generation, a "
            "derivation, an evaluation, an approval and an installation recorded; the "
            "fingerprint recomputed from the "
            "recorded definition, the derived policy and the key the validator holds "
            "is the one the owner approved"
        )

    def a_candidate_that_fails_evaluation_never_reaches_an_approval(self) -> str:
        provenance = self._provisioning.provenance
        before = len(provenance.entries)
        refusal = self._provisioning.refuse_unevaluable_candidate(SECOND_AGENT)
        if len(provenance.entries) != before:
            raise DrillFailureError("a refused candidate was written to the record")
        if STATUS_TOOL not in refusal:
            raise DrillFailureError(f"it was refused for another reason: {refusal}")
        self._require_unchanged_authority()
        return (
            "a candidate that cannot check a retry failed evaluation and was refused "
            f"promotion ({refusal.split(':')[-1].strip()}); nothing was recorded and "
            "the agent's cap and payee on chain are unchanged"
        )

    def an_overstepping_proposal_never_becomes_an_authority(self) -> str:
        provenance = self._provisioning.provenance
        before = len(provenance.entries)
        refusal = self._provisioning.refuse_overstepping_generation(SECOND_AGENT)
        if len(provenance.entries) != before:
            raise DrillFailureError("a refused generation was written to the record")
        self._require_unchanged_authority()
        return (
            f"a proposal that doubled its own cap was refused ({refusal}); nothing was "
            "recorded and the agent's cap and payee on chain are unchanged"
        )

    def a_payment_agent_can_only_pay_an_approved_payee(self) -> str:
        agent = self._agent(FIRST_AGENT)
        self._require_enclaves_refuse(agent, self._payment_data_to(UNAPPROVED_PAYEE))
        before = self._observe(agent)
        self._force(agent, self._payment_data_to(UNAPPROVED_PAYEE))
        self._require_unchanged(agent, before)
        if self._toolbox.native_balance(UNAPPROVED_PAYEE) != 0:
            raise DrillFailureError("value reached an address that is not a payee")
        self._pay(Payment(agent, SMALL_PAYMENT))
        self._require_spent(Payment(agent, SMALL_PAYMENT), before)
        return (
            "the enclaves refuse to sign a payment to an address the owner never "
            "approved, the hook refuses the same payment when a compromised quorum "
            "forces it on chain, and the approved payee is paid"
        )

    def a_payment_agent_cannot_attach_call_data(self) -> str:
        toolbox = self._toolbox
        agent = self._agent(FIRST_AGENT)
        self._require_enclaves_refuse(agent, toolbox.session_ping_data())
        before = self._observe(agent)
        pings = toolbox.pings()
        self._force(agent, toolbox.session_ping_data())
        self._require_unchanged(agent, before)
        if toolbox.pings() != pings:
            raise DrillFailureError("the agent called a function on its payee")
        self._pay(Payment(agent, SMALL_PAYMENT))
        self._require_spent(Payment(agent, SMALL_PAYMENT), before)
        return (
            "the enclaves refuse to sign a call that carries call data, the hook "
            "refuses it when a compromised quorum forces it on chain, the payee's "
            "function is never called, and a value-only payment still works"
        )

    def the_runtime_pays_an_approved_payee_once(self) -> str:
        agent = self._agent(SECOND_AGENT)
        runtime = self._runtime(agent)
        before = self._observe(agent)
        self._require_summary(runtime.run(self._task()), PAID)
        self._require_spent(Payment(agent, RUNTIME_PAYMENT), before)
        settled = self._observe(agent)
        self._require_summary(runtime.run(self._task()), ALREADY)
        self._require_unchanged(agent, settled)
        if self._journal.settled_total(ZERO_ADDRESS) != RUNTIME_PAYMENT:
            raise DrillFailureError("the journal totals more than one payment")
        self._require_stranger_never_reaches_the_chain(agent, runtime)
        return (
            f"the agent listed its payees, read its balance and paid {RUNTIME_PAYMENT} "
            "wei to the approved payee; the same task again paid nothing more; a task "
            "naming an unapproved payee was refused before any operation was built"
        )

    def a_failing_candidate_is_iterated_inside_its_budget(self) -> str:
        provisioning = self._provisioning
        before = len(provisioning.provenance.entries)
        developed = provisioning.develop_agent(
            DevelopmentCase(LOOP_AGENT, LearningProposer(widens=False))
        )
        outcome = developed.outcome
        accepted = self._require_accepted(outcome)
        self._require_a_failed_opening(outcome)
        self._require_feedback_named_the_gap(developed.feedback)
        self._require_inside_the_budget(LOOP_AGENT, outcome)
        if len(provisioning.provenance.entries) != before:
            raise DrillFailureError("an unapproved iteration was written to the record")
        limits = provisioning.lifecycle.limits
        return (
            f"the loop generated, failed, iterated and accepted on attempt "
            f"{len(outcome.attempts)} of {limits.maximum_attempts} for "
            f"{outcome.cost_micros} of {limits.maximum_cost_micros} micros; the second "
            "attempt was handed the first's failures and answered them without asking "
            f"for more authority ({accepted.candidate.definition.assets[0].cap} wei, "
            "the cap the first attempt proposed); nothing was recorded"
        )

    def a_widening_iteration_is_escalated_to_the_owner(self) -> str:
        provisioning = self._provisioning
        before = len(provisioning.provenance.entries)
        developed = provisioning.develop_agent(
            DevelopmentCase(WIDENING_AGENT, LearningProposer(widens=True))
        )
        outcome = developed.outcome
        if outcome.accepted is not None:
            raise DrillFailureError("an iteration that widened authority was accepted")
        if WIDENING_ESCALATION not in outcome.escalation:
            raise DrillFailureError(
                f"it stopped for another reason: {outcome.escalation}"
            )
        if len(provisioning.provenance.entries) != before:
            raise DrillFailureError("a refused iteration was written to the record")
        self._require_standing(WIDENING_AGENT, LifecycleState.REFUSED)
        self._require_unwidened_standing(WIDENING_AGENT, outcome)
        self._require_unchanged_authority()
        return (
            "the loop refused its own second attempt and escalated instead of "
            f"installing it ({outcome.escalation.split(':')[-1].strip()}); the standing "
            "authority is still the first attempt's, nothing was recorded and the "
            "agent's cap and payee on chain are unchanged"
        )

    def a_session_renews_on_schedule_without_a_human(self) -> str:
        provisioning = self._provisioning
        standing = {agent.agent_identifier: agent for agent in self._both()}
        caps = {name: self._cap_on_chain(held) for name, held in standing.items()}
        self._require_nothing_due()
        due = self._require_due(FIRST_AGENT.agent_identifier)
        refusal = provisioning.refuse_widened_renewal(self._agent(FIRST_AGENT))
        report = provisioning.run_scheduler(
            ScheduleRun(1, SCHEDULER_INTERVAL_SECONDS, FLEET_LEAD_SECONDS)
        )
        self._require_scheduled(report, due)
        for name, held in standing.items():
            self._require_renewed(held, provisioning.agent(name))
        self._require_caps_unchanged(caps)
        provisioning.provenance.verify()
        for name in due:
            self._require_events(name, RENEWED_EVENTS)
        return (
            f"with a {SHORT_LEAD_SECONDS} second lead the scheduler reported nothing "
            f"due; with the fleet's {FLEET_LEAD_SECONDS} second lead it reported "
            f"{len(due)} sessions due; a renewal that doubled the cap was refused "
            f"({refusal.split(':')[-1].strip()}), and one tick of the scheduler renewed "
            f"all {len(report.renewed)} of them unattended: a fresh DKG and tenancy per "
            "agent, a new key and window on chain, the old quorum evicted, the caps "
            "untouched and every new key recorded"
        )

    def the_trace_reconstructs_a_multi_agent_sequence(self) -> str:
        first, second = self._both()
        trace = InteractionTrace(self._toolbox.chain_time)
        before = (self._observe(first), self._observe(second))
        opening = self._traced_run(TracedRun(first, trace, ROOT, TRACE_FIRST_KEY, PAID))
        answer = self._traced_run(
            TracedRun(second, trace, opening, TRACE_SECOND_KEY, PAID)
        )
        self._traced_run(TracedRun(first, trace, answer, TRACE_FIRST_KEY, ALREADY))
        sequence = reconstruct(trace.spans)
        crossed = self._require_crossings(trace, sequence)
        self._require_payment_order(sequence)
        self._require_spent(Payment(first, TRACE_PAYMENT), before[0])
        self._require_spent(Payment(second, TRACE_PAYMENT), before[1])
        return (
            f"three runs by two agents left {len(sequence)} spans in one causal chain: "
            f"{first.agent_identifier} paid {TRACE_PAYMENT} wei, "
            f"{second.agent_identifier} paid because that payment settled, and "
            f"{first.agent_identifier} answered that it had already paid; the chain "
            f"crosses between the agents {len(crossed)} times "
            f"({crossed[0].cause} to {crossed[0].effect}, then back), which neither "
            "agent's own record shows"
        )

    def the_fleet_shares_one_enclave_set(self) -> str:
        self._provision(HALT_AGENT)
        agents = (*self._both(), self._agent(HALT_AGENT))
        root = self._require_one_set(agents)
        tenants = self._require_own_tenancies(agents)
        processes = len((root / PID_FILE_NAME).read_text().split())
        refusal = self._require_cross_tenant_refusal(agents[-1], agents[0])
        self._pay(Payment(self._agent(HALT_AGENT), SECOND_PAYMENT))
        return (
            f"{len(agents)} agents run on one set of {processes} enclave processes as "
            f"the separate tenancies {', '.join(tenants)}; each holds its own key "
            "share, policy and ledger, a tenant presenting its own token for another "
            f"tenant is refused ({refusal}), and the newest agent pays its own payee "
            "through the shared quorum"
        )

    def a_fleet_halt_stops_every_agent(self) -> str:
        provisioning = self._provisioning
        halted, witness = self._agent(HALT_AGENT), self._agent(SECOND_AGENT)
        self._pay(Payment(halted, SECOND_PAYMENT))
        standing = (self._observe(halted), self._observe(witness))
        provisioning.halt_the_fleet(self._order(HaltScope.FLEET, FLEET_HALT_REASON))
        self._require_every_node_halted()
        reasons = (self._require_halted(halted), self._require_halted(witness))
        self._require_unchanged(halted, standing[0])
        self._require_unchanged(witness, standing[1])
        provisioning.resume_the_fleet(self._order(HaltScope.FLEET, "the drill is over"))
        self._pay(Payment(halted, SECOND_PAYMENT))
        return (
            f"the halt was written straight into the storage of all "
            f"{ENCLAVE_PROCESSES} enclaves, with no service in the path; every agent "
            f"was then refused ({reasons[0]}), the "
            "quorum signed nothing for either agent, no balance or window moved, and "
            "both pay again once the halt is lifted"
        )

    def a_halted_agent_can_still_be_revoked_and_recovered(self) -> str:
        provisioning = self._provisioning
        halted, witness = self._agent(HALT_AGENT), self._agent(SECOND_AGENT)
        held = self._signed_payment(halted)
        provisioning.halt_one_agent(
            halted, self._order(HaltScope.AGENT, AGENT_HALT_REASON)
        )
        reason = self._require_halted(halted)
        self._require_still_paying(witness)
        provisioning.halt_the_fleet(self._order(HaltScope.FLEET, FLEET_HALT_REASON))
        self._revoke_directly(halted)
        with self._toolbox.acting_as(halted.context):
            self._require_no_validator()
        self._require_rejected(halted, held)
        self._retire_in_place(HALT_AGENT)
        provisioning.resume_one_agent(
            halted, self._order(HaltScope.AGENT, "the review is closed")
        )
        provisioning.resume_the_fleet(self._order(HaltScope.FLEET, "the drill is over"))
        self._require_still_paying(witness)
        return (
            f"an agent halt stopped one agent alone ({reason}) while the other paid on; "
            "with the whole fleet then halted the owner still revoked that agent "
            "through a self-submitted handleOps, with no bundler and no enclave in the "
            "path, and a payment its quorum had signed now fails with AA24; lifting the "
            "halt leaves the rest of the fleet paying"
        )

    def a_retired_agent_keeps_nothing(self) -> str:
        provisioning = self._provisioning
        agent = self._agent(SECOND_AGENT)
        identifier = SECOND_AGENT.agent_identifier
        treasury = self._toolbox.native_balance(provisioning.treasury)
        remaining = self._ledger.remaining
        held = self._signed_payment(agent)
        allowance = self._toolbox.native_balance(agent.context.account)
        self._require_a_live_allowance(allowance)
        outcome = provisioning.retire_agent(agent, self._ledger)
        self._require_swept(agent, outcome.swept, allowance)
        self._require_still_tenanted(self._agent(FIRST_AGENT))
        if self._toolbox.native_balance(provisioning.treasury) != treasury + allowance:
            raise DrillFailureError("the allowance did not reach the treasury")
        if self._ledger.remaining != remaining + allowance:
            raise DrillFailureError("the unspent allowance was not freed")
        self._require_rejected(agent, held)
        self._require_standing(SECOND_AGENT, LifecycleState.RETIRED)
        provisioning.provenance.verify()
        self._require_events(identifier, RETIRED_EVENTS)
        return (
            f"the validator is uninstalled, {outcome.swept} wei of unspent allowance "
            "went back to the treasury and freed the same amount of the approved total, "
            "its enclave tenancy is evicted and unreachable while the rest of the fleet "
            "keeps its own, a payment its quorum signed beforehand now fails with AA24, "
            "and the retirement is on the record"
        )

    def an_analysis_agent_is_approved_and_holds_nothing(self) -> str:
        provisioning = self._provisioning
        refused = provisioning.refuse_paying_reader(ANALYSIS_AGENT)
        remaining = self._ledger.remaining
        authored = provisioning.author_analysis_agent(ANALYSIS_AGENT)
        self._require_no_authority(authored)
        derivation = provisioning.refuse_reader_derivation(authored)
        keyed = provisioning.refuse_reader_with_a_key(authored)
        self._require_the_screen_said_so()
        reported = self._require_the_reader_reports(authored)
        self._require_the_reader_cannot_pay(authored)
        if self._ledger.remaining != remaining:
            raise DrillFailureError("authoring a reader moved the fleet's budget")
        provisioning.provenance.verify()
        self._require_events(ANALYSIS_AGENT.agent_identifier, ANALYSIS_EVENTS)
        return (
            "a generator that proposed a paying tool for a reader was refused "
            f"({refused.split(':')[-1].strip()}); the reader the owner approved holds "
            "no account, no session key, no payee, no asset and no budget, showing "
            f"it with a key is refused ({keyed.split(',')[0]}), its "
            "fingerprint binds its definition alone, deriving a policy for it is "
            f"refused ({derivation.split(',')[0]}), and its screen says so; it read the "
            f"fleet's payees and one payment and reported ({reported.split(':')[-1].strip()}), "
            "and a payment task was refused at the tool boundary with no account touched"
        )

    def _require_no_authority(self, authored: AuthoredAgent) -> None:
        definition = authored.definition
        if definition.agent_type is not AgentType.ANALYSIS:
            raise DrillFailureError(f"it is a {definition.agent_type.value} agent")
        if definition.payees or definition.assets or definition.budget is not None:
            raise DrillFailureError("the reader declares a payee, asset or budget")
        approved = authored.approved
        if approved.policy is not None:
            raise DrillFailureError("the reader was approved with a session policy")
        binding = approved.binding
        if binding.policy_digest or binding.group_public_key:
            raise DrillFailureError("the reader's approval binds a policy or a key")
        expected = ApprovalBinding(definition_digest(definition), "", b"")
        if agent_fingerprint(expected) != approved.fingerprint:
            raise DrillFailureError("the reader's fingerprint is not its definition's")

    def _require_the_screen_said_so(self) -> None:
        shown = self._provisioning.shown
        if NO_AUTHORITY not in shown:
            raise DrillFailureError("the screen did not say it holds no authority")
        if SPENDING_HEADING in shown:
            raise DrillFailureError("the screen showed spending limits for a reader")

    def _require_the_reader_reports(self, authored: AuthoredAgent) -> str:
        account = NoAccount()
        runtime = self._reader_runtime(authored, account, ScriptedReportDecider())
        outcome = runtime.run(
            ReportTask(
                "Report on the drill's payments.", PAYEE_IDENTIFIER, TRACE_FIRST_KEY
            )
        )
        if not outcome.summary.startswith(REPORTED):
            raise DrillFailureError(f"the reader reported {outcome.summary!r}")
        if account.reached:
            raise DrillFailureError("the reader reached an account")
        return outcome.summary

    def _require_the_reader_cannot_pay(self, authored: AuthoredAgent) -> None:
        account = NoAccount()
        journal = self._journal_of(FIRST_AGENT.agent_identifier)
        before = journal.keys
        runtime = self._reader_runtime(authored, account, ScriptedPaymentDecider())
        outcome = runtime.run(self._trace_task(READER_KEY))
        if not outcome.summary.startswith(REFUSED):
            raise DrillFailureError(f"the reader reported {outcome.summary!r}")
        if account.reached:
            raise DrillFailureError("the reader reached an account to pay from")
        if journal.keys != before:
            raise DrillFailureError("the refused payment was written to the journal")

    def _reader_runtime(
        self, authored: AuthoredAgent, account: NoAccount, decider: AgentDecider
    ) -> AgentRuntime:
        return build_reader_runtime(
            ReaderSetup(
                authored.definition,
                self._provisioning.payee_addresses,
                self._provisioning.payee_labels,
                self._journal_of(FIRST_AGENT.agent_identifier),
                account,
                decider,
            )
        )

    def _require_accepted(self, outcome: DevelopmentOutcome) -> Attempt:
        if outcome.accepted is None:
            raise DrillFailureError(
                f"the loop accepted no candidate: {outcome.escalation}"
            )
        return outcome.accepted

    def _require_a_failed_opening(self, outcome: DevelopmentOutcome) -> None:
        if len(outcome.attempts) != EXPECTED_ATTEMPTS:
            raise DrillFailureError(
                f"the loop took {len(outcome.attempts)} attempts, not "
                f"{EXPECTED_ATTEMPTS}"
            )
        if outcome.attempts[0].report.passed:
            raise DrillFailureError(
                "the check is vacuous: the first attempt already passed"
            )

    def _require_feedback_named_the_gap(self, feedback: tuple[str, ...]) -> None:
        if not any(STATUS_TOOL in line for line in feedback):
            raise DrillFailureError(
                f"the second attempt was handed {feedback} instead of the failure"
            )

    def _require_inside_the_budget(
        self, plan: AgentPlan, outcome: DevelopmentOutcome
    ) -> None:
        limits = self._provisioning.lifecycle.limits
        if len(outcome.attempts) > limits.maximum_attempts:
            raise DrillFailureError("the loop ran past its attempt budget")
        if outcome.cost_micros > limits.maximum_cost_micros:
            raise DrillFailureError("the loop ran past its cost ceiling")
        standing = self._provisioning.lifecycle.standing_of(plan.agent_identifier)
        if standing.attempts != len(outcome.attempts):
            raise DrillFailureError(
                f"the lifecycle counted {standing.attempts} attempts"
            )

    def _require_standing(self, plan: AgentPlan, state: LifecycleState) -> None:
        standing = self._provisioning.lifecycle.standing_of(plan.agent_identifier)
        if standing.state is not state:
            raise DrillFailureError(
                f"{plan.agent_identifier} is {standing.state.value}, not {state.value}"
            )

    def _require_unwidened_standing(
        self, plan: AgentPlan, outcome: DevelopmentOutcome
    ) -> None:
        standing = self._provisioning.lifecycle.standing_of(plan.agent_identifier)
        if standing.definition != outcome.attempts[0].candidate.definition:
            raise DrillFailureError("the widened definition became the standing one")

    def _require_nothing_due(self) -> None:
        due = self._provisioning.due(SHORT_LEAD_SECONDS)
        if due:
            raise DrillFailureError(
                f"the check is vacuous: {due} is already due for renewal"
            )

    def _require_due(self, identifier: str) -> tuple[str, ...]:
        due = self._provisioning.due(FLEET_LEAD_SECONDS)
        if identifier not in due:
            raise DrillFailureError(
                f"the scheduler sees {due} as due, not {identifier}"
            )
        return due

    def _require_scheduled(self, report: TickReport, due: tuple[str, ...]) -> None:
        if report.escalated:
            raise DrillFailureError(f"the scheduler escalated {report.escalated}")
        if report.renewed != due:
            raise DrillFailureError(
                f"the scheduler renewed {report.renewed}, not {due}"
            )

    def _require_renewed(
        self, before: ProvisionedAgent, after: ProvisionedAgent
    ) -> None:
        policy = after.context.policy
        if policy is None:
            raise DrillFailureError("the renewal has no derived policy")
        if after.group_public_key == before.group_public_key:
            raise DrillFailureError("the renewal kept the old session key")
        if after.context.tenant == before.context.tenant:
            raise DrillFailureError("the renewal kept the old enclave tenancy")
        with self._toolbox.acting_as(after.context):
            stored = self._toolbox.stored_session_key()
            window = self._toolbox.stored_session_window()
        if stored != after.group_public_key:
            raise DrillFailureError("the validator does not hold the renewed key")
        if window != (policy.valid_after, policy.valid_until):
            raise DrillFailureError(f"the validator holds the window {window}")

    def _require_caps_unchanged(self, caps: dict[str, int]) -> None:
        for identifier, cap in caps.items():
            found = self._cap_on_chain(self._provisioning.agent(identifier))
            if found != cap:
                raise DrillFailureError(
                    f"the cap of {identifier} on chain is {found}, not {cap}"
                )

    def _traced_run(self, run: TracedRun) -> str:
        identifier = run.agent.agent_identifier
        recorder = AgentTrace(TraceParts(run.trace, identifier, run.parent))
        runtime = build_runtime(
            RuntimeSetup(
                self._toolbox,
                run.agent,
                self._provisioning.payee_addresses,
                self._provisioning.payee_labels,
                self._journal_of(identifier),
                recorder,
            )
        )
        self._require_summary(
            runtime.run(self._trace_task(run.idempotency_key)), run.expected
        )
        return recorder.last

    def _journal_of(self, agent_identifier: str) -> PaymentJournal:
        if agent_identifier not in self._journals:
            self._journals[agent_identifier] = PaymentJournal()
        return self._journals[agent_identifier]

    def _trace_task(self, idempotency_key: str) -> PaymentTask:
        return PaymentTask(
            "Pay the development target for the fleet sequence.",
            PAYEE_IDENTIFIER,
            ZERO_ADDRESS,
            TRACE_PAYMENT,
            idempotency_key,
        )

    def _require_crossings(
        self, trace: InteractionTrace, sequence: tuple[Span, ...]
    ) -> tuple[Crossing, ...]:
        crossed = crossings(trace.spans)
        if len(crossed) != EXPECTED_CROSSINGS:
            raise DrillFailureError(
                f"the chain crosses between agents {len(crossed)} times"
            )
        if {crossing.cause for crossing in crossed} != {
            crossing.effect for crossing in crossed
        }:
            raise DrillFailureError("the crossings do not run both ways")
        if per_agent_order(trace.spans) == sequence:
            raise DrillFailureError(
                "the check is vacuous: each agent's own record is already the sequence"
            )
        return crossed

    def _require_payment_order(self, sequence: tuple[Span, ...]) -> None:
        payments = [
            span.agent_identifier for span in sequence if span.step == PAY_PAYEE
        ]
        expected = [
            FIRST_AGENT.agent_identifier,
            SECOND_AGENT.agent_identifier,
            FIRST_AGENT.agent_identifier,
        ]
        if payments != expected:
            raise DrillFailureError(f"the payments were reconstructed as {payments}")

    def _require_one_set(self, agents: tuple[ProvisionedAgent, ...]) -> Path:
        roots = {agent.context.enclave_root for agent in agents}
        if len(roots) != 1:
            raise DrillFailureError(f"the fleet runs on {len(roots)} enclave sets")
        return next(iter(roots))

    def _require_own_tenancies(
        self, agents: tuple[ProvisionedAgent, ...]
    ) -> tuple[str, ...]:
        tenants = tuple(agent.context.tenant for agent in agents)
        if len(set(tenants)) != EXPECTED_TENANTS:
            raise DrillFailureError(
                f"{len(agents)} agents hold the tenancies {tenants}"
            )
        keys = {agent.group_public_key for agent in agents}
        if len(keys) != len(agents):
            raise DrillFailureError("two tenancies hold the same session key")
        return tenants

    def _require_cross_tenant_refusal(
        self, holder: ProvisionedAgent, target: ProvisionedAgent
    ) -> str:
        own = EnclaveSet(holder.context.enclave_root, holder.context.tenant)
        borrowed = replace(
            endpoints_of(own)[FIRST_ENCLAVE], tenant=target.context.tenant
        )
        try:
            HttpEnclaveGateway(borrowed).attest(HALT_CHALLENGE)
        except EnclaveRefusedError as refusal:
            if UNAUTHORIZED not in str(refusal):
                raise DrillFailureError(
                    f"refused for another reason: {refusal}"
                ) from refusal
            return str(refusal)
        raise DrillFailureError("one tenant's token reached another tenant")

    def _order(self, scope: HaltScope, reason: str) -> HaltOrder:
        return HaltOrder(scope, reason, self._toolbox.chain_time())

    def _require_every_node_halted(self) -> None:
        halted = self._provisioning.halted_nodes()
        if len(halted) != ENCLAVE_PROCESSES:
            raise DrillFailureError(
                f"the halt reached {len(halted)} of {ENCLAVE_PROCESSES} enclaves"
            )

    def _require_halted(self, agent: ProvisionedAgent) -> str:
        reason = self._signing_reason(agent, self._payment_data(SECOND_PAYMENT))
        if not reason:
            raise DrillFailureError(
                f"the enclaves signed for {agent.agent_identifier} although it is halted"
            )
        if "halted" not in reason:
            raise DrillFailureError(f"refused for another reason: {reason}")
        return reason

    def _revoke_directly(self, agent: ProvisionedAgent) -> None:
        toolbox = self._toolbox
        uninstall = toolbox.environment.foundry.encode_call(
            FunctionCall(
                "uninstallModule(uint256,address,bytes)",
                (VALIDATOR_MODULE_TYPE, toolbox.contracts.validator, "0x"),
            )
        )
        with toolbox.acting_as(agent.context):
            operation = toolbox.with_direct_limits(
                toolbox.unsigned(
                    UnsignedRequest(
                        b"",
                        toolbox.next_owner_nonce(),
                        toolbox.self_call_data(uninstall),
                    )
                )
            )
            toolbox.submit_directly(
                toolbox.sign_with_key(
                    KeyedOperation(operation, toolbox.actors.owner_key)
                )
            )

    def _retire_in_place(self, plan: AgentPlan) -> None:
        self._ledger.retire(plan.agent_identifier)
        self._provisioning.lifecycle.retired(plan.agent_identifier)

    def _signed_payment(self, agent: ProvisionedAgent) -> UserOperation:
        toolbox = self._toolbox
        with toolbox.acting_as(agent.context):
            return toolbox.quorum_signed(
                toolbox.direct_session_operation(self._payment_data(SECOND_PAYMENT))
            )

    def _require_a_live_allowance(self, allowance: int) -> None:
        if allowance <= 0:
            raise DrillFailureError(
                "the check is vacuous: the agent has no allowance left to sweep"
            )

    def _require_still_tenanted(self, agent: ProvisionedAgent) -> None:
        context = agent.context
        if not tenant_is_reachable(EnclaveSet(context.enclave_root, context.tenant)):
            raise DrillFailureError(
                f"retiring one tenant left {agent.agent_identifier} unreachable"
            )

    def _require_swept(
        self, agent: ProvisionedAgent, swept: int, allowance: int
    ) -> None:
        if swept != allowance:
            raise DrillFailureError(f"{swept} wei was swept, not {allowance}")
        if self._toolbox.native_balance(agent.context.account) != 0:
            raise DrillFailureError("the agent account still holds an allowance")

    def _require_events(self, identifier: str, expected: list[ProvenanceEvent]) -> None:
        entries = self._provisioning.provenance.entries_for(identifier)
        events = [entry.event for entry in entries]
        if events != expected:
            raise DrillFailureError(f"{identifier} has the record {events}")

    def revoking_one_agent_leaves_the_other_working(self) -> str:
        first, second = self._agent(FIRST_AGENT), self._agent(SECOND_AGENT)
        remaining = self._ledger.remaining
        self._hold_payment(first)
        self._revoke(first)
        self._require_dead(first)
        self._require_still_paying(second)
        self._require_without_session(self._provisioning.treasury)
        self._ledger.retire(FIRST_AGENT.agent_identifier)
        self._provisioning.lifecycle.retired(FIRST_AGENT.agent_identifier)
        if self._ledger.remaining != remaining:
            raise DrillFailureError("retiring an agent freed budget never swept back")
        return (
            "the first agent's validator is uninstalled and a payment its quorum "
            "signed beforehand now fails with AA24; the second agent pays on, the "
            "treasury is untouched, and the retired agent's funding stays counted"
        )

    def _require_bound(self, agent: ProvisionedAgent) -> None:
        provenance = self._provisioning.provenance
        identifier = agent.agent_identifier
        events = [entry.event for entry in provenance.entries_for(identifier)]
        if events != EXPECTED_EVENTS:
            raise DrillFailureError(f"{identifier} has the record {events}")
        recorded = provenance.recorded_definition(identifier)
        if recorded != agent.approved.definition:
            raise DrillFailureError(f"{identifier} recorded another definition")
        self._require_installed_binding(agent, recorded)
        self._require_recorded_fingerprint(agent)

    def _require_installed_binding(
        self, agent: ProvisionedAgent, recorded: AgentDefinition
    ) -> None:
        policy = agent.context.policy
        if policy is None:
            raise DrillFailureError(f"{agent.agent_identifier} has no derived policy")
        with self._toolbox.acting_as(agent.context):
            installed_key = self._toolbox.stored_session_key()
        binding = ApprovalBinding(
            definition_digest(recorded), policy_digest(policy), installed_key
        )
        if agent_fingerprint(binding) != agent.approved.fingerprint:
            raise DrillFailureError(
                f"{agent.agent_identifier} runs under a session the owner never approved"
            )

    def _require_recorded_fingerprint(self, agent: ProvisionedAgent) -> None:
        entries = self._provisioning.provenance.entries_for(agent.agent_identifier)
        approvals = [
            entry for entry in entries if entry.event is ProvenanceEvent.APPROVED
        ]
        if approvals[0].details["fingerprint"] != agent.approved.fingerprint:
            raise DrillFailureError(
                f"{agent.agent_identifier} recorded an approval it does not run under"
            )

    def _require_approvals_are_not_interchangeable(self) -> None:
        first, second = self._both()
        if first.approved.fingerprint == second.approved.fingerprint:
            raise DrillFailureError("two agents share one approval fingerprint")

    def _runtime(self, agent: ProvisionedAgent) -> AgentRuntime:
        self._journal = PaymentJournal()
        return build_runtime(
            RuntimeSetup(
                self._toolbox,
                agent,
                self._provisioning.payee_addresses,
                self._provisioning.payee_labels,
                self._journal,
            )
        )

    def _task(self, payee: str = PAYEE_IDENTIFIER) -> PaymentTask:
        return PaymentTask(
            "Pay the development target for the drill invoice.",
            payee,
            ZERO_ADDRESS,
            RUNTIME_PAYMENT,
            RUNTIME_KEY if payee == PAYEE_IDENTIFIER else STRANGER_KEY,
        )

    def _require_summary(self, outcome: RuntimeOutcome, expected: str) -> None:
        if outcome.summary != expected:
            raise DrillFailureError(
                f"the agent reported {outcome.summary!r}, not {expected!r}"
            )
        if outcome.stopped_early:
            raise DrillFailureError("the agent ran into its step limit")

    def _require_stranger_never_reaches_the_chain(
        self, agent: ProvisionedAgent, runtime: AgentRuntime
    ) -> None:
        with self._toolbox.acting_as(agent.context):
            nonce = self._toolbox.next_session_nonce()
        outcome = runtime.run(self._task("stranger"))
        if not outcome.summary.startswith(REFUSED):
            raise DrillFailureError(f"the agent reported {outcome.summary!r}")
        with self._toolbox.acting_as(agent.context):
            if self._toolbox.next_session_nonce() != nonce:
                raise DrillFailureError("an operation for a stranger reached the chain")
        if self._journal.keys != (RUNTIME_KEY,):
            raise DrillFailureError("the refused payment was written to the journal")

    def _cap_on_chain(self, agent: ProvisionedAgent) -> int:
        return self._toolbox.read_integer(self._hook_call(agent, CAP_SIGNATURE))

    def _require_unchanged_authority(self) -> None:
        agent = self._agent(SECOND_AGENT)
        cap = self._cap_on_chain(agent)
        if cap != SECOND_AGENT.native_cap:
            raise DrillFailureError(f"the cap on chain is {cap}")
        if (
            self._toolbox.environment.foundry.call(
                self._hook_call(agent, PAYEE_SIGNATURE)
            )
            != "true"
        ):
            raise DrillFailureError("the approved payee is no longer allowed")

    def _hook_call(self, agent: ProvisionedAgent, signature: str) -> ContractCall:
        toolbox = self._toolbox
        return ContractCall(
            toolbox.contracts.policy_hook,
            FunctionCall(
                signature,
                (agent.context.account, self._subject_of(signature)),
            ),
        )

    def _subject_of(self, signature: str) -> str:
        if signature == CAP_SIGNATURE:
            return ZERO_ADDRESS
        return self._toolbox.contracts.target

    def _require_enclaves_refuse(
        self, agent: ProvisionedAgent, call_data: bytes
    ) -> None:
        reason = self._signing_reason(agent, call_data)
        if not reason:
            raise DrillFailureError(
                "the enclaves signed an operation outside the policy"
            )
        if not any(refusal in reason for refusal in PAYMENT_REFUSALS):
            raise DrillFailureError(f"refused for another reason: {reason}")

    def _signing_reason(self, agent: ProvisionedAgent, call_data: bytes) -> str:
        toolbox = self._toolbox
        with toolbox.acting_as(agent.context):
            try:
                toolbox.quorum_signed(toolbox.direct_session_operation(call_data))
            except SigningFailedError as refusal:
                return str(refusal.__cause__)
        return ""

    def _force(self, agent: ProvisionedAgent, call_data: bytes) -> None:
        toolbox = self._toolbox
        with toolbox.acting_as(agent.context):
            toolbox.submit_directly(
                toolbox.compromised_quorum_signed(
                    toolbox.direct_session_operation(call_data)
                )
            )

    def _payment_data_to(self, target: str) -> bytes:
        toolbox = self._toolbox
        return toolbox.session_execute_data(
            ExecutionRequest(target, b"", SMALL_PAYMENT)
        )

    def _provision(self, plan: AgentPlan) -> None:
        agent = self._provisioning.provision_agent(plan)
        self._ledger.admit(
            FleetMember(
                plan.agent_identifier, agent.context.account.lower(), plan.funded
            )
        )

    def _agent(self, plan: AgentPlan) -> ProvisionedAgent:
        return self._provisioning.agent(plan.agent_identifier)

    def _both(self) -> tuple[ProvisionedAgent, ProvisionedAgent]:
        return self._agent(FIRST_AGENT), self._agent(SECOND_AGENT)

    def _require_distinct_accounts(self, treasury: str) -> None:
        first, second = self._both()
        accounts = {
            first.context.account.lower(),
            second.context.account.lower(),
            treasury.lower(),
        }
        if len(accounts) != 3:
            raise DrillFailureError("the treasury and the agents share an account")
        if first.group_public_key == second.group_public_key:
            raise DrillFailureError("the two agents share a session key")
        if first.context.policy == second.context.policy:
            raise DrillFailureError("the two agents share a session policy")

    def _require_own_sessions(self) -> None:
        for agent in self._both():
            self._require_installed_session(agent)

    def _require_installed_session(self, agent: ProvisionedAgent) -> None:
        policy = agent.context.policy
        if policy is None:
            raise DrillFailureError(f"{agent.agent_identifier} has no derived policy")
        with self._toolbox.acting_as(agent.context):
            stored = self._toolbox.stored_session_key()
            window = self._toolbox.stored_session_window()
        if stored != agent.group_public_key:
            raise DrillFailureError(
                f"{agent.agent_identifier} does not hold the key its enclaves generated"
            )
        if window != (policy.valid_after, policy.valid_until):
            raise DrillFailureError(
                f"{agent.agent_identifier} holds the window {window}, not the derived "
                f"({policy.valid_after}, {policy.valid_until})"
            )

    def _require_funded_as_approved(self) -> None:
        for agent in self._both():
            balance = self._toolbox.native_balance(agent.context.account)
            if balance != agent.funded:
                raise DrillFailureError(
                    f"{agent.agent_identifier} holds {balance} wei, not the "
                    f"{agent.funded} wei it was funded with"
                )

    def _require_without_session(self, account: str) -> None:
        context = AccountContext(account, self._toolbox.enclave_root, None)
        with self._toolbox.acting_as(context):
            self._require_no_validator()

    def _require_no_validator(self) -> None:
        toolbox = self._toolbox
        if toolbox.stored_session_key():
            raise DrillFailureError("the account still holds a session key")
        if toolbox.is_installed(VALIDATOR_MODULE_TYPE, toolbox.contracts.validator):
            raise DrillFailureError("the session validator is still installed")

    def _require_budget_is_bounded(self) -> None:
        member = FleetMember(
            UNFUNDABLE_AGENT.agent_identifier,
            UNFUNDABLE_ACCOUNT,
            UNFUNDABLE_AGENT.funded,
        )
        try:
            self._ledger.admit(member)
        except FleetBudgetError:
            return
        raise DrillFailureError("the ledger funded an agent beyond the approved total")

    def _require_foreign_quorum_refused(
        self, borrower: ProvisionedAgent, lender: ProvisionedAgent
    ) -> None:
        reason = self._foreign_signing_reason(borrower, lender)
        if not reason:
            raise DrillFailureError("another agent's enclaves signed for this account")
        if FOREIGN_ACCOUNT_REFUSAL not in reason:
            raise DrillFailureError(f"refused for another reason: {reason}")

    def _foreign_signing_reason(
        self, borrower: ProvisionedAgent, lender: ProvisionedAgent
    ) -> str:
        toolbox = self._toolbox
        with self._toolbox.acting_as(self._borrowed_context(borrower, lender)):
            try:
                toolbox.quorum_signed(
                    toolbox.direct_session_operation(self._payment_data(SMALL_PAYMENT))
                )
            except SigningFailedError as refusal:
                return str(refusal.__cause__)
        return ""

    def _borrowed_context(
        self, borrower: ProvisionedAgent, lender: ProvisionedAgent
    ) -> AccountContext:
        return AccountContext(
            borrower.context.account,
            lender.context.enclave_root,
            lender.context.policy,
            lender.context.tenant,
        )

    def _require_foreign_signature_rejected(
        self, borrower: ProvisionedAgent, lender: ProvisionedAgent
    ) -> None:
        with self._toolbox.acting_as(self._borrowed_context(borrower, lender)):
            operation = self._toolbox.compromised_quorum_signed(
                self._toolbox.direct_session_operation(
                    self._payment_data(SMALL_PAYMENT)
                )
            )
        self._require_rejected(borrower, operation)

    def _require_independent_windows(
        self, first: ProvisionedAgent, second: ProvisionedAgent
    ) -> None:
        before_first = self._observe(first)
        before_second = self._observe(second)
        self._pay(Payment(first, SMALL_PAYMENT))
        self._require_spent(Payment(first, SMALL_PAYMENT), before_first)
        self._require_unchanged(second, before_second)

    def _require_over_balance_but_inside_cap(self, before: Observation) -> None:
        if OVER_BALANCE_PAYMENT <= before.balance:
            raise DrillFailureError(
                "the check is vacuous: the payment is within the balance"
            )
        if before.window_spend + OVER_BALANCE_PAYMENT > FIRST_AGENT.native_cap:
            raise DrillFailureError(
                "the check is vacuous: the payment is outside the cap"
            )

    def _hold_payment(self, agent: ProvisionedAgent) -> None:
        toolbox = self._toolbox
        with self._toolbox.acting_as(agent.context):
            self._held = toolbox.quorum_signed(
                toolbox.direct_session_operation(self._payment_data(SECOND_PAYMENT))
            )

    def _revoke(self, agent: ProvisionedAgent) -> None:
        toolbox = self._toolbox
        with self._toolbox.acting_as(agent.context):
            toolbox.owner_uninstalls(
                ModuleInstall(VALIDATOR_MODULE_TYPE, toolbox.contracts.validator, b"")
            )

    def _require_dead(self, agent: ProvisionedAgent) -> None:
        held = self._held
        if held is None:
            raise DrillFailureError("no payment was held before the revocation")
        with self._toolbox.acting_as(agent.context):
            self._require_no_validator()
        self._require_rejected(agent, held)

    def _require_rejected(
        self, agent: ProvisionedAgent, operation: UserOperation
    ) -> None:
        before = self._observe(agent)
        try:
            self._toolbox.submit_directly(operation)
        except FoundryError as rejection:
            self._require_signature_rejection(rejection)
            self._require_unchanged(agent, before)
            return
        raise DrillFailureError("an operation outside this agent's authority executed")

    def _require_signature_rejection(self, rejection: FoundryError) -> None:
        if not is_rejection(rejection, "AA24"):
            raise DrillFailureError(f"unexpected rejection: {rejection}")

    def _require_still_paying(self, agent: ProvisionedAgent) -> None:
        before = self._observe(agent)
        self._pay(Payment(agent, SECOND_PAYMENT))
        self._require_spent(Payment(agent, SECOND_PAYMENT), before)

    def _pay(self, payment: Payment) -> None:
        toolbox = self._toolbox
        with self._toolbox.acting_as(payment.agent.context):
            toolbox.submit_directly(
                toolbox.quorum_signed(
                    toolbox.direct_session_operation(self._payment_data(payment.amount))
                )
            )

    def _observe(self, agent: ProvisionedAgent) -> Observation:
        toolbox = self._toolbox
        with self._toolbox.acting_as(agent.context):
            window_spend = toolbox.spent_in_window(ZERO_ADDRESS)
        return Observation(toolbox.native_balance(agent.context.account), window_spend)

    def _require_unchanged(self, agent: ProvisionedAgent, before: Observation) -> None:
        after = self._observe(agent)
        if after != before:
            raise DrillFailureError(
                f"{agent.agent_identifier} changed state although nothing should have "
                f"moved: {before} became {after}"
            )

    def _require_spent(self, payment: Payment, before: Observation) -> None:
        after = self._observe(payment.agent)
        expected = Observation(
            before.balance - payment.amount, before.window_spend + payment.amount
        )
        if after != expected:
            raise DrillFailureError(
                f"{payment.agent.agent_identifier} did not spend exactly "
                f"{payment.amount} wei: expected {expected}, found {after}"
            )

    def _payment_data(self, amount: int) -> bytes:
        toolbox = self._toolbox
        return toolbox.session_execute_data(
            ExecutionRequest(toolbox.contracts.target, b"", amount)
        )
