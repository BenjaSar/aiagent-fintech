import pytest

from fintech_agent.application.agent_lifecycle import LifecycleError
from fintech_agent.application.renewal_scheduler import (
    Escalation,
    RenewalScheduler,
    SchedulerParts,
    ScheduleRun,
)
from fintech_agent.application.session_schedule import (
    RENEWAL_LEAD_SECONDS,
    ScheduleError,
)
from fintech_agent.domain.authority_order import AuthorityWidenedError

FIRST = "agent-one"
SECOND = "agent-two"
WIDENED = "agent-wide"


class Schedule:
    def __init__(self, *rounds: tuple[str, ...]) -> None:
        self.rounds = list(rounds)
        self.leads: list[int] = []

    def due(self, lead_seconds: int) -> tuple[str, ...]:
        self.leads.append(lead_seconds)
        if not self.rounds:
            return ()
        return self.rounds.pop(0)


class Renewer:
    def __init__(self) -> None:
        self.renewed: list[str] = []

    def renew(self, agent_identifier: str) -> None:
        self.renewed.append(agent_identifier)
        if agent_identifier == WIDENED:
            raise AuthorityWidenedError("a renewal cannot widen authority: cap")


class Pause:
    def __init__(self) -> None:
        self.seconds: list[int] = []

    def __call__(self, seconds: int) -> None:
        self.seconds.append(seconds)


def scheduler(schedule: Schedule, renewer: Renewer) -> RenewalScheduler:
    return RenewalScheduler(SchedulerParts(schedule, renewer, Pause()))


def test_a_tick_renews_every_due_session() -> None:
    renewer = Renewer()

    report = scheduler(Schedule((FIRST, SECOND)), renewer).tick()

    assert report.renewed == (FIRST, SECOND)
    assert renewer.renewed == [FIRST, SECOND]


def test_a_tick_with_nothing_due_renews_nothing() -> None:
    renewer = Renewer()

    report = scheduler(Schedule(()), renewer).tick()

    assert report == report.__class__((), ())
    assert renewer.renewed == []


def test_a_tick_asks_the_schedule_with_the_lead_it_was_given() -> None:
    schedule = Schedule(())

    scheduler(schedule, Renewer()).tick(7_200)

    assert schedule.leads == [7_200]


def test_a_tick_uses_the_standing_lead_by_default() -> None:
    schedule = Schedule(())

    scheduler(schedule, Renewer()).tick()

    assert schedule.leads == [RENEWAL_LEAD_SECONDS]


def test_a_renewal_that_would_widen_authority_is_escalated() -> None:
    report = scheduler(Schedule((WIDENED,)), Renewer()).tick()

    assert report.renewed == ()
    assert len(report.escalated) == 1
    assert report.escalated[0].agent_identifier == WIDENED
    assert "widen" in report.escalated[0].reason


def test_one_escalation_does_not_stop_the_other_renewals() -> None:
    renewer = Renewer()

    report = scheduler(Schedule((FIRST, WIDENED, SECOND)), renewer).tick()

    assert report.renewed == (FIRST, SECOND)
    assert [held.agent_identifier for held in report.escalated] == [WIDENED]
    assert renewer.renewed == [FIRST, WIDENED, SECOND]


def test_a_lifecycle_refusal_is_an_escalation_too() -> None:
    class Refusing:
        def renew(self, agent_identifier: str) -> None:
            raise LifecycleError(f"{agent_identifier} is retired")

    parts = SchedulerParts(Schedule((FIRST,)), Refusing(), Pause())

    report = RenewalScheduler(parts).tick()

    assert report.escalated == (Escalation(FIRST, "agent-one is retired"),)


def test_a_failure_that_is_not_an_escalation_stops_the_scheduler() -> None:
    class Broken:
        def renew(self, agent_identifier: str) -> None:
            raise OSError("the chain is unreachable")

    parts = SchedulerParts(Schedule((FIRST,)), Broken(), Pause())

    with pytest.raises(OSError, match="unreachable"):
        RenewalScheduler(parts).tick()


def test_a_run_keeps_ticking_and_gathers_every_renewal() -> None:
    schedule = Schedule((), (FIRST,), (SECOND,))
    renewer = Renewer()

    report = RenewalScheduler(SchedulerParts(schedule, renewer, Pause())).run(
        ScheduleRun(3, 60)
    )

    assert report.renewed == (FIRST, SECOND)
    assert schedule.leads == [RENEWAL_LEAD_SECONDS] * 3


def test_a_run_pauses_between_its_ticks() -> None:
    pause = Pause()
    parts = SchedulerParts(Schedule((), ()), Renewer(), pause)

    RenewalScheduler(parts).run(ScheduleRun(2, 300))

    assert pause.seconds == [300, 300]


def test_a_run_passes_its_lead_to_every_tick() -> None:
    schedule = Schedule((), ())

    parts = SchedulerParts(schedule, Renewer(), Pause())
    RenewalScheduler(parts).run(ScheduleRun(2, 60, 7_200))

    assert schedule.leads == [7_200, 7_200]


def test_a_run_gathers_the_escalations_as_well() -> None:
    parts = SchedulerParts(Schedule((WIDENED,), ()), Renewer(), Pause())

    report = RenewalScheduler(parts).run(ScheduleRun(2, 60))

    assert len(report.escalated) == 1


def test_a_run_needs_at_least_one_tick() -> None:
    with pytest.raises(ScheduleError, match="one tick"):
        ScheduleRun(0, 60)


def test_a_run_cannot_pause_for_a_negative_time() -> None:
    with pytest.raises(ScheduleError, match="negative"):
        ScheduleRun(1, -1)


def test_a_run_needs_a_positive_lead() -> None:
    with pytest.raises(ScheduleError, match="lead"):
        ScheduleRun(1, 60, 0)
