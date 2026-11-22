import pytest

from fintech_agent.application.agent_retirement import (
    RetireAgent,
    RetirementError,
    RetirementParts,
)
from fintech_agent.application.fleet_ledger import (
    FleetBudget,
    FleetLedger,
    FleetMember,
)
from fintech_agent.domain.fleet_identity import FleetIdentityError
from fintech_agent.domain.session_policy import NATIVE_ASSET

AGENT = "agent-one"
OTHER = "agent-two"
ACCOUNT = "0x" + "a1" * 20
OTHER_ACCOUNT = "0x" + "b2" * 20
APPROVED_TOTAL = 1_000
FUNDED = 400
SPENT = 150
SESSION_KEY = bytes.fromhex("02" + "44" * 32)


class FakeTeardown:
    def __init__(self, key: bytes = SESSION_KEY) -> None:
        self._key = key
        self.uninstalls = 0

    def uninstall_validator(self) -> None:
        self.uninstalls += 1
        self._key = b""

    def installed_key(self) -> bytes:
        return self._key


class StubbornTeardown(FakeTeardown):
    def uninstall_validator(self) -> None:
        self.uninstalls += 1


class FakeSweep:
    def __init__(self, held: int = FUNDED - SPENT) -> None:
        self._held = held
        self.sweeps = 0

    def balance(self) -> int:
        return self._held

    def sweep(self) -> int:
        self.sweeps += 1
        swept = self._held
        self._held = 0
        return swept


class LeakySweep(FakeSweep):
    def sweep(self) -> int:
        self.sweeps += 1
        return 0


class FakeEnclaves:
    def __init__(self) -> None:
        self.stops = 0
        self._running = True

    def stop(self) -> None:
        self.stops += 1
        self._running = False

    def reachable(self) -> bool:
        return self._running


class LivingEnclaves(FakeEnclaves):
    def stop(self) -> None:
        self.stops += 1


def ledger_with_two() -> FleetLedger:
    ledger = FleetLedger(FleetBudget(NATIVE_ASSET, APPROVED_TOTAL))
    ledger.admit(FleetMember(AGENT, ACCOUNT, FUNDED))
    ledger.admit(FleetMember(OTHER, OTHER_ACCOUNT, FUNDED))
    return ledger


def parts(
    teardown: FakeTeardown | None = None,
    sweep: FakeSweep | None = None,
    enclaves: FakeEnclaves | None = None,
    ledger: FleetLedger | None = None,
) -> RetirementParts:
    return RetirementParts(
        teardown or FakeTeardown(),
        sweep or FakeSweep(),
        enclaves or FakeEnclaves(),
        ledger or ledger_with_two(),
    )


def test_retirement_takes_the_key_the_allowance_and_the_enclaves() -> None:
    teardown, sweep, enclaves = FakeTeardown(), FakeSweep(), FakeEnclaves()
    ledger = ledger_with_two()
    outcome = RetireAgent(parts(teardown, sweep, enclaves, ledger)).execute(AGENT)
    assert outcome.swept == FUNDED - SPENT
    assert outcome.freed == FUNDED - SPENT
    assert (teardown.uninstalls, sweep.sweeps, enclaves.stops) == (1, 1, 1)
    assert teardown.installed_key() == b""
    assert sweep.balance() == 0
    assert not enclaves.reachable()


def test_a_retired_agent_leaves_the_fleet_and_frees_what_it_did_not_spend() -> None:
    ledger = ledger_with_two()
    before = ledger.remaining
    RetireAgent(parts(ledger=ledger)).execute(AGENT)
    assert ledger.remaining == before + FUNDED - SPENT
    assert ledger.member(AGENT).retired
    assert ledger.member(AGENT).funded == SPENT
    assert [member.agent_identifier for member in ledger.active] == [OTHER]


def test_an_agent_that_spent_everything_is_still_retired() -> None:
    ledger = ledger_with_two()
    before = ledger.remaining
    outcome = RetireAgent(parts(sweep=FakeSweep(held=0), ledger=ledger)).execute(AGENT)
    assert outcome.swept == 0
    assert outcome.freed == 0
    assert ledger.remaining == before
    assert ledger.member(AGENT).retired


def test_a_validator_that_survives_the_teardown_is_reported() -> None:
    with pytest.raises(RetirementError, match="still holds a session key"):
        RetireAgent(parts(teardown=StubbornTeardown())).execute(AGENT)


def test_an_allowance_that_survives_the_sweep_is_reported() -> None:
    with pytest.raises(RetirementError, match="still holds 250 of its allowance"):
        RetireAgent(parts(sweep=LeakySweep())).execute(AGENT)


def test_enclaves_that_survive_the_stop_are_reported() -> None:
    with pytest.raises(RetirementError, match="still has reachable enclaves"):
        RetireAgent(parts(enclaves=LivingEnclaves())).execute(AGENT)


def test_the_key_is_taken_before_the_allowance_is_swept() -> None:
    order: list[str] = []

    class RecordingTeardown(FakeTeardown):
        def uninstall_validator(self) -> None:
            order.append("uninstall")
            super().uninstall_validator()

    class RecordingSweep(FakeSweep):
        def sweep(self) -> int:
            order.append("sweep")
            return super().sweep()

    RetireAgent(parts(RecordingTeardown(), RecordingSweep())).execute(AGENT)
    assert order == ["uninstall", "sweep"]


def test_an_agent_outside_the_fleet_cannot_be_retired() -> None:
    ledger = FleetLedger(FleetBudget(NATIVE_ASSET, APPROVED_TOTAL))
    with pytest.raises(ValueError, match="not in the fleet"):
        RetireAgent(parts(ledger=ledger)).execute(AGENT)


def test_a_malformed_identifier_is_refused_before_anything_is_torn_down() -> None:
    teardown = FakeTeardown()
    with pytest.raises(FleetIdentityError):
        RetireAgent(parts(teardown=teardown)).execute("Agent One")
    assert teardown.uninstalls == 0
