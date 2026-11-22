import re
from dataclasses import dataclass

from fintech_agent.domain.plain_text import is_plain_text
from fintech_agent.domain.session_policy import is_normalized_address

PAYEE_IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{1,63}")
PAYEE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")


class PayeeRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovedPayee:
    identifier: str
    address: str
    label: str
    approved_by: str
    approved_at: int

    def __post_init__(self) -> None:
        if not PAYEE_IDENTIFIER.fullmatch(self.identifier):
            raise PayeeRegistryError("a payee identifier is lowercase and hyphenated")
        if not is_normalized_address(self.address):
            raise PayeeRegistryError("a payee address must be lowercase 0x hex")
        if not PAYEE_LABEL.fullmatch(self.label) or not is_plain_text(self.label):
            raise PayeeRegistryError("a payee label must be short plain text")
        if not is_normalized_address(self.approved_by):
            raise PayeeRegistryError("the approver must be a lowercase address")
        if self.approved_at <= 0:
            raise PayeeRegistryError("the approval time must be a positive timestamp")


class PayeeRegistry:
    def __init__(self) -> None:
        self._payees: dict[str, ApprovedPayee] = {}

    @property
    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._payees))

    @property
    def addresses_by_identifier(self) -> dict[str, str]:
        return {payee.identifier: payee.address for payee in self._payees.values()}

    @property
    def labels_by_address(self) -> dict[str, str]:
        return {payee.address: payee.label for payee in self._payees.values()}

    def approve(self, payee: ApprovedPayee) -> None:
        if payee.identifier in self._payees:
            raise PayeeRegistryError(f"{payee.identifier} is already approved")
        if any(known.address == payee.address for known in self._payees.values()):
            raise PayeeRegistryError(f"{payee.address} is already approved")
        self._payees[payee.identifier] = payee

    def withdraw(self, identifier: str) -> None:
        if self._payees.pop(identifier, None) is None:
            raise PayeeRegistryError(f"{identifier} is not an approved payee")

    def payee(self, identifier: str) -> ApprovedPayee:
        known = self._payees.get(identifier)
        if known is None:
            raise PayeeRegistryError(f"{identifier} is not an approved payee")
        return known

    def resolve(self, identifiers: tuple[str, ...]) -> tuple[ApprovedPayee, ...]:
        if len(set(identifiers)) != len(identifiers):
            raise PayeeRegistryError("each payee is named once")
        return tuple(self.payee(identifier) for identifier in identifiers)
