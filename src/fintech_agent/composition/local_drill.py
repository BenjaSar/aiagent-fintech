from collections.abc import Callable
from dataclasses import dataclass

from fintech_agent.adapters.bundler_rpc import BundlerRpcError
from fintech_agent.adapters.foundry_toolkit import (
    ContractCall,
    ContractTransaction,
    FoundryError,
    FunctionCall,
)
from fintech_agent.application.agent_lifecycle import LifecycleError
from fintech_agent.application.agent_retirement import RetirementError
from fintech_agent.application.session_rotation import RotationFailedError
from fintech_agent.application.sign_user_operation import (
    QuorumUnavailableError,
    SigningFailedError,
    SigningRequest,
    SignUserOperation,
)
from fintech_agent.composition.attestation_drill import AttestationChecks
from fintech_agent.composition.drill_toolbox import (
    ACCOUNT_SALT,
    EXECUTOR_MODULE_TYPE,
    GUARDIAN_THRESHOLD,
    RECOVERY_DELAY_SECONDS,
    VALIDATOR_MODULE_TYPE,
    DrillEnvironment,
    DrillFailureError,
    DrillToolbox,
    KeyedOperation,
    ModuleInstall,
    UnsignedRequest,
    is_rejection,
    new_key,
)
from fintech_agent.composition.enclave_drill import EnclaveChecks
from fintech_agent.composition.fleet_drill import FleetChecks, FleetSetup
from fintech_agent.composition.local_enclaves import (
    EnclaveSet,
    build_signing_setup,
    load_public_group,
)
from fintech_agent.composition.policy_drill import PolicyChecks
from fintech_agent.composition.rotation_drill import RotationChecks, RotationSetup
from fintech_agent.composition.rotation_local import (
    EnclaveFleetError,
    FleetConfiguration,
)
from fintech_agent.composition.sanctions_drill import SanctionsChecks
from fintech_agent.domain.user_operation import UserOperation

SESSION_LIFETIME_SECONDS = 86_400
TIME_BYTES = 6
STEP_COUNT = 46
EXPECTED_FAILURES = (
    FoundryError,
    BundlerRpcError,
    SigningFailedError,
    RotationFailedError,
    LifecycleError,
    RetirementError,
    EnclaveFleetError,
    OSError,
    KeyError,
    ValueError,
)


@dataclass(frozen=True)
class DrillResult:
    name: str
    passed: bool
    detail: str


class LocalDrill:
    def __init__(self, environment: DrillEnvironment) -> None:
        self._environment = environment
        self._toolbox = DrillToolbox(environment)
        self._policy = PolicyChecks(self._toolbox)
        self._attestation = AttestationChecks(self._toolbox)
        self._enclaves = EnclaveChecks(self._toolbox)
        self._sanctions = SanctionsChecks(self._toolbox)
        self._rotation = RotationChecks(
            self._toolbox,
            RotationSetup(
                FleetConfiguration(
                    environment.enclave_sets_directory, environment.enclave_script
                )
            ),
        )
        self._fleet = FleetChecks(
            self._toolbox,
            FleetSetup(
                FleetConfiguration(
                    environment.enclave_sets_directory, environment.enclave_script
                )
            ),
        )
        self._first_session_operation: UserOperation | None = None
        self._held_operation: UserOperation | None = None
        self._new_owner_key: str | None = None

    def run(self) -> list[DrillResult]:
        results: list[DrillResult] = []
        for name, action in self._steps():
            result = self._attempt(name, action)
            results.append(result)
            if not result.passed:
                break
        return results

    def _steps(self) -> list[tuple[str, Callable[[], str]]]:
        return [
            ("deploy contracts and prepare the account", self._deploy),
            (
                "owner operation creates the account and installs the session validator",
                self._install_validator,
            ),
            (
                "owner installs the policy hook with an allowlist and a native cap",
                self._policy.install_hook,
            ),
            (
                "owner issues the session policy to the three enclaves, once",
                self._enclaves.policy_is_issued,
            ),
            (
                "enclaves refuse every operation outside the policy before signing",
                self._enclaves.violations_are_refused,
            ),
            (
                "2-of-3 signature passes the bundler and executes through the hook",
                self._quorum_operation,
            ),
            ("a replayed operation is rejected", self._replay),
            ("an operation signed with a single share is rejected", self._single_share),
            (
                "a session operation cannot use the plain execute entry point",
                self._policy.plain_execute_is_refused,
            ),
            (
                "a session operation cannot call the account itself",
                self._policy.self_call_is_refused,
            ),
            (
                "the rolling native cap is enforced on chain",
                self._policy.cap_is_enforced,
            ),
            (
                "enclave ledgers refuse a spend over the cap before any share is released",
                self._enclaves.ledger_refuses_over_cap,
            ),
            (
                "a sanctioned counterparty is refused on chain although the enclaves signed",
                self._sanctions.sanctioned_target_is_refused,
            ),
            (
                "an unavailable sanctions oracle fails closed for sessions, not for the owner",
                self._sanctions.oracle_outage_fails_closed,
            ),
            (
                "a module without an attestation cannot be installed (ERC-7484 gate)",
                self._attestation.unattested_install_is_refused,
            ),
            (
                "a revoked attestation blocks installation again",
                self._attestation.revoked_attestation_blocks_install,
            ),
            (
                "a declined session rotation changes nothing on chain",
                self._rotation.declined_rotation_changes_nothing,
            ),
            (
                "session rotation: new DKG, reviewed policy, user grant, one owner batch",
                self._rotation.rotation_replaces_the_session,
            ),
            (
                "after rotation the old quorum is rejected and the new quorum executes",
                self._rotation.old_key_is_dead_and_new_key_works,
            ),
            (
                "a treasury and two agent accounts hold separate, funded authorities",
                self._fleet.treasury_and_two_agents_are_provisioned,
            ),
            (
                "one agent cannot use another agent's key, policy or window",
                self._fleet.agents_cannot_use_each_others_authority,
            ),
            (
                "an agent cannot spend more than it was funded, even inside its cap",
                self._fleet.an_agent_cannot_spend_more_than_it_was_funded,
            ),
            (
                "every agent runs under an approval bound to its definition and key",
                self._fleet.every_agent_runs_under_a_bound_approval,
            ),
            (
                "a candidate that fails evaluation never reaches an approval",
                self._fleet.a_candidate_that_fails_evaluation_never_reaches_an_approval,
            ),
            (
                "a generated definition beyond its task never becomes an authority",
                self._fleet.an_overstepping_proposal_never_becomes_an_authority,
            ),
            (
                "a payment agent can only pay an approved payee",
                self._fleet.a_payment_agent_can_only_pay_an_approved_payee,
            ),
            (
                "a payment agent cannot attach call data to a payment",
                self._fleet.a_payment_agent_cannot_attach_call_data,
            ),
            (
                "an agent's runtime pays an approved payee, once, and nothing else",
                self._fleet.the_runtime_pays_an_approved_payee_once,
            ),
            (
                "an interaction trace reconstructs a multi-agent payment sequence",
                self._fleet.the_trace_reconstructs_a_multi_agent_sequence,
            ),
            (
                "a failing candidate is iterated inside a bounded budget",
                self._fleet.a_failing_candidate_is_iterated_inside_its_budget,
            ),
            (
                "an iteration that would widen authority is escalated, not installed",
                self._fleet.a_widening_iteration_is_escalated_to_the_owner,
            ),
            (
                "a session renews on schedule without a human, a widened one never does",
                self._fleet.a_session_renews_on_schedule_without_a_human,
            ),
            (
                "three agents share one enclave set as separate tenants",
                self._fleet.the_fleet_shares_one_enclave_set,
            ),
            (
                "a fleet halt stops every agent with the company offline (I-12)",
                self._fleet.a_fleet_halt_stops_every_agent,
            ),
            (
                "a halted agent is still revoked and the fleet recovered by its owner",
                self._fleet.a_halted_agent_can_still_be_revoked_and_recovered,
            ),
            (
                "revoking one agent leaves the other paying and the treasury intact",
                self._fleet.revoking_one_agent_leaves_the_other_working,
            ),
            (
                "a retired agent keeps no key, no allowance and no enclave tenancy",
                self._fleet.a_retired_agent_keeps_nothing,
            ),
            (
                "an analysis agent is approved and holds no authority at all",
                self._fleet.an_analysis_agent_is_approved_and_holds_nothing,
            ),
            (
                "owner installs social recovery with three user-chosen guardians",
                self._install_recovery,
            ),
            (
                "paymaster sponsors a session operation; the account pays nothing",
                self._sponsored,
            ),
            (
                "sponsorship does not replace the account signature (I-6)",
                self._sponsorship_is_not_authority,
            ),
            (
                "quorum signs an operation that is held back for the revocation check",
                self._hold_back,
            ),
            ("fewer than 2 reachable enclaves halts signing", self._halt),
            (
                "owner revokes the session validator with every company service offline (I-2)",
                self._revoke,
            ),
            (
                "guardians recover the account without the company (I-7)",
                self._recover,
            ),
            (
                "after recovery the old owner is rejected and the new owner acts",
                self._after_recovery,
            ),
        ]

    def _attempt(self, name: str, action: Callable[[], str]) -> DrillResult:
        try:
            return DrillResult(name, True, action())
        except (DrillFailureError, *EXPECTED_FAILURES) as failure:
            return DrillResult(name, False, str(failure))

    def _deploy(self) -> str:
        return self._toolbox.deploy_everything()

    def _install_validator(self) -> str:
        toolbox = self._toolbox
        foundry = self._environment.foundry
        contracts, actors = toolbox.contracts, toolbox.actors
        group = load_public_group(EnclaveSet(self._environment.enclave_root))
        valid_until = toolbox.chain_time() + SESSION_LIFETIME_SECONDS
        toolbox.session_valid_until = valid_until
        install_data = (
            group.configuration.group_public_key.serialize()
            + (0).to_bytes(TIME_BYTES, "big")
            + valid_until.to_bytes(TIME_BYTES, "big")
        )
        creation = foundry.encode_call(
            FunctionCall("createAccount(address,bytes32)", (actors.owner, ACCOUNT_SALT))
        )
        validator_install = ModuleInstall(
            VALIDATOR_MODULE_TYPE, contracts.validator, install_data
        )
        operation = toolbox.unsigned(
            UnsignedRequest(
                bytes.fromhex(contracts.factory.removeprefix("0x")) + creation,
                0,
                toolbox.self_call_data(toolbox.install_call(validator_install)),
            )
        )
        signed = toolbox.sign_with_key(
            KeyedOperation(toolbox.estimated(operation), actors.owner_key)
        )
        toolbox.submit_through_bundler(signed)
        self._require_installed(VALIDATOR_MODULE_TYPE, contracts.validator)
        return (
            "account created through initCode, validator installed with the group key"
        )

    def _require_installed(self, module_type: str, module: str) -> None:
        if not self._toolbox.is_installed(module_type, module):
            raise DrillFailureError(f"module {module} is not installed")

    def _quorum_operation(self) -> str:
        toolbox = self._toolbox
        operation = toolbox.quorum_signed(
            toolbox.session_operation(toolbox.next_session_nonce())
        )
        self._first_session_operation = operation
        toolbox.submit_through_bundler(operation)
        self._require_pings(1)
        return "ping executed once through the account"

    def _require_pings(self, expected: int) -> None:
        pings = self._toolbox.pings()
        if pings != expected:
            raise DrillFailureError(f"expected {expected} pings, found {pings}")

    def _replay(self) -> str:
        if self._first_session_operation is None:
            raise DrillFailureError("there is no earlier operation to replay")
        try:
            self._toolbox.submit_through_bundler(self._first_session_operation)
        except BundlerRpcError as rejection:
            return f"rejected: {rejection}"
        raise DrillFailureError("the replayed operation was accepted")

    def _single_share(self) -> str:
        toolbox = self._toolbox
        operation = toolbox.session_operation(toolbox.next_session_nonce())
        forged = toolbox.forge_with_one_share(toolbox.operation_hash(operation))
        try:
            toolbox.submit_through_bundler(operation.with_signature(forged.serialize()))
        except BundlerRpcError as rejection:
            return f"rejected: {rejection}"
        raise DrillFailureError("an operation signed with one share was accepted")

    def _install_recovery(self) -> str:
        toolbox = self._toolbox
        contracts = toolbox.contracts
        toolbox.owner_installs(
            ModuleInstall(
                EXECUTOR_MODULE_TYPE, contracts.recovery, toolbox.recovery_settings()
            )
        )
        self._require_installed(EXECUTOR_MODULE_TYPE, contracts.recovery)
        return f"{GUARDIAN_THRESHOLD}-of-3 guardians, {RECOVERY_DELAY_SECONDS} s delay"

    def _sponsored(self) -> str:
        toolbox = self._toolbox
        actors, contracts = toolbox.actors, toolbox.contracts
        account_before = toolbox.entry_point_balance(actors.account)
        paymaster_before = toolbox.entry_point_balance(contracts.paymaster)
        pings_before = toolbox.pings()
        draft = toolbox.sponsored(
            toolbox.session_operation(toolbox.next_session_nonce())
        )
        toolbox.submit_through_bundler(toolbox.quorum_signed(draft))
        account_after = toolbox.entry_point_balance(actors.account)
        paymaster_after = toolbox.entry_point_balance(contracts.paymaster)
        if account_after != account_before:
            raise DrillFailureError("the account deposit changed on a sponsored call")
        if paymaster_after >= paymaster_before:
            raise DrillFailureError("the paymaster deposit did not pay for the gas")
        self._require_pings(pings_before + 1)
        return f"paymaster paid {paymaster_before - paymaster_after} wei; account deposit unchanged"

    def _sponsorship_is_not_authority(self) -> str:
        toolbox = self._toolbox
        unsigned = toolbox.unsigned(
            UnsignedRequest(b"", toolbox.next_owner_nonce(), toolbox.ping_data())
        )
        sponsored = toolbox.sponsored(toolbox.estimated(unsigned))
        stranger = toolbox.sign_with_key(KeyedOperation(sponsored, new_key()))
        try:
            toolbox.submit_through_bundler(stranger)
        except BundlerRpcError as rejection:
            return f"rejected: {rejection}"
        raise DrillFailureError("a sponsored operation without authority was accepted")

    def _hold_back(self) -> str:
        toolbox = self._toolbox
        self._held_operation = toolbox.quorum_signed(
            toolbox.session_operation(toolbox.next_session_nonce())
        )
        return "a valid, unsubmitted session operation is kept for later"

    def _halt(self) -> str:
        toolbox = self._toolbox
        self._environment.halt_enclave(self._toolbox.enclave_root, 1)
        self._environment.halt_enclave(self._toolbox.enclave_root, 2)
        pings_before = toolbox.pings()
        try:
            SignUserOperation(build_signing_setup(self._toolbox.enclaves)).execute(
                SigningRequest(bytes(32))
            )
        except QuorumUnavailableError as halted:
            if toolbox.pings() != pings_before:
                raise DrillFailureError(
                    "state changed while signing was halted"
                ) from halted
            return f"signing halted: {halted}"
        raise DrillFailureError("signing succeeded with only one enclave running")

    def _revoke(self) -> str:
        toolbox = self._toolbox
        foundry = self._environment.foundry
        contracts, actors = toolbox.contracts, toolbox.actors
        held = self._held_operation
        if held is None:
            raise DrillFailureError("there is no held operation")
        toolbox.simulate_directly(held)
        self._environment.halt_enclave(self._toolbox.enclave_root, 3)
        self._environment.halt_bundler()
        uninstall = foundry.encode_call(
            FunctionCall(
                "uninstallModule(uint256,address,bytes)",
                (VALIDATOR_MODULE_TYPE, contracts.validator, "0x"),
            )
        )
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
            toolbox.sign_with_key(KeyedOperation(operation, actors.owner_key))
        )
        stored = foundry.call(
            ContractCall(
                contracts.validator,
                FunctionCall("sessionPublicKeyOf(address)(bytes)", (actors.account,)),
            )
        )
        if stored != "0x":
            raise DrillFailureError("the session key is still stored after revocation")
        try:
            toolbox.submit_directly(held)
        except FoundryError as rejection:
            if not is_rejection(rejection, "AA24"):
                raise DrillFailureError(
                    f"unexpected rejection: {rejection}"
                ) from rejection
            return "uninstalled through a self-submitted handleOps; the held quorum operation now fails with AA24"
        raise DrillFailureError("the held operation still executes after revocation")

    def _recover(self) -> str:
        toolbox = self._toolbox
        foundry = self._environment.foundry
        contracts, actors = toolbox.contracts, toolbox.actors
        new_owner_key = new_key()
        new_owner = foundry.address_of(new_owner_key)
        self._new_owner_key = new_owner_key
        approvals = [
            ContractTransaction(
                ContractCall(
                    contracts.recovery,
                    FunctionCall(
                        "approveRecovery(address,address)", (actors.account, new_owner)
                    ),
                ),
                key,
            )
            for key in actors.guardian_keys[:GUARDIAN_THRESHOLD]
        ]
        for approval in approvals:
            toolbox.send_as(approval)
        execution = ContractTransaction(
            ContractCall(
                contracts.recovery,
                FunctionCall(
                    "executeRecovery(address,address)", (actors.account, new_owner)
                ),
            ),
            actors.deployer_key,
        )
        self._require_early_execution_refused(execution)
        toolbox.advance_time(RECOVERY_DELAY_SECONDS + 1)
        toolbox.send_as(execution)
        signer = foundry.call(
            ContractCall(actors.account, FunctionCall("signer()(address)"))
        )
        if signer.lower() != new_owner.lower():
            raise DrillFailureError("the account owner did not change")
        return "2 guardians approved, early execution refused, owner rotated after the delay"

    def _require_early_execution_refused(self, execution: ContractTransaction) -> None:
        selector = self._environment.foundry.error_selector("RecoveryNotReady()")
        try:
            self._toolbox.send_as(execution)
        except FoundryError as refusal:
            if selector not in str(refusal).lower():
                raise DrillFailureError(f"unexpected refusal: {refusal}") from refusal
            return
        raise DrillFailureError("recovery executed before the delay passed")

    def _after_recovery(self) -> str:
        toolbox = self._toolbox
        actors = toolbox.actors
        unsigned = toolbox.with_direct_limits(
            toolbox.unsigned(
                UnsignedRequest(b"", toolbox.next_owner_nonce(), toolbox.ping_data())
            )
        )
        old_owner = toolbox.sign_with_key(KeyedOperation(unsigned, actors.owner_key))
        try:
            toolbox.submit_directly(old_owner)
        except FoundryError as rejection:
            if not is_rejection(rejection, "AA24"):
                raise DrillFailureError(
                    f"unexpected rejection: {rejection}"
                ) from rejection
        else:
            raise DrillFailureError("the old owner still controls the account")
        pings_before = toolbox.pings()
        if self._new_owner_key is None:
            raise DrillFailureError("no new owner was chosen")
        new_owner = toolbox.sign_with_key(KeyedOperation(unsigned, self._new_owner_key))
        toolbox.submit_directly(new_owner)
        self._require_pings(pings_before + 1)
        return "old owner rejected with AA24; new owner executed a call through self-submitted handleOps"
