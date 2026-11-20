from collections.abc import Sequence

from .group import GROUP_ORDER


class InvalidParticipantListError(ValueError):
    pass


def derive_interpolating_value(identifiers: Sequence[int], identifier: int) -> int:
    _validate_participants(identifiers, identifier)
    numerator = 1
    denominator = 1
    for other in (other for other in identifiers if other != identifier):
        numerator = numerator * other % GROUP_ORDER
        denominator = denominator * (other - identifier) % GROUP_ORDER
    return numerator * pow(denominator, -1, GROUP_ORDER) % GROUP_ORDER


def _validate_participants(identifiers: Sequence[int], identifier: int) -> None:
    if identifier not in identifiers:
        raise InvalidParticipantListError(
            "the identifier is not among the participants"
        )
    if len(set(identifiers)) != len(identifiers):
        raise InvalidParticipantListError("an identifier appears more than once")
