from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

HALF_WORD_BYTES = 16
FACTORY_LENGTH = 20


@dataclass(frozen=True)
class GasLimits:
    call_gas_limit: int
    verification_gas_limit: int
    pre_verification_gas: int


@dataclass(frozen=True)
class GasPrices:
    max_fee_per_gas: int
    max_priority_fee_per_gas: int


@dataclass(frozen=True)
class PaymasterFields:
    address: str
    verification_gas_limit: int
    post_operation_gas_limit: int
    data: bytes

    def packed(self) -> bytes:
        return (
            bytes.fromhex(self.address.removeprefix("0x"))
            + self.verification_gas_limit.to_bytes(HALF_WORD_BYTES, "big")
            + self.post_operation_gas_limit.to_bytes(HALF_WORD_BYTES, "big")
            + self.data
        )


@dataclass(frozen=True)
class UserOperation:
    sender: str
    nonce: int
    init_code: bytes
    call_data: bytes
    gas_limits: GasLimits
    gas_prices: GasPrices
    signature: bytes = b""
    paymaster: PaymasterFields | None = None

    def with_signature(self, signature: bytes) -> UserOperation:
        return replace(self, signature=signature)

    def with_gas_limits(self, gas_limits: GasLimits) -> UserOperation:
        return replace(self, gas_limits=gas_limits)

    def with_paymaster(self, paymaster: PaymasterFields | None) -> UserOperation:
        return replace(self, paymaster=paymaster)

    @property
    def paymaster_and_data(self) -> bytes:
        return b"" if self.paymaster is None else self.paymaster.packed()

    @property
    def account_gas_limits(self) -> bytes:
        return self.gas_limits.verification_gas_limit.to_bytes(
            HALF_WORD_BYTES, "big"
        ) + self.gas_limits.call_gas_limit.to_bytes(HALF_WORD_BYTES, "big")

    @property
    def packed_gas_prices(self) -> bytes:
        return self.gas_prices.max_priority_fee_per_gas.to_bytes(
            HALF_WORD_BYTES, "big"
        ) + self.gas_prices.max_fee_per_gas.to_bytes(HALF_WORD_BYTES, "big")

    def as_packed_tuple(self) -> str:
        fields = (
            self.sender,
            str(self.nonce),
            "0x" + self.init_code.hex(),
            "0x" + self.call_data.hex(),
            "0x" + self.account_gas_limits.hex(),
            str(self.gas_limits.pre_verification_gas),
            "0x" + self.packed_gas_prices.hex(),
            "0x" + self.paymaster_and_data.hex(),
            "0x" + self.signature.hex(),
        )
        return "(" + ",".join(fields) + ")"

    def as_rpc(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "sender": self.sender,
            "nonce": hex(self.nonce),
            "callData": "0x" + self.call_data.hex(),
            "callGasLimit": hex(self.gas_limits.call_gas_limit),
            "verificationGasLimit": hex(self.gas_limits.verification_gas_limit),
            "preVerificationGas": hex(self.gas_limits.pre_verification_gas),
            "maxFeePerGas": hex(self.gas_prices.max_fee_per_gas),
            "maxPriorityFeePerGas": hex(self.gas_prices.max_priority_fee_per_gas),
            "signature": "0x" + self.signature.hex(),
        }
        if self.init_code:
            document["factory"] = "0x" + self.init_code[:FACTORY_LENGTH].hex()
            document["factoryData"] = "0x" + self.init_code[FACTORY_LENGTH:].hex()
        if self.paymaster is not None:
            document["paymaster"] = self.paymaster.address
            document["paymasterVerificationGasLimit"] = hex(
                self.paymaster.verification_gas_limit
            )
            document["paymasterPostOpGasLimit"] = hex(
                self.paymaster.post_operation_gas_limit
            )
            document["paymasterData"] = "0x" + self.paymaster.data.hex()
        return document
