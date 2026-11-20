import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from fintech_agent.application.ports import ChannelMessage, SecureChannelError

KEY_LENGTH = 32
NONCE_LENGTH = 12
PUBLIC_KEY_LENGTH = 32
DOMAIN = b"fintech-agent/secure-channel/v1"


@dataclass(frozen=True)
class KeyPair:
    private_key: bytes
    public_key: bytes


@dataclass(frozen=True)
class DerivationInput:
    shared_secrets: bytes
    ephemeral_public: bytes
    sender_public: bytes
    receiver_public: bytes


def generate_identity() -> KeyPair:
    private = X25519PrivateKey.generate()
    return KeyPair(private.private_bytes_raw(), _public_bytes(private.public_key()))


def _public_bytes(public: X25519PublicKey) -> bytes:
    return public.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _derive_key(derivation: DerivationInput) -> bytes:
    info = (
        DOMAIN
        + derivation.ephemeral_public
        + derivation.sender_public
        + derivation.receiver_public
    )
    return HKDF(hashes.SHA256(), KEY_LENGTH, None, info).derive(
        derivation.shared_secrets
    )


class X25519ChaChaChannel:
    def __init__(self, identity: KeyPair, roster: Mapping[int, bytes]) -> None:
        self._private = X25519PrivateKey.from_private_bytes(identity.private_key)
        self._public = identity.public_key
        self._roster = roster

    def seal(self, message: ChannelMessage) -> bytes:
        receiver_public = self._peer_public(message.receiver)
        ephemeral = X25519PrivateKey.generate()
        ephemeral_public = _public_bytes(ephemeral.public_key())
        try:
            receiver_key = X25519PublicKey.from_public_bytes(receiver_public)
            shared = ephemeral.exchange(receiver_key) + self._private.exchange(
                receiver_key
            )
        except ValueError as failure:
            raise SecureChannelError("the receiver key is unusable") from failure
        key = _derive_key(
            DerivationInput(shared, ephemeral_public, self._public, receiver_public)
        )
        nonce = secrets.token_bytes(NONCE_LENGTH)
        encrypted = ChaCha20Poly1305(key).encrypt(
            nonce, message.payload, message.associated_data
        )
        return ephemeral_public + nonce + encrypted

    def open(self, message: ChannelMessage) -> bytes:
        sender_public = self._peer_public(message.sender)
        if len(message.payload) <= PUBLIC_KEY_LENGTH + NONCE_LENGTH:
            raise SecureChannelError("the sealed message is too short")
        ephemeral_public = message.payload[:PUBLIC_KEY_LENGTH]
        nonce = message.payload[PUBLIC_KEY_LENGTH : PUBLIC_KEY_LENGTH + NONCE_LENGTH]
        encrypted = message.payload[PUBLIC_KEY_LENGTH + NONCE_LENGTH :]
        key = self._receiving_key(ephemeral_public, sender_public)
        try:
            return ChaCha20Poly1305(key).decrypt(
                nonce, encrypted, message.associated_data
            )
        except InvalidTag as failure:
            raise SecureChannelError("the sealed message failed authentication") from (
                failure
            )

    def _receiving_key(self, ephemeral_public: bytes, sender_public: bytes) -> bytes:
        try:
            shared = self._private.exchange(
                X25519PublicKey.from_public_bytes(ephemeral_public)
            ) + self._private.exchange(X25519PublicKey.from_public_bytes(sender_public))
        except ValueError as failure:
            raise SecureChannelError("a public key is unusable") from failure
        return _derive_key(
            DerivationInput(shared, ephemeral_public, sender_public, self._public)
        )

    def _peer_public(self, identifier: int) -> bytes:
        if identifier not in self._roster:
            raise SecureChannelError("the participant is not in the roster")
        return self._roster[identifier]
