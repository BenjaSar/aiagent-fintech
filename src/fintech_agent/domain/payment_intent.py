import re
from dataclasses import dataclass

from .session_policy import is_normalized_address

IDEMPOTENCY_KEY = re.compile(r"[a-z0-9][a-z0-9-]{7,63}")


class PaymentIntentError(ValueError):
    pass


@dataclass(frozen=True)
class PaymentIntent:
    idempotency_key: str
    payee: str
    asset: str
    amount: int

    def __post_init__(self) -> None:
        if not IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise PaymentIntentError(
                "an idempotency key is 8 to 64 lowercase letters, digits or hyphens"
            )
        if not is_normalized_address(self.payee):
            raise PaymentIntentError("the payee must be a lowercase 0x address")
        if not is_normalized_address(self.asset):
            raise PaymentIntentError("the asset must be a lowercase 0x address")
        if self.amount <= 0:
            raise PaymentIntentError("a payment must be positive")
