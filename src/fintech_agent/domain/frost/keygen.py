import secrets
from collections.abc import Sequence
from dataclasses import dataclass

from .group import GROUP_ORDER, Point, base_multiply
from .interpolation import derive_interpolating_value

MINIMUM_THRESHOLD = 2


class InvalidKeygenParametersError(ValueError):
    pass


@dataclass(frozen=True)
class SecretShare:
    identifier: int
    value: int


@dataclass(frozen=True)
class SharingPolynomial:
    coefficients: tuple[int, ...]

    def evaluate(self, point: int) -> int:
        result = 0
        for coefficient in reversed(self.coefficients):
            result = (result * point + coefficient) % GROUP_ORDER
        return result


@dataclass(frozen=True)
class DealerOutput:
    shares: tuple[SecretShare, ...]
    group_public_key: Point
    vss_commitment: tuple[Point, ...]


def random_polynomial(secret_key: int, threshold: int) -> SharingPolynomial:
    if threshold < MINIMUM_THRESHOLD:
        raise InvalidKeygenParametersError("the threshold must be at least 2")
    if not 0 < secret_key < GROUP_ORDER:
        raise InvalidKeygenParametersError("the secret key must be a non-zero scalar")
    random_coefficients = tuple(random_non_zero_scalar() for _ in range(threshold - 1))
    return SharingPolynomial((secret_key, *random_coefficients))


def trusted_dealer_keygen(
    polynomial: SharingPolynomial, participant_count: int
) -> DealerOutput:
    _validate_participant_count(len(polynomial.coefficients), participant_count)
    shares = tuple(
        SecretShare(identifier, polynomial.evaluate(identifier))
        for identifier in range(1, participant_count + 1)
    )
    commitment = vss_commit(polynomial)
    return DealerOutput(shares, commitment[0], commitment)


def vss_commit(polynomial: SharingPolynomial) -> tuple[Point, ...]:
    return tuple(base_multiply(coefficient) for coefficient in polynomial.coefficients)


def vss_verify(share: SecretShare, vss_commitment: Sequence[Point]) -> bool:
    return base_multiply(share.value) == evaluate_commitment(
        vss_commitment, share.identifier
    )


def derive_group_info(
    participant_count: int, vss_commitment: Sequence[Point]
) -> tuple[Point, dict[int, Point]]:
    verifying_shares = {
        identifier: evaluate_commitment(vss_commitment, identifier)
        for identifier in range(1, participant_count + 1)
    }
    return vss_commitment[0], verifying_shares


def combine_shares(shares: Sequence[SecretShare], threshold: int) -> int:
    if len(shares) < threshold:
        raise InvalidKeygenParametersError(
            "fewer shares than the threshold were provided"
        )
    identifiers = [share.identifier for share in shares]
    return (
        sum(
            share.value * derive_interpolating_value(identifiers, share.identifier)
            for share in shares
        )
        % GROUP_ORDER
    )


def evaluate_commitment(vss_commitment: Sequence[Point], identifier: int) -> Point:
    result = Point.identity()
    for power, commitment in enumerate(vss_commitment):
        result = result.add(commitment.multiply(pow(identifier, power, GROUP_ORDER)))
    return result


def _validate_participant_count(threshold: int, participant_count: int) -> None:
    if participant_count < threshold or participant_count >= GROUP_ORDER:
        raise InvalidKeygenParametersError(
            "the participant count must be at least the threshold"
        )


def random_non_zero_scalar() -> int:
    return secrets.randbelow(GROUP_ORDER - 1) + 1
