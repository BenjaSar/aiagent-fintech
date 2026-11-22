import json
from dataclasses import dataclass
from enum import Enum

from .ports import (
    AuthorizationRequest,
    EnclaveRefusedError,
    SealedDataCorruptError,
    SealedDataNotFoundError,
    SealedStorage,
    SigningAuthorizer,
)

HALT_NAME = "halt"
UNREADABLE_REASON = "the halt record cannot be read"


class HaltScope(Enum):
    FLEET = "fleet"
    AGENT = "agent"


class HaltedError(EnclaveRefusedError):
    pass


@dataclass(frozen=True)
class HaltOrder:
    scope: HaltScope
    reason: str
    ordered_at: int


@dataclass(frozen=True)
class HaltScopes:
    fleet: SealedStorage
    agent: SealedStorage


class HaltSwitch:
    def __init__(self, scopes: HaltScopes) -> None:
        self._scopes = scopes

    def halt(self, order: HaltOrder) -> None:
        self._storage_of(order.scope).seal(HALT_NAME, _sealed(order, halted=True))

    def resume(self, order: HaltOrder) -> None:
        self._storage_of(order.scope).seal(HALT_NAME, _sealed(order, halted=False))

    def in_force(self) -> HaltOrder | None:
        for scope in HaltScope:
            order = self._order_of(scope)
            if order is not None:
                return order
        return None

    def _order_of(self, scope: HaltScope) -> HaltOrder | None:
        storage = self._storage_of(scope)
        try:
            return _order_from(scope, json.loads(storage.unseal(HALT_NAME)))
        except SealedDataNotFoundError:
            return None
        except (SealedDataCorruptError, ValueError, TypeError):
            return HaltOrder(scope, UNREADABLE_REASON, 0)

    def _storage_of(self, scope: HaltScope) -> SealedStorage:
        if scope is HaltScope.FLEET:
            return self._scopes.fleet
        return self._scopes.agent


@dataclass(frozen=True)
class GuardParts:
    switch: HaltSwitch
    authorizer: SigningAuthorizer


class HaltGuard:
    def __init__(self, parts: GuardParts) -> None:
        self._parts = parts

    def authorize(self, request: AuthorizationRequest) -> None:
        order = self._parts.switch.in_force()
        if order is not None:
            raise HaltedError(f"the {order.scope.value} is halted: {order.reason}")
        self._parts.authorizer.authorize(request)


def _sealed(order: HaltOrder, halted: bool) -> bytes:
    return json.dumps(
        {
            "halted": halted,
            "scope": order.scope.value,
            "reason": order.reason,
            "ordered_at": order.ordered_at,
        }
    ).encode()


def _order_from(scope: HaltScope, document: object) -> HaltOrder | None:
    if not isinstance(document, dict):
        return HaltOrder(scope, UNREADABLE_REASON, 0)
    if not document.get("halted"):
        return None
    return HaltOrder(
        scope, str(document.get("reason", "")), int(document.get("ordered_at", 0))
    )
