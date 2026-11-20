from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from fintech_agent.domain.frost.dkg import Round1Package
from fintech_agent.domain.frost.group import Point


@dataclass(frozen=True)
class CeremonyRequest:
    ceremony_identifier: bytes
    threshold: int
    participant_count: int


@dataclass(frozen=True)
class Round2Request:
    ceremony_identifier: bytes
    round1_packages: Mapping[int, Round1Package]


@dataclass(frozen=True)
class SealedRound2Package:
    sender: int
    receiver: int
    sealed: bytes


@dataclass(frozen=True)
class FinishRequest:
    ceremony_identifier: bytes
    round1_packages: Mapping[int, Round1Package]
    sealed_packages: Mapping[int, SealedRound2Package]


@dataclass(frozen=True)
class PublicKeyGeneration:
    identifier: int
    group_public_key: Point
    verifying_shares: Mapping[int, Point]


@dataclass(frozen=True)
class ConfirmRequest:
    ceremony_identifier: bytes
    group_public_key: Point


class KeyGenerationGateway(Protocol):
    def begin_ceremony(self, request: CeremonyRequest) -> Round1Package: ...

    def produce_round2(
        self, request: Round2Request
    ) -> dict[int, SealedRound2Package]: ...

    def finish_ceremony(self, request: FinishRequest) -> PublicKeyGeneration: ...

    def confirm_ceremony(self, request: ConfirmRequest) -> None: ...

    def abandon_ceremony(self, ceremony_identifier: bytes) -> None: ...
