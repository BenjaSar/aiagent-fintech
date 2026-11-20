import json
from dataclasses import dataclass

from fintech_agent.domain.session_policy import Movement, SessionPolicy

from .ports import EnclaveRefusedError, SealedDataNotFoundError, SealedStorage

LEDGER_NAME = "spend-ledger"
MAXIMUM_EVENTS_PER_ASSET = 32


@dataclass(frozen=True)
class SpendEvent:
    timestamp: int
    amount: int
    operation_hash: str


@dataclass(frozen=True)
class Charge:
    operation_hash: bytes
    movements: tuple[Movement, ...]
    now: int


class SpendLedger:
    def __init__(self, storage: SealedStorage) -> None:
        self._storage = storage
        self._events = self._load()

    def spent(self, asset: str, now: int, window_seconds: int) -> int:
        return sum(event.amount for event in self._live(asset, now, window_seconds))

    def charge(self, policy: SessionPolicy, charge: Charge) -> None:
        digest = charge.operation_hash.hex()
        if self._already_counted(policy, charge, digest):
            return
        for movement in charge.movements:
            self._require_room(policy, movement, charge.now)
        for movement in charge.movements:
            live = self._live(movement.asset, charge.now, policy.window_seconds)
            live.append(SpendEvent(charge.now, movement.amount, digest))
            self._events[movement.asset] = live
        self._persist()

    def _already_counted(
        self, policy: SessionPolicy, charge: Charge, digest: str
    ) -> bool:
        return all(
            any(
                event.operation_hash == digest
                for event in self._live(
                    movement.asset, charge.now, policy.window_seconds
                )
            )
            for movement in charge.movements
        )

    def _require_room(
        self, policy: SessionPolicy, movement: Movement, now: int
    ) -> None:
        cap = policy.cap_of(movement.asset)
        live = self._live(movement.asset, now, policy.window_seconds)
        if cap is None:
            raise EnclaveRefusedError(f"the asset {movement.asset} has no cap")
        if len(live) >= MAXIMUM_EVENTS_PER_ASSET:
            raise EnclaveRefusedError(
                f"the rolling window for {movement.asset} is full"
            )
        spent = sum(event.amount for event in live)
        if spent + movement.amount > cap:
            raise EnclaveRefusedError(
                f"the rolling cap for {movement.asset} would be exceeded: "
                f"{spent} already signed, {movement.amount} requested, cap {cap}"
            )

    def _live(self, asset: str, now: int, window_seconds: int) -> list[SpendEvent]:
        return [
            event
            for event in self._events.get(asset, [])
            if now - event.timestamp < window_seconds
        ]

    def _load(self) -> dict[str, list[SpendEvent]]:
        try:
            document = json.loads(self._storage.unseal(LEDGER_NAME))
        except SealedDataNotFoundError:
            return {}
        return {
            asset: [
                SpendEvent(int(timestamp), int(amount), operation_hash)
                for timestamp, amount, operation_hash in events
            ]
            for asset, events in document.items()
        }

    def _persist(self) -> None:
        document = {
            asset: [
                [event.timestamp, str(event.amount), event.operation_hash]
                for event in events
            ]
            for asset, events in self._events.items()
        }
        self._storage.seal(LEDGER_NAME, json.dumps(document).encode())
