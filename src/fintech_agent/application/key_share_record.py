from __future__ import annotations

from dataclasses import dataclass

from fintech_agent.domain.frost.group import (
    COMPRESSED_POINT_LENGTH,
    GROUP_ORDER,
    SCALAR_LENGTH,
    Point,
    deserialize_scalar,
    serialize_scalar,
)

IDENTIFIER_LENGTH = 2
THRESHOLD_LENGTH = 2
RECORD_LENGTH = (
    IDENTIFIER_LENGTH + THRESHOLD_LENGTH + SCALAR_LENGTH + COMPRESSED_POINT_LENGTH
)
MAXIMUM_IDENTIFIER = 2**16 - 1


class InvalidKeyShareRecordError(ValueError):
    pass


@dataclass(frozen=True)
class KeyShareRecord:
    identifier: int
    threshold: int
    secret_share: int
    group_public_key: Point

    def __post_init__(self) -> None:
        if not 1 <= self.identifier <= MAXIMUM_IDENTIFIER:
            raise InvalidKeyShareRecordError("the identifier is out of range")
        if not 2 <= self.threshold <= MAXIMUM_IDENTIFIER:
            raise InvalidKeyShareRecordError("the threshold is out of range")
        if not 0 < self.secret_share < GROUP_ORDER:
            raise InvalidKeyShareRecordError("the secret share is out of range")
        if self.group_public_key.is_identity:
            raise InvalidKeyShareRecordError("the group public key is the identity")

    def serialize(self) -> bytes:
        return (
            self.identifier.to_bytes(IDENTIFIER_LENGTH, "big")
            + self.threshold.to_bytes(THRESHOLD_LENGTH, "big")
            + serialize_scalar(self.secret_share)
            + self.group_public_key.serialize()
        )

    @classmethod
    def deserialize(cls, encoded: bytes) -> KeyShareRecord:
        if len(encoded) != RECORD_LENGTH:
            raise InvalidKeyShareRecordError("a key share record must be 69 bytes")
        threshold_start = IDENTIFIER_LENGTH
        share_start = threshold_start + THRESHOLD_LENGTH
        key_start = share_start + SCALAR_LENGTH
        try:
            return cls(
                identifier=int.from_bytes(encoded[:threshold_start], "big"),
                threshold=int.from_bytes(encoded[threshold_start:share_start], "big"),
                secret_share=deserialize_scalar(encoded[share_start:key_start]),
                group_public_key=Point.deserialize(encoded[key_start:]),
            )
        except ValueError as failure:
            raise InvalidKeyShareRecordError(str(failure)) from failure
