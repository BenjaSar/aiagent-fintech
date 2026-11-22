from collections.abc import Mapping
from dataclasses import dataclass

from fintech_agent.application.agent_tools import PaymentOutcome
from fintech_agent.application.policy_authorizer import (
    AuthorizerParts,
    PolicyAuthorizer,
)
from fintech_agent.application.ports import (
    AuthorizationRequest,
    EnclaveRefusedError,
    SealedStorage,
)
from fintech_agent.domain.payment_intent import PaymentIntent
from fintech_agent.domain.session_call import Execution, encode_single_session_call
from fintech_agent.domain.session_policy import SessionPolicy
from fintech_agent.domain.user_operation import GasLimits, GasPrices, UserOperation
from fintech_agent.domain.user_operation_hash import user_operation_hash

GAS_LIMITS = GasLimits(200_000, 300_000, 50_000)
GAS_PRICES = GasPrices(1, 1)
NONCE_SHIFT = 96
SETTLED_DETAIL = "settled in the simulation"
SHORT_DETAIL = "the account does not hold that much"


@dataclass(frozen=True)
class SimulationParts:
    policy: SessionPolicy
    storage: SealedStorage
    balances: Mapping[str, int]
    now: int


class SimulatedSettlement:
    def __init__(self, parts: SimulationParts) -> None:
        self._policy = parts.policy
        self._balances = dict(parts.balances)
        self._received: dict[str, int] = {}
        self._sequence = 0
        self._authorizer = PolicyAuthorizer(
            AuthorizerParts(parts.storage, lambda: parts.now)
        )
        self._authorizer.issue(parts.policy)

    def balance_of(self, asset: str) -> int:
        return self._balances.get(asset, 0)

    def received(self, payee: str) -> int:
        return self._received.get(payee, 0)

    def submit(self, intent: PaymentIntent) -> PaymentOutcome:
        refusal = self._authorize(intent)
        if refusal is not None:
            return refusal
        if self.balance_of(intent.asset) < intent.amount:
            return PaymentOutcome(False, SHORT_DETAIL)
        self._balances[intent.asset] = self.balance_of(intent.asset) - intent.amount
        self._received[intent.payee] = self.received(intent.payee) + intent.amount
        return PaymentOutcome(True, SETTLED_DETAIL)

    def _authorize(self, intent: PaymentIntent) -> PaymentOutcome | None:
        operation = self._operation(intent)
        digest = user_operation_hash(operation, self._policy.domain)
        try:
            self._authorizer.authorize(AuthorizationRequest(digest, operation))
        except EnclaveRefusedError as refusal:
            return PaymentOutcome(False, str(refusal))
        return None

    def _operation(self, intent: PaymentIntent) -> UserOperation:
        self._sequence += 1
        session_key = int(self._policy.validator, 16) << NONCE_SHIFT
        return UserOperation(
            sender=self._policy.account,
            nonce=session_key | self._sequence,
            init_code=b"",
            call_data=encode_single_session_call(
                Execution(intent.payee, intent.amount, b"")
            ),
            gas_limits=GAS_LIMITS,
            gas_prices=GAS_PRICES,
        )
