import pytest

from fintech_agent.application.fleet_ledger import (
    FleetBudget,
    FleetBudgetError,
    FleetLedger,
    FleetLedgerError,
    FleetMember,
)
from fintech_agent.domain.fleet_identity import FleetIdentityError
from fintech_agent.domain.session_policy import NATIVE_ASSET

APPROVED_TOTAL = 1_000
FIRST_ACCOUNT = "0x" + "a1" * 20
SECOND_ACCOUNT = "0x" + "b2" * 20
THIRD_ACCOUNT = "0x" + "c3" * 20


def ledger(approved_total: int = APPROVED_TOTAL) -> FleetLedger:
    return FleetLedger(FleetBudget(NATIVE_ASSET, approved_total))


def first(funded: int = 400) -> FleetMember:
    return FleetMember("agent-one", FIRST_ACCOUNT, funded)


def second(funded: int = 400) -> FleetMember:
    return FleetMember("agent-two", SECOND_ACCOUNT, funded)


def test_an_empty_ledger_has_the_approved_total_remaining() -> None:
    fleet = ledger()
    assert fleet.total_funded == 0
    assert fleet.remaining == APPROVED_TOTAL
    assert fleet.active == ()


def test_admitted_members_consume_the_approved_total() -> None:
    fleet = ledger()
    fleet.admit(first())
    fleet.admit(second())
    assert fleet.total_funded == 800
    assert fleet.remaining == 200
    assert [member.agent_identifier for member in fleet.active] == [
        "agent-one",
        "agent-two",
    ]


def test_funding_beyond_the_approved_total_is_refused() -> None:
    fleet = ledger()
    fleet.admit(first())
    with pytest.raises(FleetBudgetError, match="approved total"):
        fleet.admit(second(601))
    assert fleet.total_funded == 400


def test_a_member_may_take_exactly_what_remains() -> None:
    fleet = ledger()
    fleet.admit(first())
    fleet.admit(second(600))
    assert fleet.remaining == 0


def test_an_agent_cannot_be_admitted_twice() -> None:
    fleet = ledger()
    fleet.admit(first())
    with pytest.raises(FleetLedgerError, match="already in the fleet"):
        fleet.admit(FleetMember("agent-one", THIRD_ACCOUNT, 100))


def test_two_agents_cannot_share_an_account() -> None:
    fleet = ledger()
    fleet.admit(first())
    with pytest.raises(FleetLedgerError, match="already belongs"):
        fleet.admit(FleetMember("agent-two", FIRST_ACCOUNT, 100))


@pytest.mark.parametrize(
    "member",
    [
        FleetMember("agent-one", FIRST_ACCOUNT, 0),
        FleetMember("agent-one", FIRST_ACCOUNT, -1),
        FleetMember("agent-one", FIRST_ACCOUNT.upper().replace("0X", "0x"), 100),
        FleetMember("agent-one", "0xabc", 100),
        FleetMember("agent-one", FIRST_ACCOUNT, 100, retired=True),
    ],
)
def test_a_malformed_member_is_refused(member: FleetMember) -> None:
    with pytest.raises(FleetLedgerError):
        ledger().admit(member)


def test_the_treasury_and_malformed_identifiers_are_refused() -> None:
    with pytest.raises(FleetIdentityError):
        ledger().admit(FleetMember("treasury", FIRST_ACCOUNT, 100))
    with pytest.raises(FleetIdentityError):
        ledger().admit(FleetMember("Agent-One", FIRST_ACCOUNT, 100))


@pytest.mark.parametrize(
    "budget", [(NATIVE_ASSET, 0), (NATIVE_ASSET, -1), ("0xzz", 10)]
)
def test_a_malformed_budget_is_refused(budget: tuple[str, int]) -> None:
    with pytest.raises(FleetLedgerError):
        FleetBudget(*budget)


def test_retiring_an_agent_does_not_free_its_funding() -> None:
    fleet = ledger()
    fleet.admit(first())
    fleet.retire("agent-one")
    assert fleet.remaining == APPROVED_TOTAL - 400
    assert fleet.active == ()
    assert fleet.member("agent-one").retired


def test_only_a_recorded_sweep_frees_funding() -> None:
    fleet = ledger()
    fleet.admit(first())
    fleet.retire("agent-one")
    fleet.record_sweep("agent-one", 300)
    assert fleet.remaining == APPROVED_TOTAL - 100
    fleet.record_sweep("agent-one", 100)
    assert fleet.remaining == APPROVED_TOTAL


def test_a_sweep_needs_a_retired_agent_and_a_plausible_amount() -> None:
    fleet = ledger()
    fleet.admit(first())
    with pytest.raises(FleetLedgerError, match="retired"):
        fleet.record_sweep("agent-one", 100)
    fleet.retire("agent-one")
    with pytest.raises(FleetLedgerError, match="outside what was funded"):
        fleet.record_sweep("agent-one", 401)
    with pytest.raises(FleetLedgerError, match="outside what was funded"):
        fleet.record_sweep("agent-one", 0)


def test_a_retired_agent_cannot_be_re_admitted_and_keeps_its_account() -> None:
    fleet = ledger()
    fleet.admit(first())
    fleet.retire("agent-one")
    with pytest.raises(FleetLedgerError, match="already in the fleet"):
        fleet.admit(first())
    with pytest.raises(FleetLedgerError, match="already belongs"):
        fleet.admit(FleetMember("agent-two", FIRST_ACCOUNT, 100))


def test_an_unknown_agent_is_reported() -> None:
    with pytest.raises(FleetLedgerError, match="is not in the fleet"):
        ledger().member("agent-nine")
    with pytest.raises(FleetLedgerError, match="is not in the fleet"):
        ledger().retire("agent-nine")
