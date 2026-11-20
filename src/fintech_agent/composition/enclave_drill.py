from dataclasses import dataclass, replace

from fintech_agent.adapters.foundry_toolkit import FunctionCall
from fintech_agent.application.ports import EnclaveRefusedError
from fintech_agent.application.sign_user_operation import (
    SigningFailedError,
    SigningRequest,
    SignUserOperation,
)
from fintech_agent.composition.drill_toolbox import (
    ENTRY_POINT,
    VALIDATOR_MODULE_TYPE,
    ZERO_ADDRESS,
    DrillFailureError,
    DrillToolbox,
    ExecutionRequest,
)
from fintech_agent.composition.local_enclaves import (
    build_signing_setup,
    issue_session_policy,
)
from fintech_agent.composition.policy_drill import NATIVE_CAP
from fintech_agent.domain.session_policy import AssetRule, SessionPolicy
from fintech_agent.domain.user_operation import PaymasterFields, UserOperation
from fintech_agent.domain.user_operation_hash import HashingDomain

WINDOW_SECONDS = 86_400
MAXIMUM_GAS_COST = 10**17
OVER_CAP_VALUE = 3 * NATIVE_CAP // 2
EXCESSIVE_CALL_GAS = 10**9
STRANGER_ADDRESS = "0x" + "de" * 20
ONE_WEI = 1


@dataclass(frozen=True)
class Violation:
    description: str
    operation: UserOperation
    expected_reason: str
    hashed: UserOperation | None = None
    blind: bool = False


class EnclaveChecks:
    def __init__(self, toolbox: DrillToolbox) -> None:
        self._toolbox = toolbox

    def policy_is_issued(self) -> str:
        toolbox = self._toolbox
        policy = self._policy()
        root = toolbox.enclave_root
        issue_session_policy(root, policy)
        toolbox.active_policy = policy
        try:
            issue_session_policy(root, policy)
        except EnclaveRefusedError as refusal:
            return (
                f"installed on three enclaves; a second issuance is refused: {refusal}"
            )
        raise DrillFailureError("an enclave accepted a second session policy")

    def violations_are_refused(self) -> str:
        toolbox = self._toolbox
        valid = self._operation(toolbox.session_ping_data())
        for violation in self._violations(valid):
            self._require_refusal(violation)
        return "eight violations refused by the enclaves, each with its own reason; no share was released"

    def ledger_refuses_over_cap(self) -> str:
        operation = self._operation(self._value_call_data(ONE_WEI))
        self._require_refusal(
            Violation(
                f"{ONE_WEI} wei after the window reached the cap",
                operation,
                "rolling cap",
            )
        )
        return f"after {NATIVE_CAP} wei were signed in the window, one more wei is refused by the enclaves"

    def _violations(self, valid: UserOperation) -> list[Violation]:
        toolbox = self._toolbox
        stranger = toolbox.session_execute_data(
            ExecutionRequest(STRANGER_ADDRESS, b"", 0)
        )
        over_cap = self._value_call_data(OVER_CAP_VALUE)
        uninstall = toolbox.environment.foundry.encode_call(
            FunctionCall(
                "uninstallModule(uint256,address,bytes)",
                (VALIDATOR_MODULE_TYPE, toolbox.contracts.validator, "0x"),
            )
        )
        self_call = toolbox.session_execute_data(
            ExecutionRequest(toolbox.actors.account, uninstall)
        )
        foreign = PaymasterFields(STRANGER_ADDRESS, 1, 1, b"")
        return [
            Violation(
                "no operation, only a hash", valid, "no user operation", blind=True
            ),
            Violation(
                "a hash that is not the operation's",
                valid,
                "does not match",
                self._operation(over_cap),
            ),
            Violation(
                "a target outside the allowlist",
                self._operation(stranger),
                "not allowed",
            ),
            Violation("a value over the cap", self._operation(over_cap), "rolling cap"),
            Violation(
                "a call to the account itself", self._operation(self_call), "forbidden"
            ),
            Violation(
                "the plain execute entry point",
                self._operation(toolbox.ping_data()),
                "executeAsSession",
            ),
            Violation(
                "a paymaster outside the policy",
                valid.with_paymaster(foreign),
                "paymaster",
            ),
            Violation(
                "gas far above the limit",
                valid.with_gas_limits(
                    replace(valid.gas_limits, call_gas_limit=EXCESSIVE_CALL_GAS)
                ),
                "gas",
            ),
        ]

    def _require_refusal(self, violation: Violation) -> None:
        toolbox = self._toolbox
        signing = SignUserOperation(build_signing_setup(toolbox.enclave_root))
        presented = violation.operation
        message = toolbox.operation_hash(violation.hashed or presented)
        operation = None if violation.blind else presented
        try:
            signing.execute(SigningRequest(message, operation))
        except SigningFailedError as failure:
            reason = str(failure.__cause__)
            if violation.expected_reason not in reason:
                raise DrillFailureError(
                    f"{violation.description}: refused for another reason: {reason}"
                ) from failure
            return
        raise DrillFailureError(f"the enclaves signed: {violation.description}")

    def _value_call_data(self, amount: int) -> bytes:
        toolbox = self._toolbox
        return toolbox.session_execute_data(
            ExecutionRequest(toolbox.contracts.target, b"", amount)
        )

    def _operation(self, call_data: bytes) -> UserOperation:
        return self._toolbox.direct_session_operation(call_data)

    def _policy(self) -> SessionPolicy:
        toolbox = self._toolbox
        contracts, actors = toolbox.contracts, toolbox.actors
        return SessionPolicy(
            domain=HashingDomain(toolbox.chain_id(), _normal(ENTRY_POINT)),
            account=_normal(actors.account),
            validator=_normal(contracts.validator),
            hook=_normal(contracts.policy_hook),
            allowed_targets=frozenset({_normal(contracts.target)}),
            rules=(AssetRule(ZERO_ADDRESS, NATIVE_CAP),),
            allowed_paymasters=frozenset({_normal(contracts.paymaster)}),
            valid_after=0,
            valid_until=toolbox.session_valid_until,
            window_seconds=WINDOW_SECONDS,
            maximum_gas_cost=MAXIMUM_GAS_COST,
        )


def _normal(address: str) -> str:
    return address.lower()
