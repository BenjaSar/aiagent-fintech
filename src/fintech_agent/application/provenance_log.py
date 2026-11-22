from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.agent_definition_document import (
    definition_digest,
    definition_from_document,
    definition_to_document,
    document_digest,
)
from fintech_agent.domain.fleet_identity import require_agent_identifier
from fintech_agent.domain.plain_text import is_plain_text

GENESIS_DIGEST = "0" * 64
MAXIMUM_DETAIL = 256
MAXIMUM_DETAILS = 16


class ProvenanceError(ValueError):
    pass


class ProvenanceEvent(Enum):
    GENERATED = "generated"
    DERIVED = "derived"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    INSTALLED = "installed"
    RENEWED = "renewed"
    RETIRED = "retired"


@dataclass(frozen=True)
class ProvenanceRecord:
    agent_identifier: str
    event: ProvenanceEvent
    recorded_at: int
    details: Mapping[str, str]
    definition: AgentDefinition | None = None


@dataclass(frozen=True)
class ProvenanceEntry:
    sequence: int
    agent_identifier: str
    event: ProvenanceEvent
    recorded_at: int
    details: Mapping[str, str]
    definition_document: Mapping[str, Any] | None
    previous_digest: str
    entry_digest: str


class ProvenanceLog:
    def __init__(self) -> None:
        self._entries: list[ProvenanceEntry] = []

    @property
    def head(self) -> str:
        if not self._entries:
            return GENESIS_DIGEST
        return self._entries[-1].entry_digest

    @property
    def entries(self) -> tuple[ProvenanceEntry, ...]:
        return tuple(self._entries)

    def append(self, record: ProvenanceRecord) -> ProvenanceEntry:
        self._require_recordable(record)
        entry = _sealed_entry(
            ProvenanceEntry(
                len(self._entries),
                record.agent_identifier,
                record.event,
                record.recorded_at,
                dict(record.details),
                _document_of(record),
                self.head,
                "",
            )
        )
        self._entries.append(entry)
        return entry

    def entries_for(self, agent_identifier: str) -> tuple[ProvenanceEntry, ...]:
        return tuple(
            entry
            for entry in self._entries
            if entry.agent_identifier == agent_identifier
        )

    def verify(self) -> None:
        previous = GENESIS_DIGEST
        for position, entry in enumerate(self._entries):
            self._require_linked(entry, position, previous)
            previous = entry.entry_digest

    def recorded_definition(self, agent_identifier: str) -> AgentDefinition:
        document = self._recorded_document(agent_identifier)
        definition = definition_from_document(document)
        if definition_digest(definition) != document["definition_digest"]:
            raise ProvenanceError(
                f"the definition recorded for {agent_identifier} does not match its digest"
            )
        return definition

    def _recorded_document(self, agent_identifier: str) -> Mapping[str, Any]:
        for entry in self.entries_for(agent_identifier):
            if entry.definition_document is not None:
                return entry.definition_document
        raise ProvenanceError(f"no definition was recorded for {agent_identifier}")

    def _require_linked(
        self, entry: ProvenanceEntry, position: int, previous: str
    ) -> None:
        if entry.sequence != position or entry.previous_digest != previous:
            raise ProvenanceError(
                f"entry {position} is not linked to the one before it"
            )
        if _sealed_entry(entry).entry_digest != entry.entry_digest:
            raise ProvenanceError(f"entry {position} was altered after it was recorded")

    def _require_recordable(self, record: ProvenanceRecord) -> None:
        require_agent_identifier(record.agent_identifier)
        if record.recorded_at <= 0:
            raise ProvenanceError("a record needs the time it was made")
        if len(record.details) > MAXIMUM_DETAILS:
            raise ProvenanceError("a record holds a few details")
        for key, value in record.details.items():
            _require_detail(key, value)
        if record.event is ProvenanceEvent.GENERATED and record.definition is None:
            raise ProvenanceError("a generation records the definition it produced")


def _require_detail(key: str, value: str) -> None:
    if not key or len(key) > MAXIMUM_DETAIL or not is_plain_text(key):
        raise ProvenanceError(f"the detail name {key!r} is not short plain text")
    if len(value) > MAXIMUM_DETAIL or not is_plain_text(value):
        raise ProvenanceError(f"the detail {key} is not short plain text")


def _document_of(record: ProvenanceRecord) -> Mapping[str, Any] | None:
    if record.definition is None:
        return None
    document = dict(definition_to_document(record.definition))
    document["definition_digest"] = definition_digest(record.definition)
    return document


def _sealed_entry(entry: ProvenanceEntry) -> ProvenanceEntry:
    body = {
        "sequence": entry.sequence,
        "agent_identifier": entry.agent_identifier,
        "event": entry.event.value,
        "recorded_at": entry.recorded_at,
        "details": dict(entry.details),
        "definition_document": entry.definition_document,
        "previous_digest": entry.previous_digest,
    }
    return ProvenanceEntry(
        entry.sequence,
        entry.agent_identifier,
        entry.event,
        entry.recorded_at,
        entry.details,
        entry.definition_document,
        entry.previous_digest,
        document_digest(body),
    )
