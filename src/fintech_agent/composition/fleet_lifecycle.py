from dataclasses import dataclass

from fintech_agent.application.session_rotation import (
    RotationApproval,
    RotationReview,
)
from fintech_agent.composition.drill_toolbox import (
    VALIDATOR_MODULE_TYPE,
    AccountContext,
    DrillToolbox,
    ExecutionRequest,
    ModuleInstall,
    OwnerOperation,
)
from fintech_agent.composition.local_enclaves import (
    EnclaveSet,
    evict_tenant,
    tenant_is_reachable,
)
from fintech_agent.domain.policy_approval import Acknowledgement


class UnattendedRenewalApproval:
    def review(self, review: RotationReview) -> RotationApproval:
        return RotationApproval(
            Acknowledgement(
                review.screen.policy_digest, review.screen.required_acknowledgements
            ),
            review.key_fingerprint,
        )


@dataclass(frozen=True)
class SweepTarget:
    context: AccountContext
    treasury: str


class OnChainTeardown:
    def __init__(self, toolbox: DrillToolbox, context: AccountContext) -> None:
        self._toolbox = toolbox
        self._context = context

    def uninstall_validator(self) -> None:
        toolbox = self._toolbox
        validator = toolbox.contracts.validator
        with toolbox.acting_as(self._context):
            if toolbox.is_installed(VALIDATOR_MODULE_TYPE, validator):
                toolbox.owner_uninstalls(
                    ModuleInstall(VALIDATOR_MODULE_TYPE, validator, b"")
                )

    def installed_key(self) -> bytes:
        toolbox = self._toolbox
        with toolbox.acting_as(self._context):
            return toolbox.stored_session_key()


class TreasurySweep:
    def __init__(self, toolbox: DrillToolbox, target: SweepTarget) -> None:
        self._toolbox = toolbox
        self._target = target

    def balance(self) -> int:
        return self._toolbox.native_balance(self._target.context.account)

    def sweep(self) -> int:
        amount = self.balance()
        if not amount:
            return 0
        self._send(amount)
        return amount

    def _send(self, amount: int) -> None:
        toolbox = self._toolbox
        with toolbox.acting_as(self._target.context):
            toolbox.owner_submits(
                OwnerOperation(
                    b"",
                    toolbox.execute_data(
                        ExecutionRequest(self._target.treasury, b"", amount)
                    ),
                )
            )


class LocalEnclaveTenancy:
    def __init__(self, enclaves: EnclaveSet) -> None:
        self._enclaves = enclaves

    def stop(self) -> None:
        evict_tenant(self._enclaves)

    def reachable(self) -> bool:
        return tenant_is_reachable(self._enclaves)
