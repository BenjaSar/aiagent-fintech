from dataclasses import dataclass

from .keccak import keccak256
from .user_operation import UserOperation

WORD_BYTES = 32
DOMAIN_TYPE = b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
OPERATION_TYPE = (
    b"PackedUserOperation(address sender,uint256 nonce,bytes initCode,bytes callData,"
    b"bytes32 accountGasLimits,uint256 preVerificationGas,bytes32 gasFees,"
    b"bytes paymasterAndData)"
)
DOMAIN_NAME = b"ERC4337"
DOMAIN_VERSION = b"1"
TYPED_DATA_PREFIX = b"\x19\x01"


@dataclass(frozen=True)
class HashingDomain:
    chain_id: int
    entry_point: str


def user_operation_hash(operation: UserOperation, domain: HashingDomain) -> bytes:
    return keccak256(
        TYPED_DATA_PREFIX + _domain_separator(domain) + _structure_hash(operation)
    )


def _domain_separator(domain: HashingDomain) -> bytes:
    return keccak256(
        keccak256(DOMAIN_TYPE)
        + keccak256(DOMAIN_NAME)
        + keccak256(DOMAIN_VERSION)
        + _word(domain.chain_id)
        + _address_word(domain.entry_point)
    )


def _structure_hash(operation: UserOperation) -> bytes:
    return keccak256(
        keccak256(OPERATION_TYPE)
        + _address_word(operation.sender)
        + _word(operation.nonce)
        + keccak256(operation.init_code)
        + keccak256(operation.call_data)
        + operation.account_gas_limits
        + _word(operation.gas_limits.pre_verification_gas)
        + operation.packed_gas_prices
        + keccak256(operation.paymaster_and_data)
    )


def _word(value: int) -> bytes:
    return value.to_bytes(WORD_BYTES, "big")


def _address_word(address: str) -> bytes:
    return bytes(WORD_BYTES - 20) + bytes.fromhex(address.removeprefix("0x"))
