import pytest

from fintech_agent.adapters.in_memory import InMemorySealedStorage
from fintech_agent.application.halt_switch import (
    HALT_NAME,
    UNREADABLE_REASON,
    GuardParts,
    HaltedError,
    HaltGuard,
    HaltOrder,
    HaltScope,
    HaltScopes,
    HaltSwitch,
)
from fintech_agent.application.ports import AuthorizationRequest
from tests.policy_fixtures import NOW

FLEET_REASON = "the fleet is stopped for an incident"
AGENT_REASON = "this agent is stopped for a review"


class Recorder:
    def __init__(self) -> None:
        self.requests: list[AuthorizationRequest] = []

    def authorize(self, request: AuthorizationRequest) -> None:
        self.requests.append(request)


def scopes() -> HaltScopes:
    return HaltScopes(InMemorySealedStorage(), InMemorySealedStorage())


def fleet_order(reason: str = FLEET_REASON) -> HaltOrder:
    return HaltOrder(HaltScope.FLEET, reason, NOW)


def agent_order(reason: str = AGENT_REASON) -> HaltOrder:
    return HaltOrder(HaltScope.AGENT, reason, NOW)


def request() -> AuthorizationRequest:
    return AuthorizationRequest(bytes(32), None)


def test_nothing_is_in_force_before_any_order() -> None:
    assert HaltSwitch(scopes()).in_force() is None


def test_a_fleet_halt_is_in_force_with_its_reason() -> None:
    switch = HaltSwitch(scopes())

    switch.halt(fleet_order())

    assert switch.in_force() == fleet_order()


def test_an_agent_halt_is_in_force_with_its_own_scope() -> None:
    switch = HaltSwitch(scopes())

    switch.halt(agent_order())

    assert switch.in_force() == agent_order()


def test_the_fleet_halt_is_reported_first_when_both_are_ordered() -> None:
    switch = HaltSwitch(scopes())
    switch.halt(agent_order())

    switch.halt(fleet_order())

    assert switch.in_force() == fleet_order()


def test_a_resumed_fleet_runs_again() -> None:
    switch = HaltSwitch(scopes())
    switch.halt(fleet_order())

    switch.resume(fleet_order("the incident is closed"))

    assert switch.in_force() is None


def test_resuming_one_agent_leaves_the_fleet_halt_standing() -> None:
    switch = HaltSwitch(scopes())
    switch.halt(fleet_order())
    switch.halt(agent_order())

    switch.resume(agent_order("the review is closed"))

    assert switch.in_force() == fleet_order()


def test_resuming_the_fleet_leaves_an_agent_halt_standing() -> None:
    switch = HaltSwitch(scopes())
    switch.halt(fleet_order())
    switch.halt(agent_order())

    switch.resume(fleet_order("the incident is closed"))

    assert switch.in_force() == agent_order()


def test_one_agents_halt_does_not_halt_another() -> None:
    fleet = InMemorySealedStorage()
    first = HaltSwitch(HaltScopes(fleet, InMemorySealedStorage()))
    second = HaltSwitch(HaltScopes(fleet, InMemorySealedStorage()))

    first.halt(agent_order())

    assert first.in_force() == agent_order()
    assert second.in_force() is None


def test_a_fleet_halt_halts_every_agent_on_the_same_process() -> None:
    fleet = InMemorySealedStorage()
    first = HaltSwitch(HaltScopes(fleet, InMemorySealedStorage()))
    second = HaltSwitch(HaltScopes(fleet, InMemorySealedStorage()))

    first.halt(fleet_order())

    assert second.in_force() == fleet_order()


def test_the_order_survives_a_new_switch_over_the_same_storage() -> None:
    held = scopes()
    HaltSwitch(held).halt(fleet_order())

    assert HaltSwitch(held).in_force() == fleet_order()


def test_an_unreadable_record_fails_closed() -> None:
    held = scopes()
    held.fleet.seal(HALT_NAME, b"not json at all")

    order = HaltSwitch(held).in_force()

    assert order == HaltOrder(HaltScope.FLEET, UNREADABLE_REASON, 0)


def test_a_record_that_is_not_a_document_fails_closed() -> None:
    held = scopes()
    held.agent.seal(HALT_NAME, b"[]")

    order = HaltSwitch(held).in_force()

    assert order == HaltOrder(HaltScope.AGENT, UNREADABLE_REASON, 0)


def test_a_record_without_a_time_still_halts() -> None:
    held = scopes()
    held.agent.seal(HALT_NAME, b'{"halted": true, "reason": "no time"}')

    assert HaltSwitch(held).in_force() == HaltOrder(HaltScope.AGENT, "no time", 0)


def test_the_guard_passes_a_request_through_when_nothing_is_halted() -> None:
    recorder = Recorder()
    guard = HaltGuard(GuardParts(HaltSwitch(scopes()), recorder))

    guard.authorize(request())

    assert recorder.requests == [request()]


def test_the_guard_refuses_a_halted_agent_without_asking_the_policy() -> None:
    switch = HaltSwitch(scopes())
    switch.halt(agent_order())
    recorder = Recorder()

    with pytest.raises(HaltedError, match=AGENT_REASON):
        HaltGuard(GuardParts(switch, recorder)).authorize(request())
    assert recorder.requests == []


def test_the_guard_names_the_scope_that_stopped_the_request() -> None:
    switch = HaltSwitch(scopes())
    switch.halt(fleet_order())

    with pytest.raises(HaltedError, match="the fleet is halted"):
        HaltGuard(GuardParts(switch, Recorder())).authorize(request())


def test_the_guard_signs_again_once_the_halt_is_lifted() -> None:
    switch = HaltSwitch(scopes())
    switch.halt(fleet_order())
    recorder = Recorder()
    guard = HaltGuard(GuardParts(switch, recorder))

    switch.resume(fleet_order("the incident is closed"))
    guard.authorize(request())

    assert recorder.requests == [request()]
