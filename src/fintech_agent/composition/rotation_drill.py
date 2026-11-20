from dataclasses import dataclass, replace
from pathlib import Path

from fintech_agent.adapters.bundler_rpc import BundlerRpcError
from fintech_agent.adapters.foundry_toolkit import (
    ContractCall,
    FoundryError,
    FunctionCall,
)
from fintech_agent.application.ports import EnclaveUnavailableError
from fintech_agent.application.session_rotation import (
    GrantFailedError,
    InstalledSession,
    RotateSession,
    RotationApproval,
    RotationDeclinedError,
    RotationOutcome,
    RotationParts,
    RotationRequest,
    RotationReview,
    SessionGrant,
    SessionStateUnreadableError,
)
from fintech_agent.application.sign_user_operation import SigningFailedError
from fintech_agent.composition.drill_toolbox import (
    VALIDATOR_MODULE_TYPE,
    ZERO_ADDRESS,
    DrillFailureError,
    DrillToolbox,
    is_rejection,
)
from fintech_agent.composition.local_enclaves import build_signing_setup
from fintech_agent.composition.policy_drill import NATIVE_CAP
from fintech_agent.composition.rotation_local import (
    FleetConfiguration,
    LocalEnclaveFleet,
    LocalKeyCeremony,
    LocalPolicyIssuer,
    LocalPreviousSession,
)
from fintech_agent.domain.approval_display import AssetDisplay, DisplayCatalog
from fintech_agent.domain.approval_text import render_text
from fintech_agent.domain.policy_approval import Acknowledgement, ApprovalLimits
from fintech_agent.domain.policy_transition import (
    HookChange,
    HookChangeKind,
    PolicyTransition,
    hook_changes,
)
from fintech_agent.domain.session_call import Execution
from fintech_agent.domain.session_policy import AssetRule, SessionPolicy
from fintech_agent.domain.user_operation import UserOperation

ROTATED_SESSION_SECONDS = 2 * 86_400
CLOCK_SKEW_MARGIN_SECONDS = 300
ROTATED_CAP = 2 * NATIVE_CAP
TIME_BYTES = 6
GRANT_FAILURES = (FoundryError, BundlerRpcError, DrillFailureError)
READ_FAILURES = (FoundryError, OSError, ValueError)


class DrillUser:
    def __init__(self, declines: bool) -> None:
        self._declines = declines
        self.shown = ""

    def review(self, review: RotationReview) -> RotationApproval | None:
        self.shown = render_text(review.screen)
        if self._declines:
            return None
        return RotationApproval(
            Acknowledgement(
                review.screen.policy_digest, review.screen.required_acknowledgements
            ),
            review.key_fingerprint,
        )


class OnChainGrantor:
    def __init__(self, toolbox: DrillToolbox) -> None:
        self._toolbox = toolbox

    def apply(self, grant: SessionGrant) -> None:
        try:
            self._toolbox.owner_executes_batch(self._executions(grant))
        except GRANT_FAILURES as failure:
            raise GrantFailedError(str(failure)) from failure

    def installed(self) -> InstalledSession | None:
        toolbox = self._toolbox
        try:
            key = toolbox.stored_session_key()
            valid_after, valid_until = toolbox.stored_session_window()
        except READ_FAILURES as failure:
            raise SessionStateUnreadableError(str(failure)) from failure
        if not key:
            return None
        return InstalledSession(key, valid_after, valid_until)

    def _executions(self, grant: SessionGrant) -> tuple[Execution, ...]:
        steps: list[Execution] = []
        if grant.previous is not None:
            steps.append(self._uninstall())
            steps.extend(self._hook_calls(grant.previous, grant))
        steps.append(self._install(grant))
        return tuple(steps)

    def _uninstall(self) -> Execution:
        toolbox = self._toolbox
        call = toolbox.environment.foundry.encode_call(
            FunctionCall(
                "uninstallModule(uint256,address,bytes)",
                (VALIDATOR_MODULE_TYPE, toolbox.contracts.validator, "0x"),
            )
        )
        return Execution(toolbox.actors.account, 0, call)

    def _install(self, grant: SessionGrant) -> Execution:
        toolbox = self._toolbox
        policy = grant.approved.policy
        data = (
            grant.group_public_key
            + policy.valid_after.to_bytes(TIME_BYTES, "big")
            + policy.valid_until.to_bytes(TIME_BYTES, "big")
        )
        call = toolbox.environment.foundry.encode_call(
            FunctionCall(
                "installModule(uint256,address,bytes)",
                (VALIDATOR_MODULE_TYPE, toolbox.contracts.validator, "0x" + data.hex()),
            )
        )
        return Execution(toolbox.actors.account, 0, call)

    def _hook_calls(
        self, previous: SessionPolicy, grant: SessionGrant
    ) -> list[Execution]:
        hook = self._toolbox.contracts.policy_hook
        changes = hook_changes(PolicyTransition(previous, grant.approved.policy))
        return [Execution(hook, 0, self._encoded(change)) for change in changes]

    def _encoded(self, change: HookChange) -> bytes:
        arguments: tuple[str, ...] = (change.subject,)
        if change.kind is HookChangeKind.SET_CAP:
            arguments = (change.subject, str(change.cap))
        return self._toolbox.environment.foundry.encode_call(
            FunctionCall(change.kind.value, arguments)
        )


@dataclass(frozen=True)
class RotationSetup:
    fleet_configuration: FleetConfiguration


class RotationChecks:
    def __init__(self, toolbox: DrillToolbox, setup: RotationSetup) -> None:
        self._toolbox = toolbox
        self._fleet = LocalEnclaveFleet(setup.fleet_configuration)
        self._stale: UserOperation | None = None
        self._outcome: RotationOutcome | None = None
        self._pings_after_rotation = 0

    def declined_rotation_changes_nothing(self) -> str:
        toolbox = self._toolbox
        toolbox.submit_through_bundler(self._old_quorum_ping())
        pings = toolbox.pings()
        self._stale = self._old_quorum_ping()
        before = OnChainGrantor(toolbox).installed()
        ceremony = LocalKeyCeremony(self._fleet)
        try:
            self._rotation(ceremony, DrillUser(declines=True)).execute(self._request())
        except RotationDeclinedError:
            return self._declined_report(ceremony, before, pings)
        raise DrillFailureError("a declined rotation went ahead")

    def rotation_replaces_the_session(self) -> str:
        toolbox = self._toolbox
        old_root = toolbox.enclave_root
        old_key = toolbox.stored_session_key()
        spent_before = toolbox.spent_in_window(ZERO_ADDRESS)
        ceremony = LocalKeyCeremony(self._fleet)
        request = self._request()
        outcome = self._rotation(ceremony, DrillUser(declines=False)).execute(request)
        toolbox.enclave_root = ceremony.require_root()
        toolbox.active_policy = request.following
        self._outcome = outcome
        self._require_rotated(request, outcome)
        if toolbox.stored_session_key() == old_key:
            raise DrillFailureError("the validator still holds the old key")
        if toolbox.spent_in_window(ZERO_ADDRESS) != spent_before:
            raise DrillFailureError("the rotation changed the hook accounting")
        self._require_stopped(old_root)
        self._pings_after_rotation = toolbox.pings()
        return (
            "DKG, review, approval, policy issuance, one owner batch and verification "
            f"completed; key {outcome.group_public_key.hex()[:12]}... installed, cap "
            f"raised to {ROTATED_CAP} wei, window accounting unchanged, previous "
            "enclaves stopped"
        )

    def old_key_is_dead_and_new_key_works(self) -> str:
        toolbox = self._toolbox
        stale = self._stale
        if stale is None or self._outcome is None:
            raise DrillFailureError("no rotation has happened")
        try:
            toolbox.submit_directly(stale)
        except FoundryError as rejection:
            if not is_rejection(rejection, "AA24"):
                raise DrillFailureError(
                    f"unexpected rejection: {rejection}"
                ) from rejection
        else:
            raise DrillFailureError("an operation signed by the old quorum still works")
        if toolbox.pings() != self._pings_after_rotation:
            raise DrillFailureError("the old quorum operation executed")
        toolbox.submit_through_bundler(self._old_quorum_ping())
        if toolbox.pings() != self._pings_after_rotation + 1:
            raise DrillFailureError("the new quorum operation did not execute")
        return "old quorum operation rejected with AA24; new quorum operation executed"

    def _old_quorum_ping(self) -> UserOperation:
        toolbox = self._toolbox
        operation = toolbox.session_operation(toolbox.next_session_nonce())
        try:
            return toolbox.quorum_signed(operation)
        except SigningFailedError as failure:
            raise DrillFailureError(f"{failure}: {failure.__cause__}") from failure

    def _declined_report(
        self,
        ceremony: LocalKeyCeremony,
        before: InstalledSession | None,
        pings: int,
    ) -> str:
        toolbox = self._toolbox
        if OnChainGrantor(toolbox).installed() != before:
            raise DrillFailureError("a declined rotation changed the validator")
        self._require_stopped(ceremony.require_root())
        if toolbox.pings() != pings:
            raise DrillFailureError("an unexpected call executed")
        return (
            "the user declined after seeing the policy; the validator keeps its key "
            "and the new enclaves were stopped"
        )

    def _require_rotated(
        self, request: RotationRequest, outcome: RotationOutcome
    ) -> None:
        toolbox = self._toolbox
        installed = OnChainGrantor(toolbox).installed()
        expected = InstalledSession(
            outcome.group_public_key,
            request.following.valid_after,
            request.following.valid_until,
        )
        if installed != expected:
            raise DrillFailureError("the validator does not hold the approved session")
        if toolbox.read_integer(self._cap_call()) != ROTATED_CAP:
            raise DrillFailureError("the hook did not take the approved cap")
        if not outcome.previous_retired:
            raise DrillFailureError("the previous enclaves were not retired")

    def _rotation(self, ceremony: LocalKeyCeremony, user: DrillUser) -> RotateSession:
        toolbox = self._toolbox
        return RotateSession(
            RotationParts(
                ceremony,
                LocalPolicyIssuer(ceremony),
                user,
                OnChainGrantor(toolbox),
                LocalPreviousSession(self._fleet, toolbox.enclave_root),
            )
        )

    def _request(self) -> RotationRequest:
        toolbox = self._toolbox
        active = toolbox.active_policy
        if active is None:
            raise DrillFailureError("no session policy is active")
        now = toolbox.chain_time()
        following = replace(
            active,
            rules=(AssetRule(ZERO_ADDRESS, ROTATED_CAP),),
            valid_after=now - CLOCK_SKEW_MARGIN_SECONDS,
            valid_until=now + ROTATED_SESSION_SECONDS,
        )
        catalog = DisplayCatalog(
            AssetDisplay("ETH", 18),
            {},
            {toolbox.contracts.target.lower(): "Development Ping Target"},
        )
        return RotationRequest(active, following, catalog, ApprovalLimits(), now)

    def _cap_call(self) -> ContractCall:
        toolbox = self._toolbox
        return ContractCall(
            toolbox.contracts.policy_hook,
            FunctionCall(
                "capOf(address,address)(uint256)",
                (toolbox.actors.account, ZERO_ADDRESS),
            ),
        )

    def _require_stopped(self, root: Path) -> None:
        gateway = build_signing_setup(root).gateways[1]
        try:
            gateway.attest(b"liveness")
        except EnclaveUnavailableError:
            return
        raise DrillFailureError(f"the enclaves under {root.name} are still running")
