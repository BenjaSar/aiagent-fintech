from dataclasses import dataclass
from enum import Enum
from typing import NoReturn

from fintech_agent.application.ports import CardSignatureCheck, CardSignatureVerifier
from fintech_agent.domain.agent_card import (
    AgentCard,
    AgentCardError,
    normalize_origin,
    parse_agent_card,
)
from fintech_agent.domain.lookalike import are_lookalikes


class AgentRejectedError(Exception):
    pass


class MalformedCardError(AgentRejectedError):
    pass


class UnknownAgentError(AgentRejectedError):
    pass


class LookalikeAgentError(AgentRejectedError):
    pass


class OriginMismatchError(AgentRejectedError):
    pass


class InvalidSignatureError(AgentRejectedError):
    pass


class CardChangedError(AgentRejectedError):
    pass


class AgentQuarantinedError(AgentRejectedError):
    pass


class RegistrationError(AgentRejectedError):
    pass


class AgentStatus(Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class Registration:
    public_key: bytes
    card: bytes


@dataclass(frozen=True)
class RegisteredAgent:
    decentralized_identifier: str
    name: str
    origin: str
    public_key: bytes
    pinned_digest: bytes
    status: AgentStatus


class AgentDirectory:
    def __init__(self, verifier: CardSignatureVerifier) -> None:
        self._verifier = verifier
        self._agents: dict[str, RegisteredAgent] = {}

    def register(self, registration: Registration) -> RegisteredAgent:
        card = self._parse(registration.card)
        self._require_signed(card, registration.public_key)
        self._require_distinct(card)
        agent = RegisteredAgent(
            card.decentralized_identifier,
            card.name,
            card.origin,
            registration.public_key,
            card.digest,
            AgentStatus.ACTIVE,
        )
        self._agents[agent.decentralized_identifier] = agent
        return agent

    def remove(self, decentralized_identifier: str) -> None:
        self._agents.pop(decentralized_identifier, None)

    def is_active(self, decentralized_identifier: str) -> bool:
        agent = self._agents.get(decentralized_identifier)
        return agent is not None and agent.status is AgentStatus.ACTIVE

    def admit(self, raw_card: bytes) -> RegisteredAgent:
        card = self._parse(raw_card)
        agent = self._registered_for(card)
        if agent.status is AgentStatus.QUARANTINED:
            raise AgentQuarantinedError(
                "the agent is quarantined until its owner re-approves it"
            )
        if card.origin != agent.origin:
            raise OriginMismatchError("the card is served from another origin")
        self._require_signed(card, agent.public_key)
        if card.digest != agent.pinned_digest:
            self._set_status(agent, AgentStatus.QUARANTINED)
            raise CardChangedError(
                "a correctly signed card differs from the pinned card"
            )
        return agent

    def approve_change(self, raw_card: bytes) -> RegisteredAgent:
        card = self._parse(raw_card)
        agent = self._registered_for(card)
        if card.origin != agent.origin:
            raise OriginMismatchError("the card is served from another origin")
        self._require_signed(card, agent.public_key)
        approved = RegisteredAgent(
            agent.decentralized_identifier,
            card.name,
            agent.origin,
            agent.public_key,
            card.digest,
            AgentStatus.ACTIVE,
        )
        self._agents[approved.decentralized_identifier] = approved
        return approved

    def resolve_origin(self, address: str) -> RegisteredAgent:
        try:
            origin = normalize_origin(address)
        except AgentCardError as failure:
            raise MalformedCardError(str(failure)) from failure
        for agent in self._agents.values():
            if agent.origin == origin:
                return agent
        self._refuse_unregistered(origin, "")

    def _parse(self, raw: bytes) -> AgentCard:
        try:
            return parse_agent_card(raw)
        except AgentCardError as failure:
            raise MalformedCardError(str(failure)) from failure

    def _require_signed(self, card: AgentCard, public_key: bytes) -> None:
        check = CardSignatureCheck(public_key, card.signed_body, card.signature)
        if not self._verifier.verify(check):
            raise InvalidSignatureError("the card signature does not verify")

    def _registered_for(self, card: AgentCard) -> RegisteredAgent:
        agent = self._agents.get(card.decentralized_identifier)
        if agent is not None:
            return agent
        self._refuse_unregistered(card.origin, card.name)

    def _refuse_unregistered(self, origin: str, name: str) -> NoReturn:
        for agent in self._agents.values():
            if are_lookalikes(origin, agent.origin) or (
                name and are_lookalikes(name, agent.name)
            ):
                raise LookalikeAgentError(
                    f"this agent imitates the registered agent {agent.name}"
                )
        raise UnknownAgentError("no registered agent matches")

    def _require_distinct(self, card: AgentCard) -> None:
        for agent in self._agents.values():
            if agent.decentralized_identifier == card.decentralized_identifier:
                raise RegistrationError("the identifier is already registered")
            if agent.origin == card.origin:
                raise RegistrationError("the origin is already registered")
            if (
                card.name.lower() == agent.name.lower()
                or are_lookalikes(card.origin, agent.origin)
                or are_lookalikes(card.name, agent.name)
            ):
                raise RegistrationError(
                    f"the origin or name is too close to {agent.name}"
                )

    def _set_status(self, agent: RegisteredAgent, status: AgentStatus) -> None:
        self._agents[agent.decentralized_identifier] = RegisteredAgent(
            agent.decentralized_identifier,
            agent.name,
            agent.origin,
            agent.public_key,
            agent.pinned_digest,
            status,
        )
