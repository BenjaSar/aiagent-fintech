import hashlib
from dataclasses import dataclass

from .group import GROUP_ORDER

CONTEXT_STRING = b"FROST-secp256k1-SHA256-v1"
HASH_OUTPUT_LENGTH = 32
HASH_INPUT_BLOCK_LENGTH = 64
SCALAR_EXPANSION_LENGTH = 48
MAX_TAG_LENGTH = 255
MAX_BLOCK_COUNT = 255
MAX_EXPANDED_LENGTH = 65535


@dataclass(frozen=True)
class ExpansionRequest:
    message: bytes
    domain_separation_tag: bytes
    length: int


def expand_message_xmd(request: ExpansionRequest) -> bytes:
    block_count = -(-request.length // HASH_OUTPUT_LENGTH)
    _validate_expansion(request, block_count)
    tag_suffix = request.domain_separation_tag + bytes(
        [len(request.domain_separation_tag)]
    )
    first_input = (
        bytes(HASH_INPUT_BLOCK_LENGTH)
        + request.message
        + request.length.to_bytes(2, "big")
        + b"\x00"
        + tag_suffix
    )
    first_block = hashlib.sha256(first_input).digest()
    blocks = [hashlib.sha256(first_block + b"\x01" + tag_suffix).digest()]
    for index in range(2, block_count + 1):
        mixed = _exclusive_or(first_block, blocks[-1])
        blocks.append(hashlib.sha256(mixed + bytes([index]) + tag_suffix).digest())
    return b"".join(blocks)[: request.length]


def hash_to_scalar(message: bytes, domain_suffix: bytes) -> int:
    request = ExpansionRequest(
        message, CONTEXT_STRING + domain_suffix, SCALAR_EXPANSION_LENGTH
    )
    return int.from_bytes(expand_message_xmd(request), "big") % GROUP_ORDER


def hash_to_binding_factor(message: bytes) -> int:
    return hash_to_scalar(message, b"rho")


def hash_to_nonce(message: bytes) -> int:
    return hash_to_scalar(message, b"nonce")


def hash_to_dkg_challenge(message: bytes) -> int:
    return hash_to_scalar(message, b"dkg")


def hash_to_challenge(message: bytes) -> int:
    return hash_to_scalar(message, b"chal")


def hash_message(message: bytes) -> bytes:
    return hashlib.sha256(CONTEXT_STRING + b"msg" + message).digest()


def hash_commitments(encoded_commitments: bytes) -> bytes:
    return hashlib.sha256(CONTEXT_STRING + b"com" + encoded_commitments).digest()


def _validate_expansion(request: ExpansionRequest, block_count: int) -> None:
    if request.length <= 0 or request.length > MAX_EXPANDED_LENGTH:
        raise ValueError("the requested length is out of range")
    if block_count > MAX_BLOCK_COUNT:
        raise ValueError("the requested length needs more than 255 blocks")
    if len(request.domain_separation_tag) > MAX_TAG_LENGTH:
        raise ValueError("the domain separation tag exceeds 255 bytes")


def _exclusive_or(left: bytes, right: bytes) -> bytes:
    return bytes(
        left_byte ^ right_byte
        for left_byte, right_byte in zip(left, right, strict=True)
    )
