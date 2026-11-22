from dataclasses import replace

from fintech_agent.domain.session_call import (
    Execution,
    encode_batch_session_call,
    encode_single_session_call,
)
from fintech_agent.domain.session_policy import (
    NATIVE_ASSET,
    AssetRule,
    SessionPolicy,
)
from fintech_agent.domain.user_operation import (
    GasLimits,
    GasPrices,
    PaymasterFields,
    UserOperation,
)
from fintech_agent.domain.user_operation_hash import HashingDomain

ENTRY_POINT = "0x4337084d9e255ff0702461cf8895ce9e3b5ff108"
ACCOUNT = "0x" + "a1" * 20
VALIDATOR = "0x" + "b2" * 20
HOOK = "0x" + "c3" * 20
TARGET = "0x" + "d4" * 20
TOKEN = "0x" + "e5" * 20
PAYMASTER = "0x" + "f6" * 20
STRANGER = "0x" + "99" * 20
RECIPIENT = "0x" + "77" * 20
NOW = 1_000_000
ETHER = 10**18
TOKEN_CAP = 1_000 * 10**6
TRANSFER = bytes.fromhex("a9059cbb")
APPROVE = bytes.fromhex("095ea7b3")
SESSION_NONCE = int(VALIDATOR, 16) << 96
GAS_LIMITS = GasLimits(200_000, 300_000, 50_000)
GAS_PRICES = GasPrices(3 * 10**9, 10**9)


def build_policy() -> SessionPolicy:
    return SessionPolicy(
        domain=HashingDomain(31337, ENTRY_POINT),
        account=ACCOUNT,
        validator=VALIDATOR,
        hook=HOOK,
        allowed_targets=frozenset({TARGET}),
        rules=(AssetRule(NATIVE_ASSET, ETHER), AssetRule(TOKEN, TOKEN_CAP)),
        allowed_paymasters=frozenset({PAYMASTER}),
        allowed_payees=frozenset(),
        valid_after=NOW - 1_000,
        valid_until=NOW + 1_000,
        window_seconds=86_400,
        maximum_gas_cost=10**17,
        payments_only=False,
    )


def token_call(selector: bytes, recipient: str, amount: int) -> bytes:
    return (
        selector
        + bytes(12)
        + bytes.fromhex(recipient.removeprefix("0x"))
        + amount.to_bytes(32, "big")
    )


def single(target: str, value: int = 0, data: bytes = b"") -> bytes:
    return encode_single_session_call(Execution(target, value, data))


def batch(*executions: Execution) -> bytes:
    return encode_batch_session_call(tuple(executions))


def build_operation(call_data: bytes) -> UserOperation:
    return UserOperation(
        sender=ACCOUNT,
        nonce=SESSION_NONCE,
        init_code=b"",
        call_data=call_data,
        gas_limits=GAS_LIMITS,
        gas_prices=GAS_PRICES,
    )


def with_paymaster(operation: UserOperation, address: str) -> UserOperation:
    return replace(operation, paymaster=PaymasterFields(address, 60_000, 30_000, b""))
