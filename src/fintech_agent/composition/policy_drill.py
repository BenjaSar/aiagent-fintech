from collections.abc import Callable
from dataclasses import dataclass

from fintech_agent.adapters.bundler_rpc import BundlerRpcError
from fintech_agent.adapters.foundry_toolkit import FunctionCall
from fintech_agent.composition.drill_toolbox import (
    HOOK_MODULE_TYPE,
    VALIDATOR_MODULE_TYPE,
    ZERO_ADDRESS,
    DrillFailureError,
    DrillToolbox,
    ExecutionRequest,
    ModuleInstall,
)
from fintech_agent.domain.user_operation import UserOperation

ETHER = 10**18
NATIVE_CAP = ETHER
FIRST_SPEND = 6 * ETHER // 10
REFUSED_SPEND = 5 * ETHER // 10
REMAINING_SPEND = 4 * ETHER // 10


@dataclass(frozen=True)
class Observation:
    session_nonce: int
    target_balance: int
    window_spend: int


class PolicyChecks:
    def __init__(self, toolbox: DrillToolbox) -> None:
        self._toolbox = toolbox

    def install_hook(self) -> str:
        toolbox = self._toolbox
        settings = toolbox.environment.foundry.encode_arguments(
            FunctionCall(
                "f(address[],(address,uint256)[])",
                (
                    f"[{toolbox.contracts.target}]",
                    f"[({ZERO_ADDRESS},{NATIVE_CAP})]",
                ),
            )
        )
        toolbox.owner_installs(
            ModuleInstall(HOOK_MODULE_TYPE, toolbox.contracts.policy_hook, settings)
        )
        if not toolbox.is_installed(HOOK_MODULE_TYPE, toolbox.contracts.policy_hook):
            raise DrillFailureError("the policy hook is not installed")
        return "hook installed: one allowed target, native cap of 1 ETH per 24 hours"

    def plain_execute_is_refused(self) -> str:
        toolbox = self._toolbox
        before = self._observe()
        operation = toolbox.compromised_quorum_signed(
            toolbox.direct_session_operation(toolbox.ping_data())
        )
        try:
            toolbox.submit_through_bundler(operation)
        except BundlerRpcError as rejection:
            if "AA24" not in str(rejection):
                raise DrillFailureError(
                    f"unexpected rejection: {rejection}"
                ) from rejection
            self._require_unchanged(before)
            return f"rejected before execution: {rejection}"
        raise DrillFailureError("a session operation reached plain execute")

    def self_call_is_refused(self) -> str:
        toolbox = self._toolbox
        validator = toolbox.contracts.validator
        uninstall = toolbox.environment.foundry.encode_call(
            FunctionCall(
                "uninstallModule(uint256,address,bytes)",
                (VALIDATOR_MODULE_TYPE, validator, "0x"),
            )
        )
        call_data = toolbox.session_execute_data(
            ExecutionRequest(toolbox.actors.account, uninstall)
        )
        before = self._observe()
        self._include_directly(call_data, toolbox.compromised_quorum_signed)
        if not toolbox.is_installed(VALIDATOR_MODULE_TYPE, validator):
            raise DrillFailureError("a session operation uninstalled its own validator")
        self._require_refusal(before, self._observe())
        return "operation included, the hook refused the self-call, the validator is still installed"

    def cap_is_enforced(self) -> str:
        toolbox = self._toolbox
        before = self._observe()
        first = toolbox.estimated_session_operation(self._value_call_data(FIRST_SPEND))
        toolbox.submit_through_bundler(toolbox.quorum_signed(first))
        after_first = self._observe()
        self._require_spend(before, after_first, FIRST_SPEND)
        self._include_directly(
            self._value_call_data(REFUSED_SPEND), toolbox.compromised_quorum_signed
        )
        after_refused = self._observe()
        self._require_refusal(after_first, after_refused)
        self._include_directly(
            self._value_call_data(REMAINING_SPEND), toolbox.quorum_signed
        )
        after_remaining = self._observe()
        self._require_spend(after_refused, after_remaining, REMAINING_SPEND)
        if after_remaining.window_spend != NATIVE_CAP:
            raise DrillFailureError("the window did not add up to the cap")
        return "0.6 ETH sent, a further 0.5 ETH refused by the hook, the remaining 0.4 ETH sent"

    def _value_call_data(self, amount: int) -> bytes:
        toolbox = self._toolbox
        return toolbox.session_execute_data(
            ExecutionRequest(toolbox.contracts.target, b"", amount)
        )

    def _include_directly(
        self, call_data: bytes, sign: Callable[[UserOperation], UserOperation]
    ) -> None:
        toolbox = self._toolbox
        toolbox.submit_directly(sign(toolbox.direct_session_operation(call_data)))

    def _observe(self) -> Observation:
        toolbox = self._toolbox
        return Observation(
            toolbox.next_session_nonce(),
            toolbox.native_balance(toolbox.contracts.target),
            toolbox.spent_in_window(ZERO_ADDRESS),
        )

    def _require_unchanged(self, before: Observation) -> None:
        after = self._observe()
        if after != before:
            raise DrillFailureError("state changed although the operation was refused")

    def _require_refusal(self, before: Observation, after: Observation) -> None:
        if after.session_nonce != before.session_nonce + 1:
            raise DrillFailureError("the refused operation was not included on chain")
        if after.target_balance != before.target_balance:
            raise DrillFailureError("value moved although the operation was refused")
        if after.window_spend != before.window_spend:
            raise DrillFailureError("the window changed although the spend was refused")

    def _require_spend(
        self, before: Observation, after: Observation, amount: int
    ) -> None:
        if after.target_balance - before.target_balance != amount:
            raise DrillFailureError("the target did not receive the amount")
        if after.window_spend - before.window_spend != amount:
            raise DrillFailureError("the window did not record the amount")
