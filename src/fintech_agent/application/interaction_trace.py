from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

ROOT = ""


class TraceError(Exception):
    pass


@dataclass(frozen=True)
class SpanRequest:
    agent_identifier: str
    step: str
    parent: str = ROOT
    detail: str = ""


@dataclass(frozen=True)
class Span:
    identifier: str
    parent: str
    agent_identifier: str
    step: str
    at: int
    detail: str


@dataclass(frozen=True)
class Crossing:
    cause: str
    effect: str
    step: str


class InteractionTrace:
    def __init__(self, clock: Callable[[], int]) -> None:
        self._clock = clock
        self._spans: list[Span] = []

    @property
    def spans(self) -> tuple[Span, ...]:
        return tuple(self._spans)

    def record(self, request: SpanRequest) -> str:
        self._require_known(request.parent)
        span = Span(
            f"s{len(self._spans) + 1}",
            request.parent,
            request.agent_identifier,
            request.step,
            self._clock(),
            request.detail,
        )
        self._spans.append(span)
        return span.identifier

    def _require_known(self, parent: str) -> None:
        if parent == ROOT:
            return
        if parent not in {span.identifier for span in self._spans}:
            raise TraceError(f"{parent} is not a recorded span")


@dataclass(frozen=True)
class RunEvent:
    step: str
    detail: str = ""


class RunTrace(Protocol):
    def record(self, event: RunEvent) -> None: ...


class NoTrace:
    def record(self, event: RunEvent) -> None:
        return None


@dataclass(frozen=True)
class TraceParts:
    trace: InteractionTrace
    agent_identifier: str
    parent: str = ROOT


class AgentTrace:
    def __init__(self, parts: TraceParts) -> None:
        self._parts = parts
        self._last = parts.parent

    @property
    def last(self) -> str:
        return self._last

    def record(self, event: RunEvent) -> None:
        self._last = self._parts.trace.record(
            SpanRequest(
                self._parts.agent_identifier, event.step, self._last, event.detail
            )
        )


def reconstruct(spans: Sequence[Span]) -> tuple[Span, ...]:
    children = _children_of(spans)
    ordered: list[Span] = []
    pending = list(children.get(ROOT, ()))
    while pending:
        span = pending.pop(0)
        ordered.append(span)
        pending = list(children.get(span.identifier, ())) + pending
    if len(ordered) != len(spans):
        raise TraceError("the trace does not hang together")
    return tuple(ordered)


def crossings(spans: Sequence[Span]) -> tuple[Crossing, ...]:
    by_identifier = {span.identifier: span for span in spans}
    return tuple(
        Crossing(
            by_identifier[span.parent].agent_identifier,
            span.agent_identifier,
            span.step,
        )
        for span in spans
        if span.parent in by_identifier
        and by_identifier[span.parent].agent_identifier != span.agent_identifier
    )


def per_agent_order(spans: Sequence[Span]) -> tuple[Span, ...]:
    grouped: dict[str, list[Span]] = {}
    for span in spans:
        grouped.setdefault(span.agent_identifier, []).append(span)
    return tuple(span for group in grouped.values() for span in group)


def _children_of(spans: Sequence[Span]) -> dict[str, list[Span]]:
    children: dict[str, list[Span]] = {}
    for span in spans:
        children.setdefault(span.parent, []).append(span)
    return children
