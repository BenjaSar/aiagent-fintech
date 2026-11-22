from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from fintech_agent.adapters.foundry_toolkit import FunctionCall
from fintech_agent.adapters.in_memory import InMemorySealedStorage
from fintech_agent.adapters.local_proposer import (
    ReadOnlyProposer,
    WidestAllowedProposer,
)
from fintech_agent.application.agent_development import (
    Attempt,
    DevelopmentOutcome,
    DevelopmentParts,
    DevelopmentRequest,
    develop,
)
from fintech_agent.application.agent_evaluation import (
    STATUS_TOOL,
    EvaluationBudget,
    EvaluationReport,
    EvaluationRequest,
    evaluate,
)
from fintech_agent.application.agent_lifecycle import (
    AgentLifecycle,
    IterationLimits,
    LifecycleParts,
)
from fintech_agent.application.agent_promotion import (
    PromotionRefusedError,
    PromotionRequest,
    promote,
)
from fintech_agent.application.agent_retirement import (
    RetireAgent,
    RetirementOutcome,
    RetirementParts,
)
from fintech_agent.application.agent_synthesis import (
    Candidate,
    CandidateProposer,
    Proposal,
    SynthesisError,
    SynthesisLimits,
    SynthesisRequest,
    synthesize,
)
from fintech_agent.application.fleet_ledger import FleetLedger
from fintech_agent.application.halt_switch import HaltOrder
from fintech_agent.application.payee_registry import ApprovedPayee, PayeeRegistry
from fintech_agent.application.policy_derivation import (
    DeploymentParameters,
    DerivationError,
    DerivationRequest,
    derive_session_policy,
)
from fintech_agent.application.provenance_log import (
    ProvenanceEvent,
    ProvenanceLog,
    ProvenanceRecord,
)
from fintech_agent.application.renewal_scheduler import (
    RenewalScheduler,
    SchedulerParts,
    ScheduleRun,
    TickReport,
)
from fintech_agent.application.session_rotation import (
    RotateSession,
    RotationOutcome,
    RotationParts,
    RotationRequest,
)
from fintech_agent.application.session_schedule import (
    RENEWAL_LEAD_SECONDS,
    RenewalRequest,
    ScheduledSession,
    ScheduleQuery,
    due_for_renewal,
    renewal_of,
)
from fintech_agent.composition.drill_toolbox import (
    ENTRY_POINT,
    HOOK_MODULE_TYPE,
    VALIDATOR_MODULE_TYPE,
    ZERO_ADDRESS,
    AccountContext,
    DrillFailureError,
    DrillToolbox,
    ExecutionRequest,
    ModuleInstall,
    OwnerOperation,
)
from fintech_agent.composition.fleet_lifecycle import (
    LocalEnclaveTenancy,
    OnChainTeardown,
    SweepTarget,
    TreasurySweep,
    UnattendedRenewalApproval,
)
from fintech_agent.composition.local_enclaves import (
    EnclaveSet,
    halt_agent,
    halt_fleet,
    halted_nodes,
    resume_agent,
    resume_fleet,
)
from fintech_agent.composition.rotation_drill import OnChainGrantor
from fintech_agent.composition.rotation_local import (
    CeremonySet,
    FleetConfiguration,
    LocalEnclaveFleet,
    LocalKeyCeremony,
    LocalPolicyIssuer,
    PreviousTenant,
    TenantKeyCeremony,
)
from fintech_agent.composition.session_policies import (
    MAXIMUM_GAS_COST,
    WINDOW_SECONDS,
)
from fintech_agent.domain.agent_approval import (
    AgentAcknowledgement,
    AgentApprovalError,
    AgentApprovalScreen,
    AgentPresentation,
    ApprovalBinding,
    ApprovedAgent,
    agent_fingerprint,
    approve_agent,
    describe_agent,
)
from fintech_agent.domain.agent_definition import (
    AgentDefinition,
    AgentType,
    Budget,
    ModelChoice,
)
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.approval_display import AssetDisplay, DisplayCatalog
from fintech_agent.domain.approval_text import render_agent_text
from fintech_agent.domain.authority_order import AuthorityWidenedError
from fintech_agent.domain.fleet_identity import TREASURY_IDENTIFIER, account_salt
from fintech_agent.domain.policy_approval import ApprovalLimits, policy_digest
from fintech_agent.domain.session_policy import AssetRule, SessionPolicy
from fintech_agent.domain.task_specification import TaskSpecification
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from fintech_agent.domain.user_operation_hash import HashingDomain

TIME_BYTES = 6
SESSION_SECONDS = 86_400
CLOCK_SKEW_SECONDS = 300
PAYEE_IDENTIFIER = "development-target"
PAYEE_LABEL = "Development Ping Target"
STAGE_ONE_TOOLS = (
    "list_payees",
    "get_balance",
    "get_payment_status",
    "pay_payee",
)
ANALYSIS_TOOLS = ("list_payees", "get_payment_status")
PAYING_TOOL = "pay_payee"
ANALYSIS_PURPOSE = "Report on the payments the fleet has made."
DRILL_MODEL = ModelChoice("claude-sonnet-5-5", 0, 1024)
RISK_TIER = 1
OVERSTEP_FACTOR = 2
ITERATION_FACTOR = 2
NATIVE_DECIMALS = 18
NATIVE_SYMBOL = "ETH"


@dataclass(frozen=True)
class AgentPlan:
    agent_identifier: str
    native_cap: int
    funded: int


@dataclass(frozen=True)
class AnalysisPlan:
    agent_identifier: str


@dataclass(frozen=True)
class AuthoredAgent:
    agent_identifier: str
    definition: AgentDefinition
    approved: ApprovedAgent
    report: EvaluationReport


@dataclass(frozen=True)
class AgentRun:
    agent_identifier: str
    salt: str
    account: str
    candidate: Candidate
    policy: SessionPolicy
    group_public_key: bytes


@dataclass(frozen=True)
class PreparedAgent:
    run: AgentRun
    report: EvaluationReport


@dataclass(frozen=True)
class ProvisionedAgent:
    agent_identifier: str
    context: AccountContext
    group_public_key: bytes
    funded: int
    approved: ApprovedAgent


@dataclass(frozen=True)
class FundingTransfer:
    account: str
    amount: int


@dataclass(frozen=True)
class RenewalOutcome:
    definition: AgentDefinition
    policy: SessionPolicy
    rotation: RotationOutcome


@dataclass(frozen=True)
class DevelopmentCase:
    plan: AgentPlan
    proposer: CandidateProposer


@dataclass(frozen=True)
class DevelopmentRun:
    outcome: DevelopmentOutcome
    feedback: tuple[str, ...]


@dataclass(frozen=True)
class RenewedAgent:
    agent_identifier: str
    context: AccountContext
    group_public_key: bytes
    definition: AgentDefinition
    fingerprint: str


class DrillApprover:
    def __init__(self, clock: Callable[[], int]) -> None:
        self._clock = clock
        self.shown = ""

    def review(self, screen: AgentApprovalScreen) -> AgentAcknowledgement:
        self.shown = render_agent_text(screen)
        return AgentAcknowledgement(
            screen.fingerprint,
            screen.required_acknowledgements,
            screen.definition.owner,
            self._clock(),
        )


class LearningProposer:
    def __init__(self, widens: bool) -> None:
        self._widens = widens
        self._calls = 0

    def propose(self, specification: TaskSpecification) -> Proposal:
        self._calls += 1
        honest = WidestAllowedProposer().propose(specification)
        if self._calls == 1:
            return replace(
                honest,
                tools=_without_status(honest.tools),
                assets=_halved(honest.assets),
            )
        if self._widens:
            return honest
        return replace(honest, assets=_halved(honest.assets))


def _without_status(tools: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(tool for tool in tools if tool != STATUS_TOOL)


def _halved(assets: tuple[AssetRule, ...]) -> tuple[AssetRule, ...]:
    return tuple(AssetRule(rule.asset, rule.cap // ITERATION_FACTOR) for rule in assets)


class OversteppingProposer:
    def propose(self, specification: TaskSpecification) -> Proposal:
        honest = WidestAllowedProposer().propose(specification)
        return replace(
            honest,
            assets=tuple(
                AssetRule(rule.asset, rule.cap * OVERSTEP_FACTOR)
                for rule in honest.assets
            ),
        )


class FleetCandidateSource:
    def __init__(
        self, provisioning: "FleetProvisioning", case: DevelopmentCase
    ) -> None:
        self._provisioning = provisioning
        self._plan = case.plan
        self._proposer = case.proposer
        self.feedback: tuple[str, ...] = ()

    def generate(self, feedback: tuple[str, ...]) -> Candidate:
        self.feedback = feedback
        return synthesize(
            self._provisioning.synthesis_request(self._plan), self._proposer
        )


class FleetCandidateJudge:
    def __init__(self, provisioning: "FleetProvisioning", plan: AgentPlan) -> None:
        self._provisioning = provisioning
        self._plan = plan

    def judge(self, candidate: Candidate) -> EvaluationReport:
        return self._provisioning.judge_candidate(candidate, self._plan)


class FleetProvisioning:
    def __init__(
        self, toolbox: DrillToolbox, configuration: FleetConfiguration
    ) -> None:
        self._toolbox = toolbox
        self._enclave_fleet = LocalEnclaveFleet(configuration)
        self._approver = DrillApprover(toolbox.chain_time)
        self._provenance = ProvenanceLog()
        self._lifecycle = AgentLifecycle(
            LifecycleParts(IterationLimits(), STAGE_ONE_PAYMENT_CATALOG)
        )
        self._payee_registry: PayeeRegistry | None = None
        self._renewal_ceremony: CeremonySet | None = None
        self._provisioned: dict[str, ProvisionedAgent] = {}
        self._renewals: dict[str, int] = {}
        self._shared_root: Path | None = None
        self._treasury = ""
        self._treasury_created = False

    @property
    def treasury(self) -> str:
        if not self._treasury:
            raise DrillFailureError("the treasury account is not provisioned")
        return self._treasury

    @property
    def provenance(self) -> ProvenanceLog:
        return self._provenance

    @property
    def lifecycle(self) -> AgentLifecycle:
        return self._lifecycle

    @property
    def payee_addresses(self) -> dict[str, str]:
        return self._registry().addresses_by_identifier

    @property
    def payee_labels(self) -> dict[str, str]:
        return self._registry().labels_by_address

    def provision_treasury(self) -> str:
        toolbox = self._toolbox
        account = toolbox.predicted_account(account_salt(TREASURY_IDENTIFIER))
        toolbox.fund(account)
        toolbox.deposit_for_gas(account)
        self._treasury = account
        return account

    def provision_agent(self, plan: AgentPlan) -> ProvisionedAgent:
        toolbox = self._toolbox
        ceremony = self._ceremony_for(plan.agent_identifier)
        public_key = ceremony.generate().group_public_key.serialize()
        enclaves = ceremony.require_enclaves()
        self._shared_root = enclaves.root
        prepared = self._prepare(plan, public_key)
        run = prepared.run
        toolbox.deposit_for_gas(run.account)
        self._record_derivation(run)
        self._record_evaluation(run.agent_identifier, prepared.report)
        approved = self._approve(run, prepared.report)
        self._lifecycle.approved(plan.agent_identifier)
        self._record_approval(run.agent_identifier, approved)
        self._install(run)
        LocalPolicyIssuer(ceremony).issue(run.policy)
        self._transfer_from_treasury(FundingTransfer(run.account, plan.funded))
        self._record_installation(run)
        self._lifecycle.activated(plan.agent_identifier)
        provisioned = ProvisionedAgent(
            plan.agent_identifier,
            AccountContext(run.account, enclaves.root, run.policy, enclaves.tenant),
            public_key,
            plan.funded,
            approved,
        )
        self._provisioned[plan.agent_identifier] = provisioned
        return provisioned

    def agent(self, agent_identifier: str) -> ProvisionedAgent:
        provisioned = self._provisioned.get(agent_identifier)
        if provisioned is None:
            raise DrillFailureError(f"{agent_identifier} is not provisioned")
        return provisioned

    def _ceremony_for(self, tenant: str) -> CeremonySet:
        root = self._shared_root
        if root is None:
            return LocalKeyCeremony(self._enclave_fleet, tenant)
        return TenantKeyCeremony(self._enclave_fleet, EnclaveSet(root, tenant))

    def author_analysis_agent(self, plan: AnalysisPlan) -> AuthoredAgent:
        identifier = plan.agent_identifier
        candidate = synthesize(self.analysis_request(plan), ReadOnlyProposer())
        self._lifecycle.begin(identifier, candidate)
        self._record_generation(identifier, candidate)
        report = evaluate(self._reader_evaluation(candidate))
        self._lifecycle.evaluated(identifier)
        self._record_evaluation(identifier, report)
        approved = self._approve_reader(candidate, report)
        self._lifecycle.approved(identifier)
        self._record_approval(identifier, approved)
        self._lifecycle.activated(identifier)
        return AuthoredAgent(identifier, candidate.definition, approved, report)

    def analysis_request(self, plan: AnalysisPlan) -> SynthesisRequest:
        return SynthesisRequest(
            self._analysis_specification(plan),
            STAGE_ONE_PAYMENT_CATALOG,
            self._registry(),
            SynthesisLimits(),
        )

    def refuse_paying_reader(self, plan: AnalysisPlan) -> str:
        mistaken = replace(
            self._analysis_specification(plan),
            tools=(*ANALYSIS_TOOLS, PAYING_TOOL),
        )
        request = SynthesisRequest(
            mistaken, STAGE_ONE_PAYMENT_CATALOG, self._registry(), SynthesisLimits()
        )
        try:
            synthesize(request, WidestAllowedProposer())
        except SynthesisError as refusal:
            return str(refusal)
        raise DrillFailureError("a reader was given a tool that can pay")

    def refuse_reader_with_a_key(self, authored: AuthoredAgent) -> str:
        presentation = AgentPresentation(
            authored.definition,
            None,
            b"" + bytes(32),
            STAGE_ONE_PAYMENT_CATALOG,
            self._display_catalog(),
            ApprovalLimits(),
            self._toolbox.chain_time(),
        )
        try:
            describe_agent(presentation)
        except AgentApprovalError as refusal:
            return str(refusal)
        raise DrillFailureError("a reader was shown with a session key")

    def refuse_reader_derivation(self, authored: AuthoredAgent) -> str:
        try:
            self._derive(authored.definition, self.treasury)
        except DerivationError as refusal:
            return str(refusal)
        raise DrillFailureError("a session policy was derived for a reader")

    @property
    def shown(self) -> str:
        return self._approver.shown

    def _analysis_specification(self, plan: AnalysisPlan) -> TaskSpecification:
        now = self._toolbox.chain_time()
        return TaskSpecification(
            identifier=plan.agent_identifier,
            agent_type=AgentType.ANALYSIS,
            purpose=ANALYSIS_PURPOSE,
            model=DRILL_MODEL,
            tools=ANALYSIS_TOOLS,
            payees=(),
            assets=(),
            window_seconds=WINDOW_SECONDS,
            valid_after=now,
            valid_until=now + SESSION_SECONDS,
            budget=None,
            maximum_risk_tier=RISK_TIER,
            owner=self._toolbox.actors.owner.lower(),
            provenance_identifier=f"drill-{plan.agent_identifier}",
        )

    def _reader_evaluation(self, candidate: Candidate) -> EvaluationRequest:
        return EvaluationRequest(
            candidate,
            None,
            self.payee_addresses,
            STAGE_ONE_PAYMENT_CATALOG,
            0,
            InMemorySealedStorage,
            EvaluationBudget(),
            self._toolbox.chain_time(),
        )

    def _approve_reader(
        self, candidate: Candidate, report: EvaluationReport
    ) -> ApprovedAgent:
        presentation = AgentPresentation(
            candidate.definition,
            None,
            b"",
            STAGE_ONE_PAYMENT_CATALOG,
            self._display_catalog(),
            ApprovalLimits(),
            self._toolbox.chain_time(),
        )
        screen = promote(PromotionRequest(report, presentation)).screen
        return approve_agent(screen, self._approver.review(screen))

    def _prepare(self, plan: AgentPlan, public_key: bytes) -> PreparedAgent:
        salt = account_salt(plan.agent_identifier)
        account = self._toolbox.predicted_account(salt)
        developed = self.develop_agent(DevelopmentCase(plan, WidestAllowedProposer()))
        accepted = self._accepted(plan, developed.outcome)
        candidate = accepted.candidate
        self._record_generation(plan.agent_identifier, candidate)
        policy = self._derive(candidate.definition, account)
        return PreparedAgent(
            AgentRun(
                plan.agent_identifier, salt, account, candidate, policy, public_key
            ),
            accepted.report,
        )

    def _accepted(self, plan: AgentPlan, outcome: DevelopmentOutcome) -> Attempt:
        if outcome.accepted is None:
            raise DrillFailureError(
                f"{plan.agent_identifier} produced no sound candidate: "
                f"{outcome.escalation}"
            )
        return outcome.accepted

    def _generate(self, plan: AgentPlan) -> Candidate:
        return synthesize(self.synthesis_request(plan), WidestAllowedProposer())

    def refuse_overstepping_generation(self, plan: AgentPlan) -> str:
        try:
            synthesize(self.synthesis_request(plan), OversteppingProposer())
        except SynthesisError as refusal:
            return str(refusal)
        raise DrillFailureError("a proposal beyond its task became a candidate")

    def synthesis_request(self, plan: AgentPlan) -> SynthesisRequest:
        return SynthesisRequest(
            self._specification(plan),
            STAGE_ONE_PAYMENT_CATALOG,
            self._registry(),
            SynthesisLimits(),
        )

    def _specification(self, plan: AgentPlan) -> TaskSpecification:
        now = self._toolbox.chain_time()
        return TaskSpecification(
            identifier=plan.agent_identifier,
            agent_type=AgentType.PAYMENT,
            purpose="Pay the development target from the funded budget.",
            model=DRILL_MODEL,
            tools=STAGE_ONE_TOOLS,
            payees=(PAYEE_IDENTIFIER,),
            assets=(AssetRule(ZERO_ADDRESS, plan.native_cap),),
            window_seconds=WINDOW_SECONDS,
            valid_after=now,
            valid_until=now + SESSION_SECONDS,
            budget=Budget(ZERO_ADDRESS, plan.funded),
            maximum_risk_tier=RISK_TIER,
            owner=self._toolbox.actors.owner.lower(),
            provenance_identifier=f"drill-{plan.agent_identifier}",
        )

    def _record_generation(self, identifier: str, candidate: Candidate) -> None:
        cost = candidate.cost
        standing = self._lifecycle.standing_of(identifier)
        self._provenance.append(
            ProvenanceRecord(
                identifier,
                ProvenanceEvent.GENERATED,
                self._toolbox.chain_time(),
                {
                    "model": candidate.model.identifier,
                    "temperature_hundredths": str(
                        candidate.model.temperature_hundredths
                    ),
                    "definition_digest": candidate.definition_digest,
                    "attempts": str(standing.attempts),
                    "cost_micros": str(standing.cost_micros),
                    "tokens": str(cost.input_tokens + cost.output_tokens),
                },
                candidate.definition,
            )
        )

    def _derive(self, definition: AgentDefinition, account: str) -> SessionPolicy:
        request = DerivationRequest(
            definition,
            self._deployment(account),
            STAGE_ONE_PAYMENT_CATALOG,
            self._registry(),
        )
        return derive_session_policy(request)

    def _deployment(self, account: str) -> DeploymentParameters:
        toolbox = self._toolbox
        contracts = toolbox.contracts
        return DeploymentParameters(
            domain=HashingDomain(toolbox.chain_id(), ENTRY_POINT.lower()),
            account=account.lower(),
            validator=contracts.validator.lower(),
            hook=contracts.policy_hook.lower(),
            allowed_paymasters=frozenset({contracts.paymaster.lower()}),
            maximum_gas_cost=MAXIMUM_GAS_COST,
            window_seconds=WINDOW_SECONDS,
            clock_skew_seconds=CLOCK_SKEW_SECONDS,
        )

    def _registry(self) -> PayeeRegistry:
        if self._payee_registry is None:
            self._payee_registry = self._build_registry()
        return self._payee_registry

    def _build_registry(self) -> PayeeRegistry:
        toolbox = self._toolbox
        registry = PayeeRegistry()
        registry.approve(
            ApprovedPayee(
                PAYEE_IDENTIFIER,
                toolbox.contracts.target.lower(),
                PAYEE_LABEL,
                toolbox.actors.owner.lower(),
                toolbox.chain_time(),
            )
        )
        return registry

    def _display_catalog(self) -> DisplayCatalog:
        return DisplayCatalog(
            AssetDisplay(NATIVE_SYMBOL, NATIVE_DECIMALS),
            {},
            self._registry().labels_by_address,
        )

    def _approve(self, run: AgentRun, report: EvaluationReport) -> ApprovedAgent:
        promoted = promote(PromotionRequest(report, self._presentation(run)))
        screen = promoted.screen
        return approve_agent(screen, self._approver.review(screen))

    def _presentation(self, run: AgentRun) -> AgentPresentation:
        return AgentPresentation(
            run.candidate.definition,
            run.policy,
            run.group_public_key,
            STAGE_ONE_PAYMENT_CATALOG,
            self._display_catalog(),
            ApprovalLimits(),
            self._toolbox.chain_time(),
        )

    def _evaluate(self, run: AgentRun, plan: AgentPlan) -> EvaluationReport:
        return evaluate(self._evaluation_request(run, plan.funded))

    def judge_candidate(
        self, candidate: Candidate, plan: AgentPlan
    ) -> EvaluationReport:
        account = self._toolbox.predicted_account(account_salt(plan.agent_identifier))
        run = AgentRun(
            plan.agent_identifier,
            account_salt(plan.agent_identifier),
            account,
            candidate,
            self._derive(candidate.definition, account),
            b"",
        )
        return self._evaluate(run, plan)

    def develop_agent(self, case: DevelopmentCase) -> DevelopmentRun:
        source = FleetCandidateSource(self, case)
        parts = DevelopmentParts(
            self._lifecycle, source, FleetCandidateJudge(self, case.plan)
        )
        outcome = develop(DevelopmentRequest(parts, case.plan.agent_identifier))
        return DevelopmentRun(outcome, source.feedback)

    def _evaluation_request(self, run: AgentRun, funded: int) -> EvaluationRequest:
        return EvaluationRequest(
            run.candidate,
            run.policy,
            self.payee_addresses,
            STAGE_ONE_PAYMENT_CATALOG,
            funded,
            InMemorySealedStorage,
            EvaluationBudget(),
            self._toolbox.chain_time(),
        )

    def agents_due_for_renewal(
        self, agents: tuple[str, ...], lead_seconds: int = RENEWAL_LEAD_SECONDS
    ) -> tuple[str, ...]:
        sessions = tuple(
            ScheduledSession(
                identifier, self._lifecycle.standing_of(identifier).definition
            )
            for identifier in agents
        )
        return due_for_renewal(
            ScheduleQuery(sessions, self._toolbox.chain_time(), lead_seconds)
        )

    def renew(self, agent_identifier: str) -> None:
        self.renew_agent(self.agent(agent_identifier))

    def due(self, lead_seconds: int) -> tuple[str, ...]:
        return self.agents_due_for_renewal(tuple(self._provisioned), lead_seconds)

    def run_scheduler(self, schedule: ScheduleRun) -> TickReport:
        scheduler = RenewalScheduler(SchedulerParts(self, self, _no_pause))
        return scheduler.run(schedule)

    def halted_nodes(self) -> tuple[int, ...]:
        return halted_nodes(self._require_shared_root())

    def halt_the_fleet(self, order: HaltOrder) -> None:
        halt_fleet(self._require_shared_root(), order)

    def resume_the_fleet(self, order: HaltOrder) -> None:
        resume_fleet(self._require_shared_root(), order)

    def halt_one_agent(self, agent: ProvisionedAgent, order: HaltOrder) -> None:
        halt_agent(self._enclaves_of(agent), order)

    def resume_one_agent(self, agent: ProvisionedAgent, order: HaltOrder) -> None:
        resume_agent(self._enclaves_of(agent), order)

    def _enclaves_of(self, agent: ProvisionedAgent) -> EnclaveSet:
        context = agent.context
        return EnclaveSet(context.enclave_root, context.tenant)

    def _require_shared_root(self) -> Path:
        root = self._shared_root
        if root is None:
            raise DrillFailureError("the fleet has no enclave set")
        return root

    def renew_agent(self, agent: ProvisionedAgent) -> RenewedAgent:
        identifier = agent.agent_identifier
        now = self._toolbox.chain_time()
        active = self._lifecycle.standing_of(identifier).definition
        renewed = renewal_of(RenewalRequest(active, STAGE_ONE_PAYMENT_CATALOG, now))
        policy = self._derive(renewed, agent.context.account)
        outcome = self._rotate(agent, self._rotation_request(agent, policy))
        self._lifecycle.renewed(identifier, renewed)
        return self._renewed(agent, RenewalOutcome(renewed, policy, outcome))

    def _rotation_request(
        self, agent: ProvisionedAgent, policy: SessionPolicy
    ) -> RotationRequest:
        return RotationRequest(
            agent.context.policy,
            policy,
            self._display_catalog(),
            ApprovalLimits(),
            self._toolbox.chain_time(),
        )

    def _rotate(
        self, agent: ProvisionedAgent, request: RotationRequest
    ) -> RotationOutcome:
        previous = self._enclaves_of(agent)
        ceremony = TenantKeyCeremony(
            self._enclave_fleet,
            EnclaveSet(previous.root, self._renewal_tenant(agent)),
        )
        self._renewal_ceremony = ceremony
        rotation = RotateSession(
            RotationParts(
                ceremony,
                LocalPolicyIssuer(ceremony),
                UnattendedRenewalApproval(),
                OnChainGrantor(self._toolbox),
                PreviousTenant(previous),
            )
        )
        with self._toolbox.acting_as(agent.context):
            return rotation.execute(request)

    def _renewal_tenant(self, agent: ProvisionedAgent) -> str:
        identifier = agent.agent_identifier
        count = self._renewals.get(identifier, 0) + 1
        self._renewals[identifier] = count
        return f"{identifier}-r{count}"

    def _renewed(
        self, agent: ProvisionedAgent, renewal: RenewalOutcome
    ) -> RenewedAgent:
        if self._renewal_ceremony is None:
            raise DrillFailureError("no renewal ceremony has run")
        fingerprint = agent_fingerprint(
            ApprovalBinding(
                definition_digest(renewal.definition),
                policy_digest(renewal.policy),
                renewal.rotation.group_public_key,
            )
        )
        enclaves = self._renewal_ceremony.require_enclaves()
        renewed = RenewedAgent(
            agent.agent_identifier,
            AccountContext(
                agent.context.account,
                enclaves.root,
                renewal.policy,
                enclaves.tenant,
            ),
            renewal.rotation.group_public_key,
            renewal.definition,
            fingerprint,
        )
        self._provisioned[renewed.agent_identifier] = replace(
            agent,
            context=renewed.context,
            group_public_key=renewed.group_public_key,
        )
        self._record_renewal(renewed)
        return renewed

    def refuse_widened_renewal(self, agent: ProvisionedAgent) -> str:
        identifier = agent.agent_identifier
        active = self._lifecycle.standing_of(identifier).definition
        widened = replace(
            renewal_of(
                RenewalRequest(
                    active, STAGE_ONE_PAYMENT_CATALOG, self._toolbox.chain_time()
                )
            ),
            assets=tuple(
                AssetRule(rule.asset, rule.cap * OVERSTEP_FACTOR)
                for rule in active.assets
            ),
        )
        refusal = self._refused_renewal(identifier, widened)
        if self._lifecycle.standing_of(identifier).definition != active:
            raise DrillFailureError("a refused renewal changed the standing authority")
        return refusal

    def _refused_renewal(self, identifier: str, widened: AgentDefinition) -> str:
        try:
            self._lifecycle.renewed(identifier, widened)
        except AuthorityWidenedError as refusal:
            return str(refusal)
        raise DrillFailureError("a widened renewal was granted without a human")

    def retire_agent(
        self, agent: ProvisionedAgent, ledger: FleetLedger
    ) -> RetirementOutcome:
        context = agent.context
        parts = RetirementParts(
            OnChainTeardown(self._toolbox, context),
            TreasurySweep(self._toolbox, SweepTarget(context, self.treasury)),
            LocalEnclaveTenancy(self._enclaves_of(agent)),
            ledger,
        )
        outcome = RetireAgent(parts).execute(agent.agent_identifier)
        self._lifecycle.retired(agent.agent_identifier)
        self._record_retirement(outcome)
        return outcome

    def refuse_unevaluable_candidate(self, plan: AgentPlan) -> str:
        run = self._weakened_run(plan)
        report = self._evaluate(run, plan)
        if report.passed:
            raise DrillFailureError(
                "a candidate without the status tool was evaluated as sound"
            )
        try:
            promote(PromotionRequest(report, self._presentation(run)))
        except PromotionRefusedError as refusal:
            return str(refusal)
        raise DrillFailureError("a candidate that failed evaluation was promoted")

    def _weakened_run(self, plan: AgentPlan) -> AgentRun:
        candidate = self._generate(plan)
        definition = replace(
            candidate.definition,
            tools=tuple(
                tool for tool in candidate.definition.tools if tool != STATUS_TOOL
            ),
        )
        weakened = replace(
            candidate,
            definition=definition,
            definition_digest=definition_digest(definition),
        )
        account = self._toolbox.predicted_account(account_salt(plan.agent_identifier))
        return AgentRun(
            plan.agent_identifier,
            account_salt(plan.agent_identifier),
            account,
            weakened,
            self._derive(definition, account),
            b"\x02" + bytes(32),
        )

    def _record_derivation(self, run: AgentRun) -> None:
        self._provenance.append(
            ProvenanceRecord(
                run.agent_identifier,
                ProvenanceEvent.DERIVED,
                self._toolbox.chain_time(),
                {
                    "account": run.account.lower(),
                    "payee": PAYEE_IDENTIFIER,
                    "policy_window": str(run.policy.valid_until),
                },
            )
        )

    def _record_evaluation(self, identifier: str, report: EvaluationReport) -> None:
        self._provenance.append(
            ProvenanceRecord(
                identifier,
                ProvenanceEvent.EVALUATED,
                self._toolbox.chain_time(),
                {
                    "score": report.score,
                    "seconds": f"{report.seconds:.3f}",
                    "cost_micros": str(report.cost_micros),
                    "candidate_digest": report.candidate_digest,
                },
            )
        )

    def _record_approval(self, identifier: str, approved: ApprovedAgent) -> None:
        self._provenance.append(
            ProvenanceRecord(
                identifier,
                ProvenanceEvent.APPROVED,
                approved.approved_at,
                {
                    "fingerprint": approved.fingerprint,
                    "approver": approved.approver,
                    "policy_digest": approved.binding.policy_digest,
                },
            )
        )

    def _record_renewal(self, renewed: RenewedAgent) -> None:
        policy = renewed.context.policy
        if policy is None:
            raise DrillFailureError("a renewal needs its derived policy")
        self._provenance.append(
            ProvenanceRecord(
                renewed.agent_identifier,
                ProvenanceEvent.RENEWED,
                self._toolbox.chain_time(),
                {
                    "fingerprint": renewed.fingerprint,
                    "session_key": renewed.group_public_key.hex(),
                    "valid_after": str(policy.valid_after),
                    "valid_until": str(policy.valid_until),
                    "approver": "scheduler",
                },
            )
        )

    def _record_retirement(self, outcome: RetirementOutcome) -> None:
        self._provenance.append(
            ProvenanceRecord(
                outcome.agent_identifier,
                ProvenanceEvent.RETIRED,
                self._toolbox.chain_time(),
                {
                    "swept": str(outcome.swept),
                    "freed": str(outcome.freed),
                },
            )
        )

    def _record_installation(self, run: AgentRun) -> None:
        self._provenance.append(
            ProvenanceRecord(
                run.agent_identifier,
                ProvenanceEvent.INSTALLED,
                self._toolbox.chain_time(),
                {
                    "session_key": run.group_public_key.hex(),
                    "valid_after": str(run.policy.valid_after),
                    "valid_until": str(run.policy.valid_until),
                },
            )
        )

    def _install(self, run: AgentRun) -> None:
        toolbox = self._toolbox
        context = AccountContext(run.account, toolbox.enclave_root, None)
        with toolbox.acting_as(context):
            self._install_modules(run)

    def _install_modules(self, run: AgentRun) -> None:
        toolbox = self._toolbox
        validator = self._validator_install(run)
        hook = self._hook_install(run.policy)
        toolbox.owner_submits(
            OwnerOperation(
                toolbox.creation_init_code(run.salt),
                toolbox.self_call_data(toolbox.install_call(validator)),
            )
        )
        toolbox.owner_installs(hook)
        self._require_installed(validator)
        self._require_installed(hook)

    def _validator_install(self, run: AgentRun) -> ModuleInstall:
        policy = run.policy
        data = (
            run.group_public_key
            + policy.valid_after.to_bytes(TIME_BYTES, "big")
            + policy.valid_until.to_bytes(TIME_BYTES, "big")
        )
        return ModuleInstall(
            VALIDATOR_MODULE_TYPE, self._toolbox.contracts.validator, data
        )

    def _hook_install(self, policy: SessionPolicy) -> ModuleInstall:
        toolbox = self._toolbox
        targets = ",".join(sorted(policy.allowed_targets))
        payees = ",".join(sorted(policy.allowed_payees))
        rules = ",".join(f"({rule.asset},{rule.cap})" for rule in policy.rules)
        settings = toolbox.environment.foundry.encode_arguments(
            FunctionCall(
                "f(address[],(address,uint256)[],address[],bool)",
                (
                    f"[{targets}]",
                    f"[{rules}]",
                    f"[{payees}]",
                    "true" if policy.payments_only else "false",
                ),
            )
        )
        return ModuleInstall(HOOK_MODULE_TYPE, toolbox.contracts.policy_hook, settings)

    def _require_installed(self, install: ModuleInstall) -> None:
        if not self._toolbox.is_installed(install.module_type, install.module):
            raise DrillFailureError(
                f"{install.module} is not installed on the agent account"
            )

    def _transfer_from_treasury(self, transfer: FundingTransfer) -> None:
        toolbox = self._toolbox
        context = AccountContext(self.treasury, toolbox.enclave_root, None)
        with toolbox.acting_as(context):
            self._submit_transfer(transfer)

    def _submit_transfer(self, transfer: FundingTransfer) -> None:
        toolbox = self._toolbox
        init_code = b""
        if not self._treasury_created:
            init_code = toolbox.creation_init_code(account_salt(TREASURY_IDENTIFIER))
        toolbox.owner_submits(
            OwnerOperation(
                init_code,
                toolbox.execute_data(
                    ExecutionRequest(transfer.account, b"", transfer.amount)
                ),
            )
        )
        self._treasury_created = True


def _no_pause(seconds: int) -> None:
    return None
