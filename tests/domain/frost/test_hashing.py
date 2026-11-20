import pytest

from fintech_agent.domain.frost.group import GROUP_ORDER
from fintech_agent.domain.frost.hashing import (
    ExpansionRequest,
    expand_message_xmd,
    hash_to_scalar,
)

from .vectors import EXPAND_MESSAGE_XMD_SHA256_VECTORS

DOMAIN_SEPARATION_TAG = b"QUUX-V01-CS02-with-expander-SHA256-128"


@pytest.mark.parametrize(
    ("message", "length", "expected_hex"), EXPAND_MESSAGE_XMD_SHA256_VECTORS
)
def test_expand_message_matches_the_published_vectors(
    message: bytes, length: int, expected_hex: str
) -> None:
    request = ExpansionRequest(message, DOMAIN_SEPARATION_TAG, length)

    assert expand_message_xmd(request) == bytes.fromhex(expected_hex)


def test_expand_message_rejects_a_domain_separation_tag_over_255_bytes() -> None:
    request = ExpansionRequest(b"", b"x" * 256, 32)

    with pytest.raises(ValueError):
        expand_message_xmd(request)


def test_expand_message_rejects_a_length_requiring_more_than_255_blocks() -> None:
    request = ExpansionRequest(b"", DOMAIN_SEPARATION_TAG, 32 * 255 + 1)

    with pytest.raises(ValueError):
        expand_message_xmd(request)


def test_hash_to_scalar_is_reduced_modulo_the_group_order() -> None:
    assert 0 <= hash_to_scalar(b"message", b"rho") < GROUP_ORDER


def test_hash_to_scalar_separates_domains() -> None:
    assert hash_to_scalar(b"message", b"rho") != hash_to_scalar(b"message", b"chal")
