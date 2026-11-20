from dataclasses import dataclass

from .session_call import (
    Execution,
    UnsupportedSessionCallError,
    decode_session_call,
)
from .user_operation import UserOperation
from .user_operation_hash import HashingDomain

NATIVE_ASSET = "0x" + "00" * 20
TOKEN_TRANSFER_SELECTOR = bytes.fromhex("a9059cbb")
TOKEN_APPROVE_SELECTOR = bytes.fromhex("095ea7b3")
TOKEN_CALL_BYTES = 68
SELECTOR_BYTES = 4
WORD_BYTES = 32
ADDRESS_BYTES = 20
NONCE_SEQUENCE_BITS = 64
VALIDATOR_KEY_SHIFT = 32
MAXIMUM_ASSETS = 8
HEX_DIGITS = frozenset("0123456789abcdef")


class PolicyViolationError(ValueError):
    pass


@dataclass(frozen=True)
class AssetRule:
    asset: str
    cap: int


@dataclass(frozen=True)
class SessionPolicy:
    domain: HashingDomain
    account: str
    validator: str
    hook: str
    allowed_targets: frozenset[str]
    rules: tuple[AssetRule, ...]
    allowed_paymasters: frozenset[str]
    valid_after: int
    valid_until: int
    window_seconds: int
    maximum_gas_cost: int

    def __post_init__(self) -> None:
        addresses = {
            self.account,
            self.validator,
            self.hook,
            self.domain.entry_point,
            *self.allowed_targets,
            *self.allowed_paymasters,
            *(rule.asset for rule in self.rules),
        }
        if not all(_is_normalized_address(address) for address in addresses):
            raise PolicyViolationError(
                "every address must be lowercase 0x-prefixed hex"
            )
        assets = [rule.asset for rule in self.rules]
        if len(set(assets)) != len(assets) or len(assets) > MAXIMUM_ASSETS:
            raise PolicyViolationError("the asset rules must be unique and few")
        if any(rule.cap <= 0 for rule in self.rules):
            raise PolicyViolationError("every cap must be positive")
        if self.window_seconds <= 0 or self.valid_until <= self.valid_after:
            raise PolicyViolationError("the time window is empty")

    def cap_of(self, asset: str) -> int | None:
        for rule in self.rules:
            if rule.asset == asset:
                return rule.cap
        return None

    @property
    def forbidden_targets(self) -> frozenset[str]:
        return frozenset(
            {self.account, NATIVE_ASSET, self.hook, self.domain.entry_point}
        )


@dataclass(frozen=True)
class Movement:
    asset: str
    amount: int


def _is_normalized_address(address: str) -> bool:
    body = address.removeprefix("0x")
    return (
        address.startswith("0x")
        and len(body) == 2 * ADDRESS_BYTES
        and body == body.lower()
        and all(character in HEX_DIGITS for character in body)
    )


def check_operation(
    policy: SessionPolicy, operation: UserOperation, now: int
) -> tuple[Movement, ...]:
    _check_envelope(policy, operation, now)
    try:
        executions = decode_session_call(operation.call_data)
    except UnsupportedSessionCallError as failure:
        raise PolicyViolationError(str(failure)) from failure
    return _merge(
        [
            movement
            for execution in executions
            for movement in _movements_of(policy, execution)
        ]
    )


def _check_envelope(policy: SessionPolicy, operation: UserOperation, now: int) -> None:
    if operation.sender.lower() != policy.account:
        raise PolicyViolationError("the operation is not for the policy's account")
    if operation.init_code:
        raise PolicyViolationError("a session operation cannot create an account")
    session_key = int(policy.validator, 16) << VALIDATOR_KEY_SHIFT
    if operation.nonce >> NONCE_SEQUENCE_BITS != session_key:
        raise PolicyViolationError("the nonce key does not name the session validator")
    if not policy.valid_after <= now < policy.valid_until:
        raise PolicyViolationError("the session is outside its time window")
    _check_paymaster(policy, operation)
    _check_gas(policy, operation)


def _check_paymaster(policy: SessionPolicy, operation: UserOperation) -> None:
    if operation.paymaster is None:
        return
    if operation.paymaster.address.lower() not in policy.allowed_paymasters:
        raise PolicyViolationError("the paymaster is not allowed")


def _check_gas(policy: SessionPolicy, operation: UserOperation) -> None:
    limits = operation.gas_limits
    gas = limits.call_gas_limit + limits.verification_gas_limit
    gas += limits.pre_verification_gas
    if operation.paymaster is not None:
        gas += operation.paymaster.verification_gas_limit
        gas += operation.paymaster.post_operation_gas_limit
    if gas * operation.gas_prices.max_fee_per_gas > policy.maximum_gas_cost:
        raise PolicyViolationError("the gas the operation can spend is above the limit")


def _movements_of(policy: SessionPolicy, execution: Execution) -> list[Movement]:
    target = execution.target
    if target in policy.forbidden_targets:
        raise PolicyViolationError(f"the target {target} is forbidden")
    if policy.cap_of(target) is not None:
        return [_token_movement(execution)]
    if target not in policy.allowed_targets:
        raise PolicyViolationError(f"the target {target} is not allowed")
    if execution.value == 0:
        return []
    if policy.cap_of(NATIVE_ASSET) is None:
        raise PolicyViolationError("native value is not allowed")
    return [Movement(NATIVE_ASSET, execution.value)]


def _token_movement(execution: Execution) -> Movement:
    data = execution.data
    selector = data[:SELECTOR_BYTES]
    if execution.value != 0:
        raise PolicyViolationError("a token call cannot carry native value")
    if len(data) != TOKEN_CALL_BYTES or selector not in (
        TOKEN_TRANSFER_SELECTOR,
        TOKEN_APPROVE_SELECTOR,
    ):
        raise PolicyViolationError("only transfer and approve calls are allowed")
    if any(data[SELECTOR_BYTES : SELECTOR_BYTES + WORD_BYTES - ADDRESS_BYTES]):
        raise PolicyViolationError("the token call names a malformed address")
    return Movement(
        execution.target, int.from_bytes(data[SELECTOR_BYTES + WORD_BYTES :], "big")
    )


def _merge(movements: list[Movement]) -> tuple[Movement, ...]:
    totals: dict[str, int] = {}
    for movement in movements:
        totals[movement.asset] = totals.get(movement.asset, 0) + movement.amount
    return tuple(Movement(asset, amount) for asset, amount in totals.items() if amount)
