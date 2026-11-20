import pytest

from fintech_agent.adapters.in_memory import (
    InMemorySealedStorage,
)
from fintech_agent.application.enclave_node import (
    MAXIMUM_PENDING_SESSIONS,
    EnclaveNode,
)
from fintech_agent.application.key_share_record import (
    InvalidKeyShareRecordError,
    KeyShareRecord,
)
from fintech_agent.application.ports import (
    EnclaveRefusedError,
    SealedDataNotFoundError,
)
from fintech_agent.domain.frost.aggregation import (
    ParticipantCommitment,
    SigningPackage,
)
from fintech_agent.domain.frost.group import GENERATOR, GROUP_ORDER
from fintech_agent.domain.frost.signing import NonceCommitments
from tests.application.fixtures import (
    USER_OPERATION_HASH,
    build_node,
    build_node_from_storage,
    group_public_key,
    permissive_dependencies,
    record_of,
)

SESSION = b"session-0000-0001"


def commitment_of(
    identifier: int, commitments: NonceCommitments
) -> ParticipantCommitment:
    return ParticipantCommitment(
        identifier,
        commitments.hiding_nonce_commitment,
        commitments.binding_nonce_commitment,
    )


def two_signer_package(
    first: NonceCommitments, message: bytes = USER_OPERATION_HASH
) -> SigningPackage:
    second = build_node(3).commit(SESSION)
    return SigningPackage(
        (commitment_of(1, first), commitment_of(3, second)),
        message,
        group_public_key(),
    )


def test_a_signature_share_can_be_produced_once_per_commitment() -> None:
    node = build_node(1)
    package = two_signer_package(node.commit(SESSION))

    share = node.sign(SESSION, package)

    assert 0 < share < GROUP_ORDER
    with pytest.raises(EnclaveRefusedError):
        node.sign(SESSION, package)


def test_signing_without_a_commitment_is_refused() -> None:
    node = build_node(1)
    package = two_signer_package(build_node(1).commit(SESSION))

    with pytest.raises(EnclaveRefusedError):
        node.sign(SESSION, package)


def test_a_rejected_package_still_burns_the_nonces() -> None:
    node = build_node(1)
    package = two_signer_package(node.commit(SESSION))
    foreign = SigningPackage(package.commitments, package.message, GENERATOR)

    with pytest.raises(EnclaveRefusedError):
        node.sign(SESSION, foreign)
    with pytest.raises(EnclaveRefusedError):
        node.sign(SESSION, package)


def test_a_package_below_the_threshold_is_refused() -> None:
    node = build_node(1)
    commitments = node.commit(SESSION)
    alone = SigningPackage(
        (commitment_of(1, commitments),), USER_OPERATION_HASH, group_public_key()
    )

    with pytest.raises(EnclaveRefusedError):
        node.sign(SESSION, alone)


def test_a_message_that_is_not_a_hash_is_refused() -> None:
    node = build_node(1)
    package = two_signer_package(node.commit(SESSION), message=b"transfer all")

    with pytest.raises(EnclaveRefusedError):
        node.sign(SESSION, package)


def test_a_package_that_changes_this_enclaves_commitments_is_refused() -> None:
    node = build_node(1)
    node.commit(SESSION)
    package = two_signer_package(build_node(1).commit(SESSION))

    with pytest.raises(EnclaveRefusedError):
        node.sign(SESSION, package)


def test_a_session_cannot_commit_twice() -> None:
    node = build_node(1)
    node.commit(SESSION)

    with pytest.raises(EnclaveRefusedError):
        node.commit(SESSION)


def test_releasing_a_session_discards_its_nonces() -> None:
    node = build_node(1)
    package = two_signer_package(node.commit(SESSION))

    node.release(SESSION)

    assert node.pending_session_count == 0
    with pytest.raises(EnclaveRefusedError):
        node.sign(SESSION, package)


def test_the_oldest_pending_session_is_evicted_at_capacity() -> None:
    node = build_node(1)
    oldest = (0).to_bytes(4, "big")
    for index in range(MAXIMUM_PENDING_SESSIONS + 1):
        node.commit(index.to_bytes(4, "big"))

    assert node.pending_session_count == MAXIMUM_PENDING_SESSIONS
    node.commit(oldest)


def test_every_commitment_uses_fresh_nonces() -> None:
    node = build_node(1)

    first = node.commit(b"first")
    second = node.commit(b"second")

    assert first != second


def test_the_node_loads_its_share_from_sealed_storage() -> None:
    node = build_node_from_storage(2)

    assert node.identifier == 2


def test_a_missing_sealed_record_is_reported() -> None:
    with pytest.raises(SealedDataNotFoundError):
        EnclaveNode.from_sealed_storage(
            InMemorySealedStorage(),
            permissive_dependencies(),
        )


def test_a_key_share_record_survives_a_round_trip() -> None:
    record = record_of(3)

    assert KeyShareRecord.deserialize(record.serialize()) == record


def test_malformed_key_share_records_are_rejected() -> None:
    encoded = record_of(1).serialize()

    with pytest.raises(InvalidKeyShareRecordError):
        KeyShareRecord.deserialize(encoded[:-1])
    with pytest.raises(InvalidKeyShareRecordError):
        KeyShareRecord.deserialize(bytes(2) + encoded[2:])
    with pytest.raises(InvalidKeyShareRecordError):
        KeyShareRecord.deserialize(encoded[:4] + bytes(32) + encoded[36:])
    with pytest.raises(InvalidKeyShareRecordError):
        KeyShareRecord.deserialize(encoded[:36] + bytes(33))
