from dataclasses import dataclass

from fintech_agent.composition.drill_toolbox import DrillFailureError, DrillToolbox


@dataclass(frozen=True)
class Observation:
    session_nonce: int
    pings: int


class SanctionsChecks:
    def __init__(self, toolbox: DrillToolbox) -> None:
        self._toolbox = toolbox

    def sanctioned_target_is_refused(self) -> str:
        toolbox = self._toolbox
        target = toolbox.contracts.target
        toolbox.set_sanctioned(target, True)
        before = self._observe()
        self._session_ping_included()
        self._require_refused(before)
        toolbox.set_sanctioned(target, False)
        control = self._observe()
        self._session_ping_included()
        self._require_executed(control)
        return (
            "an allowlisted target listed by the oracle is refused after the enclaves "
            "signed; delisted, the same kind of operation executes"
        )

    def oracle_outage_fails_closed(self) -> str:
        toolbox = self._toolbox
        toolbox.set_oracle_unavailable(True)
        try:
            before = self._observe()
            self._session_ping_included()
            self._require_refused(before)
            owner_before = self._observe()
            toolbox.owner_ping_included_directly()
            if self._observe().pings != owner_before.pings + 1:
                raise DrillFailureError("the owner path depends on the oracle")
        finally:
            toolbox.set_oracle_unavailable(False)
        control = self._observe()
        self._session_ping_included()
        self._require_executed(control)
        return (
            "session operation refused while the oracle is down, owner operation "
            "unaffected, session operation executes when it is back"
        )

    def _session_ping_included(self) -> None:
        toolbox = self._toolbox
        operation = toolbox.direct_session_operation(toolbox.session_ping_data())
        toolbox.submit_directly(toolbox.quorum_signed(operation))

    def _observe(self) -> Observation:
        toolbox = self._toolbox
        return Observation(toolbox.next_session_nonce(), toolbox.pings())

    def _require_refused(self, before: Observation) -> None:
        after = self._observe()
        if after.session_nonce != before.session_nonce + 1:
            raise DrillFailureError("the refused operation was not included on chain")
        if after.pings != before.pings:
            raise DrillFailureError("the call executed although it was refused")

    def _require_executed(self, before: Observation) -> None:
        after = self._observe()
        if after.session_nonce != before.session_nonce + 1:
            raise DrillFailureError("the control operation was not included on chain")
        if after.pings != before.pings + 1:
            raise DrillFailureError("the control operation did not execute")
