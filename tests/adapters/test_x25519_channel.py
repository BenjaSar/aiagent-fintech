import pytest

from fintech_agent.adapters.x25519_channel import (
    KeyPair,
    X25519ChaChaChannel,
    generate_identity,
)
from fintech_agent.application.ports import ChannelMessage, SecureChannelError

SECRET = b"\x42" * 32
CONTEXT = b"ceremony|1|2|digest"


def build_roster() -> tuple[dict[int, KeyPair], dict[int, bytes]]:
    identities = {identifier: generate_identity() for identifier in (1, 2, 3)}
    roster = {identifier: pair.public_key for identifier, pair in identities.items()}
    return identities, roster


def build_channels() -> tuple[X25519ChaChaChannel, X25519ChaChaChannel, KeyPair]:
    identities, roster = build_roster()
    return (
        X25519ChaChaChannel(identities[1], roster),
        X25519ChaChaChannel(identities[2], roster),
        identities[3],
    )


def message(payload: bytes, associated_data: bytes = CONTEXT) -> ChannelMessage:
    return ChannelMessage(1, 2, associated_data, payload)


def test_a_sealed_message_opens_for_its_receiver_and_hides_the_payload() -> None:
    sender, receiver, _ = build_channels()

    sealed = sender.seal(message(SECRET))

    assert SECRET not in sealed
    assert receiver.open(message(sealed)) == SECRET


def test_every_seal_of_the_same_payload_differs() -> None:
    sender, _, _ = build_channels()

    assert sender.seal(message(SECRET)) != sender.seal(message(SECRET))


def test_different_associated_data_fails_authentication() -> None:
    sender, receiver, _ = build_channels()
    sealed = sender.seal(message(SECRET))

    with pytest.raises(SecureChannelError):
        receiver.open(message(sealed, associated_data=b"another digest"))


def test_a_tampered_ciphertext_fails_authentication() -> None:
    sender, receiver, _ = build_channels()
    sealed = bytearray(sender.seal(message(SECRET)))
    sealed[-1] ^= 0x01

    with pytest.raises(SecureChannelError):
        receiver.open(message(bytes(sealed)))


def test_a_message_claiming_another_sender_fails_authentication() -> None:
    sender, receiver, _ = build_channels()
    sealed = sender.seal(message(SECRET))

    with pytest.raises(SecureChannelError):
        receiver.open(ChannelMessage(3, 2, CONTEXT, sealed))


def test_a_third_party_cannot_open_a_message() -> None:
    identities, roster = build_roster()
    sender = X25519ChaChaChannel(identities[1], roster)
    outsider = X25519ChaChaChannel(identities[3], roster)
    sealed = sender.seal(message(SECRET))

    with pytest.raises(SecureChannelError):
        outsider.open(ChannelMessage(1, 2, CONTEXT, sealed))


def test_unknown_participants_and_short_messages_are_rejected() -> None:
    sender, receiver, _ = build_channels()

    with pytest.raises(SecureChannelError):
        sender.seal(ChannelMessage(1, 9, CONTEXT, SECRET))
    with pytest.raises(SecureChannelError):
        receiver.open(message(b"short"))
    with pytest.raises(SecureChannelError):
        receiver.open(message(bytes(100)))
