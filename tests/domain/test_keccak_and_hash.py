import pytest

from fintech_agent.domain.keccak import keccak256
from fintech_agent.domain.user_operation import (
    GasLimits,
    GasPrices,
    PaymasterFields,
    UserOperation,
)
from fintech_agent.domain.user_operation_hash import HashingDomain, user_operation_hash

ENTRY_POINT = "0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108"
GWEI = 10**9


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
        (b"abc", "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"),
        (
            b"a" * 200,
            "96ea54061def936c4be90b518992fdc6f12f535068a256229aca54267b4d084d",
        ),
        (
            b"x" * 136,
            "50da8ef3747b7a7f01d08563aa11c72a2a668563fb928adc6e8d2a1ab4e36096",
        ),
        (
            b"x" * 135,
            "16570bdb055e663ea1cb57ac6f09194f4bc7b7070847971fc0b86710366dc34f",
        ),
    ],
)
def test_keccak256_matches_known_digests(data: bytes, expected: str) -> None:
    assert keccak256(data).hex() == expected


def operation(init_code: bytes, paymaster: PaymasterFields | None) -> UserOperation:
    return UserOperation(
        sender="0x" + "11" * 20,
        nonce=7,
        init_code=init_code,
        call_data=bytes.fromhex("deadbeef"),
        gas_limits=GasLimits(200_000, 100_000, 50_000),
        gas_prices=GasPrices(3 * GWEI, GWEI),
        signature=bytes.fromhex("aabb"),
        paymaster=paymaster,
    )


DOMAIN = HashingDomain(31337, ENTRY_POINT)


def test_the_hash_matches_the_entry_point_for_a_plain_operation() -> None:
    expected = "ebd4c59d10b3b1adee4c0ee1abcddae2b8f9d5e843fd339261f13865459f22a5"

    assert user_operation_hash(operation(b"", None), DOMAIN).hex() == expected


def test_the_hash_matches_the_entry_point_with_a_paymaster() -> None:
    paymaster = PaymasterFields(
        "0x" + "22" * 20, 60_000, 30_000, bytes.fromhex("1234567890")
    )
    expected = "1608e6c383feee52866e6c2aa677fb55a17ef1bb9a91c44758f85082c6bd813e"

    assert user_operation_hash(operation(b"", paymaster), DOMAIN).hex() == expected


def test_the_hash_matches_the_entry_point_with_init_code() -> None:
    init_code = bytes.fromhex("33" * 20 + "abcdef")
    expected = "f974a8d20e15f30211616d9dd1cd9b5843716d98b4b1a32601fef22f96b26208"

    assert user_operation_hash(operation(init_code, None), DOMAIN).hex() == expected


def test_the_hash_ignores_the_signature_and_depends_on_the_chain() -> None:
    plain = operation(b"", None)
    resigned = plain.with_signature(b"\x01" * 65)

    assert user_operation_hash(plain, DOMAIN) == user_operation_hash(resigned, DOMAIN)
    assert user_operation_hash(plain, DOMAIN) != user_operation_hash(
        plain, HashingDomain(1, ENTRY_POINT)
    )
