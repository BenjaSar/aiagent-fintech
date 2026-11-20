from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from fintech_agent.application.ports import CardSignatureCheck

PUBLIC_KEY_BYTES = 32


@dataclass(frozen=True)
class CardSigningKey:
    private_key: bytes
    public_key: bytes


def generate_card_signing_key() -> CardSigningKey:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return CardSigningKey(private.private_bytes_raw(), public)


def sign_card_body(key: CardSigningKey, body: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(key.private_key).sign(body)


class Ed25519CardSignatureVerifier:
    def verify(self, request: CardSignatureCheck) -> bool:
        if len(request.public_key) != PUBLIC_KEY_BYTES:
            return False
        try:
            Ed25519PublicKey.from_public_bytes(request.public_key).verify(
                request.signature, request.message
            )
        except InvalidSignature:
            return False
        return True
