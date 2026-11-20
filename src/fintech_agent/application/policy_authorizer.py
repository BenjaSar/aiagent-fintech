import json
from collections.abc import Callable
from dataclasses import dataclass

from fintech_agent.domain.session_policy import (
    PolicyViolationError,
    SessionPolicy,
    check_operation,
)
from fintech_agent.domain.session_policy_document import (
    policy_from_document,
    policy_to_document,
)
from fintech_agent.domain.user_operation_hash import user_operation_hash

from .ports import (
    AuthorizationRequest,
    EnclaveRefusedError,
    SealedDataNotFoundError,
    SealedStorage,
)
from .spend_ledger import Charge, SpendLedger

POLICY_NAME = "session-policy"


@dataclass(frozen=True)
class AuthorizerParts:
    storage: SealedStorage
    clock: Callable[[], int]


class PolicyAuthorizer:
    def __init__(self, parts: AuthorizerParts) -> None:
        self._parts = parts
        self._ledger = SpendLedger(parts.storage)
        self._policy = self._load_policy()

    def issue(self, policy: SessionPolicy) -> None:
        if self._policy is not None:
            raise EnclaveRefusedError("a session policy is already installed")
        self._parts.storage.seal(
            POLICY_NAME, json.dumps(policy_to_document(policy)).encode()
        )
        self._policy = policy

    def authorize(self, request: AuthorizationRequest) -> None:
        policy = self._policy
        if policy is None:
            raise EnclaveRefusedError("no session policy is installed")
        operation = request.operation
        if operation is None:
            raise EnclaveRefusedError(
                "the request carries no user operation to validate"
            )
        if user_operation_hash(operation, policy.domain) != request.message:
            raise EnclaveRefusedError(
                "the hash to sign does not match the user operation"
            )
        now = self._parts.clock()
        try:
            movements = check_operation(policy, operation, now)
        except PolicyViolationError as violation:
            raise EnclaveRefusedError(str(violation)) from violation
        self._ledger.charge(policy, Charge(request.message, movements, now))

    def _load_policy(self) -> SessionPolicy | None:
        try:
            document = json.loads(self._parts.storage.unseal(POLICY_NAME))
        except SealedDataNotFoundError:
            return None
        return policy_from_document(document)
