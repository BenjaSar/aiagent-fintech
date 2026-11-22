import pytest

from fintech_agent.application.interaction_trace import (
    ROOT,
    AgentTrace,
    Crossing,
    InteractionTrace,
    NoTrace,
    RunEvent,
    Span,
    SpanRequest,
    TraceError,
    TraceParts,
    crossings,
    per_agent_order,
    reconstruct,
)
from tests.policy_fixtures import NOW

FIRST = "agent-one"
SECOND = "agent-two"


def trace() -> InteractionTrace:
    return InteractionTrace(lambda: NOW)


def steps(spans: tuple[Span, ...]) -> list[tuple[str, str]]:
    return [(span.agent_identifier, span.step) for span in spans]


def sequence() -> InteractionTrace:
    recorded = trace()
    paid = recorded.record(SpanRequest(FIRST, "pay"))
    asked = recorded.record(SpanRequest(SECOND, "task", paid))
    settled = recorded.record(SpanRequest(SECOND, "pay", asked))
    recorded.record(SpanRequest(FIRST, "reconcile", settled))
    return recorded


def test_a_recorded_span_carries_its_agent_step_and_time() -> None:
    recorded = trace()

    recorded.record(SpanRequest(FIRST, "pay", ROOT, "0.02 ETH"))

    assert recorded.spans == (Span("s1", ROOT, FIRST, "pay", NOW, "0.02 ETH"),)


def test_span_identifiers_are_handed_out_in_order() -> None:
    recorded = trace()

    first = recorded.record(SpanRequest(FIRST, "task"))
    second = recorded.record(SpanRequest(FIRST, "pay", first))

    assert (first, second) == ("s1", "s2")


def test_a_span_naming_an_unrecorded_parent_is_refused() -> None:
    recorded = trace()

    with pytest.raises(TraceError, match="s7"):
        recorded.record(SpanRequest(FIRST, "pay", "s7"))


def test_a_sequence_is_reconstructed_in_causal_order() -> None:
    assert steps(reconstruct(sequence().spans)) == [
        (FIRST, "pay"),
        (SECOND, "task"),
        (SECOND, "pay"),
        (FIRST, "reconcile"),
    ]


def test_a_reconstruction_puts_a_parent_before_its_children() -> None:
    recorded = trace()
    first = recorded.record(SpanRequest(FIRST, "task"))
    recorded.record(SpanRequest(FIRST, "read", first))
    recorded.record(SpanRequest(FIRST, "pay", first))

    ordered = reconstruct(recorded.spans)

    assert steps(ordered) == [(FIRST, "task"), (FIRST, "read"), (FIRST, "pay")]


def test_a_reconstruction_follows_one_cause_through_before_the_next() -> None:
    recorded = trace()
    paid = recorded.record(SpanRequest(FIRST, "pay"))
    asked = recorded.record(SpanRequest(SECOND, "task", paid))
    recorded.record(SpanRequest(FIRST, "note", paid))
    recorded.record(SpanRequest(SECOND, "pay", asked))

    assert steps(reconstruct(recorded.spans)) == [
        (FIRST, "pay"),
        (SECOND, "task"),
        (SECOND, "pay"),
        (FIRST, "note"),
    ]


def test_an_empty_trace_reconstructs_to_nothing() -> None:
    assert reconstruct(()) == ()


def test_a_sequence_missing_its_root_cannot_be_reconstructed() -> None:
    spans = sequence().spans

    with pytest.raises(TraceError, match="hang together"):
        reconstruct(spans[1:])


def test_a_span_whose_parent_is_its_own_child_cannot_be_reconstructed() -> None:
    circular = (
        Span("s1", "s2", FIRST, "pay", NOW, ""),
        Span("s2", "s1", SECOND, "pay", NOW, ""),
    )

    with pytest.raises(TraceError, match="hang together"):
        reconstruct(circular)


def test_the_crossings_name_the_agent_that_caused_the_next_one_to_act() -> None:
    assert crossings(sequence().spans) == (
        Crossing(FIRST, SECOND, "task"),
        Crossing(SECOND, FIRST, "reconcile"),
    )


def test_one_agent_acting_alone_crosses_to_nobody() -> None:
    recorded = trace()
    first = recorded.record(SpanRequest(FIRST, "task"))
    recorded.record(SpanRequest(FIRST, "pay", first))

    assert crossings(recorded.spans) == ()


def test_each_agents_own_record_loses_the_order_of_the_interaction() -> None:
    spans = sequence().spans

    assert steps(per_agent_order(spans)) == [
        (FIRST, "pay"),
        (FIRST, "reconcile"),
        (SECOND, "task"),
        (SECOND, "pay"),
    ]
    assert per_agent_order(spans) != reconstruct(spans)


def test_a_single_agents_record_is_its_own_interaction() -> None:
    recorded = trace()
    first = recorded.record(SpanRequest(FIRST, "task"))
    recorded.record(SpanRequest(FIRST, "pay", first))

    assert per_agent_order(recorded.spans) == reconstruct(recorded.spans)


def test_an_agents_trace_chains_each_event_to_the_one_before_it() -> None:
    recorded = trace()
    agent = AgentTrace(TraceParts(recorded, FIRST))

    agent.record(RunEvent("task"))
    agent.record(RunEvent("pay", "0.02 ETH"))

    assert [(span.identifier, span.parent) for span in recorded.spans] == [
        ("s1", ROOT),
        ("s2", "s1"),
    ]


def test_an_agents_trace_hangs_its_first_event_off_the_given_parent() -> None:
    recorded = trace()
    caused = recorded.record(SpanRequest(FIRST, "pay"))

    AgentTrace(TraceParts(recorded, SECOND, caused)).record(RunEvent("task"))

    assert crossings(recorded.spans) == (Crossing(FIRST, SECOND, "task"),)


def test_an_agents_trace_reports_its_newest_span() -> None:
    recorded = trace()
    agent = AgentTrace(TraceParts(recorded, FIRST))
    agent.record(RunEvent("task"))

    agent.record(RunEvent("pay"))

    assert agent.last == "s2"


def test_an_agent_without_a_trace_records_nothing() -> None:
    recorded = trace()

    NoTrace().record(RunEvent("pay"))

    assert recorded.spans == ()
