from dataclasses import replace

import pytest

from fintech_agent.application.provenance_log import (
    GENESIS_DIGEST,
    ProvenanceError,
    ProvenanceEvent,
    ProvenanceLog,
    ProvenanceRecord,
)
from fintech_agent.domain.agent_definition_document import definition_digest
from tests.definition_fixtures import build_definition
from tests.policy_fixtures import NOW

AGENT = "agent-one"
OTHER_AGENT = "agent-two"


def log_with_derivation() -> ProvenanceLog:
    log = ProvenanceLog()
    log.append(
        ProvenanceRecord(
            AGENT,
            ProvenanceEvent.DERIVED,
            NOW,
            {"policy_digest": "ab" * 32},
            build_definition(),
        )
    )
    return log


def approval_record(agent: str = AGENT) -> ProvenanceRecord:
    return ProvenanceRecord(
        agent, ProvenanceEvent.APPROVED, NOW + 1, {"fingerprint": "cd" * 16}
    )


def test_an_empty_log_starts_at_the_genesis_digest() -> None:
    log = ProvenanceLog()
    assert log.head == GENESIS_DIGEST
    assert log.entries == ()
    log.verify()


def test_entries_are_numbered_and_chained() -> None:
    log = log_with_derivation()
    first = log.entries[0]
    second = log.append(approval_record())
    assert (first.sequence, second.sequence) == (0, 1)
    assert first.previous_digest == GENESIS_DIGEST
    assert second.previous_digest == first.entry_digest
    assert log.head == second.entry_digest
    log.verify()


def test_the_same_records_in_another_order_give_another_chain() -> None:
    first = log_with_derivation()
    first.append(approval_record())
    second = ProvenanceLog()
    second.append(approval_record())
    second.append(
        ProvenanceRecord(
            AGENT,
            ProvenanceEvent.DERIVED,
            NOW,
            {"policy_digest": "ab" * 32},
            build_definition(),
        )
    )
    assert first.head != second.head


def test_an_altered_entry_is_detected() -> None:
    log = log_with_derivation()
    log.append(approval_record())
    details = log._entries[0].details
    assert isinstance(details, dict)
    details["policy_digest"] = "ff" * 32
    with pytest.raises(ProvenanceError, match="altered"):
        log.verify()


def test_a_broken_link_is_detected() -> None:
    log = log_with_derivation()
    log.append(approval_record())
    log._entries[1] = replace(log._entries[1], previous_digest=GENESIS_DIGEST)
    with pytest.raises(ProvenanceError, match="not linked"):
        log.verify()


def test_a_renumbered_entry_is_detected() -> None:
    log = log_with_derivation()
    log._entries[0] = replace(log._entries[0], sequence=7)
    with pytest.raises(ProvenanceError, match="not linked"):
        log.verify()


def test_entries_can_be_read_per_agent() -> None:
    log = log_with_derivation()
    log.append(approval_record())
    log.append(approval_record(OTHER_AGENT))
    assert [entry.event for entry in log.entries_for(AGENT)] == [
        ProvenanceEvent.DERIVED,
        ProvenanceEvent.APPROVED,
    ]
    assert len(log.entries_for(OTHER_AGENT)) == 1


def test_the_recorded_definition_is_reproduced_and_checked_against_its_digest() -> None:
    log = log_with_derivation()
    assert log.recorded_definition(AGENT) == build_definition()
    document = log.entries[0].definition_document
    assert document is not None
    assert document["definition_digest"] == definition_digest(build_definition())


def test_a_definition_that_does_not_match_its_digest_is_refused() -> None:
    log = log_with_derivation()
    document = log.entries[0].definition_document
    assert isinstance(document, dict)
    document["name"] = "Another Agent"
    with pytest.raises(ProvenanceError, match="does not match its digest"):
        log.recorded_definition(AGENT)


def test_an_agent_without_a_recorded_definition_is_reported() -> None:
    log = ProvenanceLog()
    log.append(approval_record())
    with pytest.raises(ProvenanceError, match="no definition was recorded"):
        log.recorded_definition(AGENT)


def test_a_generation_must_record_its_definition() -> None:
    log = ProvenanceLog()
    with pytest.raises(ProvenanceError, match="records the definition"):
        log.append(ProvenanceRecord(AGENT, ProvenanceEvent.GENERATED, NOW, {}))
    log.append(ProvenanceRecord(AGENT, ProvenanceEvent.DERIVED, NOW, {}))
    assert len(log.entries) == 1


@pytest.mark.parametrize(
    "record",
    [
        ProvenanceRecord("Agent-One", ProvenanceEvent.APPROVED, NOW, {}),
        ProvenanceRecord("treasury", ProvenanceEvent.APPROVED, NOW, {}),
        ProvenanceRecord(AGENT, ProvenanceEvent.APPROVED, 0, {}),
        ProvenanceRecord(AGENT, ProvenanceEvent.APPROVED, NOW, {"": "value"}),
        ProvenanceRecord(AGENT, ProvenanceEvent.APPROVED, NOW, {"key": "v" * 257}),
        ProvenanceRecord(AGENT, ProvenanceEvent.APPROVED, NOW, {"key": "a\u200bb"}),
        ProvenanceRecord(
            AGENT,
            ProvenanceEvent.APPROVED,
            NOW,
            {f"key{number}": "value" for number in range(17)},
        ),
    ],
)
def test_a_malformed_record_is_refused(record: ProvenanceRecord) -> None:
    with pytest.raises((ProvenanceError, ValueError)):
        ProvenanceLog().append(record)


def test_a_refused_record_is_not_appended() -> None:
    log = log_with_derivation()
    with pytest.raises(ProvenanceError):
        log.append(ProvenanceRecord(AGENT, ProvenanceEvent.APPROVED, 0, {}))
    assert len(log.entries) == 1
    log.verify()


def test_an_entry_digest_covers_the_chain_before_it() -> None:
    first = log_with_derivation()
    second = ProvenanceLog()
    second.append(
        ProvenanceRecord(
            AGENT,
            ProvenanceEvent.DERIVED,
            NOW + 5,
            {"policy_digest": "ab" * 32},
            build_definition(),
        )
    )
    assert first.entries[0].entry_digest != second.entries[0].entry_digest
    appended = first.append(approval_record())
    other = second.append(approval_record())
    assert (appended.sequence, appended.details) == (other.sequence, other.details)
    assert appended.entry_digest != other.entry_digest
