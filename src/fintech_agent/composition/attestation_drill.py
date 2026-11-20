from fintech_agent.adapters.foundry_toolkit import FoundryError
from fintech_agent.composition.drill_toolbox import (
    EXECUTOR_MODULE_TYPE,
    DrillFailureError,
    DrillToolbox,
    ModuleInstall,
)

NOT_ATTESTED = "AttestationThresholdNotMet(uint256,uint256)"
REVOKED = "AttestationWasRevoked(address,address)"


class AttestationChecks:
    def __init__(self, toolbox: DrillToolbox) -> None:
        self._toolbox = toolbox

    def unattested_install_is_refused(self) -> str:
        toolbox = self._toolbox
        install = self._unattested_install()
        self._require_registry_refusal(install, NOT_ATTESTED)
        self._require_included_without_effect(install)
        toolbox.attest(install.module, EXECUTOR_MODULE_TYPE)
        toolbox.owner_install_included_directly(install)
        self._require_installed(install, True)
        toolbox.owner_uninstalls(install)
        self._require_installed(install, False)
        return (
            "registry refused the unattested module and the install changed nothing; "
            "after the attester attested the same bytecode at that address it installed"
        )

    def revoked_attestation_blocks_install(self) -> str:
        toolbox = self._toolbox
        install = self._unattested_install()
        toolbox.registry_check(install)
        toolbox.revoke_attestation(install.module)
        self._require_registry_refusal(install, REVOKED)
        self._require_included_without_effect(install)
        return "after the attester revoked, the registry refuses and the install changes nothing"

    def _unattested_install(self) -> ModuleInstall:
        toolbox = self._toolbox
        return ModuleInstall(
            EXECUTOR_MODULE_TYPE,
            toolbox.contracts.unattested,
            toolbox.recovery_settings(),
        )

    def _require_registry_refusal(self, install: ModuleInstall, error: str) -> None:
        selector = self._toolbox.environment.foundry.error_selector(error)
        try:
            self._toolbox.registry_check(install)
        except FoundryError as refusal:
            if selector not in str(refusal).lower():
                raise DrillFailureError(f"unexpected refusal: {refusal}") from refusal
            return
        raise DrillFailureError(f"the registry did not refuse with {error}")

    def _require_included_without_effect(self, install: ModuleInstall) -> None:
        toolbox = self._toolbox
        nonce_before = toolbox.next_owner_nonce()
        toolbox.owner_install_included_directly(install)
        if toolbox.next_owner_nonce() != nonce_before + 1:
            raise DrillFailureError("the refused install was not included on chain")
        self._require_installed(install, False)

    def _require_installed(self, install: ModuleInstall, expected: bool) -> None:
        installed = self._toolbox.is_installed(install.module_type, install.module)
        if installed != expected:
            raise DrillFailureError(
                f"module {install.module} installed state is {installed}, expected {expected}"
            )
