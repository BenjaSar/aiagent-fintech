from fintech_agent.domain.user_operation import (
    GasLimits,
    GasPrices,
    PaymasterFields,
    UserOperation,
)

SENDER = "0x" + "11" * 20
FACTORY = bytes.fromhex("22" * 20)
PAYMASTER = "0x" + "33" * 20
PAYMASTER_DATA = bytes.fromhex("aa")


def build(init_code: bytes = b"") -> UserOperation:
    return UserOperation(
        sender=SENDER,
        nonce=7,
        init_code=init_code,
        call_data=bytes.fromhex("abcd"),
        gas_limits=GasLimits(
            call_gas_limit=3, verification_gas_limit=2, pre_verification_gas=5
        ),
        gas_prices=GasPrices(max_fee_per_gas=9, max_priority_fee_per_gas=4),
    )


def test_gas_limits_pack_verification_first_then_call() -> None:
    packed = build().account_gas_limits

    assert packed == (2).to_bytes(16, "big") + (3).to_bytes(16, "big")


def test_gas_prices_pack_priority_fee_first_then_max_fee() -> None:
    packed = build().packed_gas_prices

    assert packed == (4).to_bytes(16, "big") + (9).to_bytes(16, "big")


def test_the_packed_tuple_lists_the_nine_entry_point_fields() -> None:
    operation = build(FACTORY + b"\x01").with_signature(bytes.fromhex("ff"))

    fields = operation.as_packed_tuple().strip("()").split(",")

    assert len(fields) == 9
    assert fields[0] == SENDER
    assert fields[1] == "7"
    assert fields[2] == "0x" + (FACTORY + b"\x01").hex()
    assert fields[7] == "0x"
    assert fields[8] == "0xff"


def test_the_rpc_form_splits_the_factory_from_its_data() -> None:
    document = build(FACTORY + b"\x01\x02").as_rpc()

    assert document["factory"] == "0x" + FACTORY.hex()
    assert document["factoryData"] == "0x0102"
    assert document["nonce"] == "0x7"


def test_an_operation_without_init_code_has_no_factory_fields() -> None:
    document = build().as_rpc()

    assert "factory" not in document
    assert "factoryData" not in document


def test_signing_and_gas_changes_return_new_operations() -> None:
    original = build()

    signed = original.with_signature(b"\x01")
    repriced = original.with_gas_limits(GasLimits(1, 1, 1))

    assert original.signature == b""
    assert signed.signature == b"\x01"
    assert repriced.gas_limits.pre_verification_gas == 1


def test_paymaster_fields_pack_address_then_both_gas_limits_then_data() -> None:
    fields = PaymasterFields(PAYMASTER, 5, 6, PAYMASTER_DATA)

    assert fields.packed() == (
        bytes.fromhex("33" * 20)
        + (5).to_bytes(16, "big")
        + (6).to_bytes(16, "big")
        + PAYMASTER_DATA
    )


def test_a_sponsored_operation_carries_the_paymaster_in_both_forms() -> None:
    operation = build().with_paymaster(PaymasterFields(PAYMASTER, 5, 6, PAYMASTER_DATA))

    document = operation.as_rpc()
    packed = operation.as_packed_tuple().strip("()").split(",")

    assert document["paymaster"] == PAYMASTER
    assert document["paymasterVerificationGasLimit"] == "0x5"
    assert document["paymasterPostOpGasLimit"] == "0x6"
    assert document["paymasterData"] == "0xaa"
    assert packed[7] == "0x" + operation.paymaster_and_data.hex()


def test_an_unsponsored_operation_has_no_paymaster_fields() -> None:
    operation = build()

    assert "paymaster" not in operation.as_rpc()
    assert operation.paymaster_and_data == b""
    assert operation.as_packed_tuple().strip("()").split(",")[7] == "0x"
