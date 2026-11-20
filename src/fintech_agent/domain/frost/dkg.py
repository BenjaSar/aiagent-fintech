import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .aggregation import SchnorrSignature
from .group import GROUP_ORDER, Point, base_multiply, serialize_scalar
from .hashing import CONTEXT_STRING, hash_to_dkg_challenge
from .keygen import (
    SecretShare,
    SharingPolynomial,
    evaluate_commitment,
    random_non_zero_scalar,
    vss_commit,
    vss_verify,
)

MINIMUM_THRESHOLD = 2
TRANSCRIPT_DOMAIN = CONTEXT_STRING + b"dkg-transcript"


class DkgFailureError(ValueError):
    def __init__(self, message: str, culprit: int | None = None) -> None:
        super().__init__(message)
        self.culprit = culprit


@dataclass(frozen=True)
class DkgParticipant:
    identifier: int
    threshold: int
    participant_count: int

    def __post_init__(self) -> None:
        if not MINIMUM_THRESHOLD <= self.threshold <= self.participant_count:
            raise DkgFailureError(
                "the threshold must be between 2 and the participant count"
            )
        if not 1 <= self.identifier <= self.participant_count:
            raise DkgFailureError(
                "the identifier must be between 1 and the participant count"
            )

    @property
    def other_identifiers(self) -> frozenset[int]:
        everyone = set(range(1, self.participant_count + 1))
        return frozenset(everyone - {self.identifier})


@dataclass(frozen=True)
class Part1Randomness:
    coefficients: tuple[int, ...]
    proof_nonce: int


@dataclass(frozen=True)
class Round1Package:
    identifier: int
    commitment: tuple[Point, ...]
    proof_of_knowledge: SchnorrSignature


@dataclass(frozen=True)
class Round1Secret:
    participant: DkgParticipant
    coefficients: tuple[int, ...]
    commitment: tuple[Point, ...]


@dataclass(frozen=True)
class Round2Package:
    sender: int
    receiver: int
    share: int


@dataclass(frozen=True)
class Round2Secret:
    participant: DkgParticipant
    commitment: tuple[Point, ...]
    own_share: int


@dataclass(frozen=True)
class ReceivedPackages:
    round1: Mapping[int, Round1Package]
    round2: Mapping[int, Round2Package]


@dataclass(frozen=True)
class DkgResult:
    identifier: int
    secret_share: int
    verifying_share: Point
    group_public_key: Point
    verifying_shares: Mapping[int, Point]


@dataclass(frozen=True)
class ProofContext:
    identifier: int
    public_key: Point


@dataclass(frozen=True)
class ShareDelivery:
    sender: int
    package: Round2Package
    commitment: tuple[Point, ...]


def fresh_part1_randomness(threshold: int) -> Part1Randomness:
    return Part1Randomness(
        coefficients=tuple(random_non_zero_scalar() for _ in range(threshold)),
        proof_nonce=random_non_zero_scalar(),
    )


def dkg_part1(
    participant: DkgParticipant, randomness: Part1Randomness
) -> tuple[Round1Secret, Round1Package]:
    _validate_randomness(participant, randomness)
    commitment = vss_commit(SharingPolynomial(randomness.coefficients))
    context = ProofContext(participant.identifier, commitment[0])
    secret = Round1Secret(participant, randomness.coefficients, commitment)
    package = Round1Package(
        participant.identifier, commitment, _prove_knowledge(context, randomness)
    )
    return secret, package


def dkg_part2(
    secret: Round1Secret, round1_packages: Mapping[int, Round1Package]
) -> tuple[Round2Secret, dict[int, Round2Package]]:
    participant = secret.participant
    _require_exactly_the_others(participant, round1_packages.keys())
    for sender, package in round1_packages.items():
        _verify_round1_package(participant, (sender, package))
    polynomial = SharingPolynomial(secret.coefficients)
    outgoing = {
        receiver: Round2Package(
            participant.identifier, receiver, polynomial.evaluate(receiver)
        )
        for receiver in sorted(participant.other_identifiers)
    }
    own_share = polynomial.evaluate(participant.identifier)
    return Round2Secret(participant, secret.commitment, own_share), outgoing


def dkg_part3(secret: Round2Secret, received: ReceivedPackages) -> DkgResult:
    participant = secret.participant
    _require_exactly_the_others(participant, received.round1.keys())
    _require_exactly_the_others(participant, received.round2.keys())
    secret_share = secret.own_share
    for delivery in _deliveries(received):
        secret_share = (
            secret_share + _accepted_share(participant, delivery)
        ) % GROUP_ORDER
    commitments = [
        secret.commitment,
        *(package.commitment for package in received.round1.values()),
    ]
    return _result(participant, secret_share, commitments)


def transcript_digest(round1_packages: Mapping[int, Round1Package]) -> bytes:
    digest = hashlib.sha256(TRANSCRIPT_DOMAIN)
    for identifier in sorted(round1_packages):
        digest.update(_encode_round1_package(identifier, round1_packages[identifier]))
    return digest.digest()


def _encode_round1_package(identifier: int, package: Round1Package) -> bytes:
    encoded_commitment = b"".join(point.serialize() for point in package.commitment)
    return (
        serialize_scalar(identifier)
        + len(package.commitment).to_bytes(2, "big")
        + encoded_commitment
        + package.proof_of_knowledge.serialize()
    )


def _validate_randomness(
    participant: DkgParticipant, randomness: Part1Randomness
) -> None:
    if len(randomness.coefficients) != participant.threshold:
        raise DkgFailureError("one coefficient per threshold member is required")
    scalars = (*randomness.coefficients, randomness.proof_nonce)
    if any(not 0 < scalar < GROUP_ORDER for scalar in scalars):
        raise DkgFailureError(
            "coefficients and the proof nonce must be non-zero scalars"
        )


def _challenge(context: ProofContext, nonce_commitment: Point) -> int:
    return hash_to_dkg_challenge(
        serialize_scalar(context.identifier)
        + context.public_key.serialize()
        + nonce_commitment.serialize()
    )


def _prove_knowledge(
    context: ProofContext, randomness: Part1Randomness
) -> SchnorrSignature:
    nonce_commitment = base_multiply(randomness.proof_nonce)
    challenge = _challenge(context, nonce_commitment)
    response = (
        randomness.proof_nonce + randomness.coefficients[0] * challenge
    ) % GROUP_ORDER
    return SchnorrSignature(nonce_commitment, response)


def _proof_is_valid(context: ProofContext, proof: SchnorrSignature) -> bool:
    if proof.group_commitment.is_identity:
        return False
    challenge = _challenge(context, proof.group_commitment)
    expected = proof.group_commitment.add(context.public_key.multiply(challenge))
    return base_multiply(proof.response) == expected


def _require_exactly_the_others(
    participant: DkgParticipant, senders: Iterable[int]
) -> None:
    if frozenset(senders) != participant.other_identifiers:
        raise DkgFailureError(
            "packages are required from exactly the other participants"
        )


def _verify_round1_package(
    participant: DkgParticipant, entry: tuple[int, Round1Package]
) -> None:
    sender, package = entry
    if package.identifier != sender:
        raise DkgFailureError(
            "a package claims another identifier than its channel", sender
        )
    if len(package.commitment) != participant.threshold:
        raise DkgFailureError("a commitment has the wrong length", sender)
    if any(point.is_identity for point in package.commitment):
        raise DkgFailureError("a commitment contains the identity element", sender)
    if not _proof_is_valid(
        ProofContext(sender, package.commitment[0]), package.proof_of_knowledge
    ):
        raise DkgFailureError("a proof of knowledge does not verify", sender)


def _deliveries(received: ReceivedPackages) -> list[ShareDelivery]:
    return [
        ShareDelivery(sender, package, received.round1[sender].commitment)
        for sender, package in received.round2.items()
    ]


def _accepted_share(participant: DkgParticipant, delivery: ShareDelivery) -> int:
    package = delivery.package
    if package.sender != delivery.sender or package.receiver != participant.identifier:
        raise DkgFailureError(
            "a share is not addressed from its sender to this participant",
            delivery.sender,
        )
    if not 0 <= package.share < GROUP_ORDER:
        raise DkgFailureError("a share is outside the scalar range", delivery.sender)
    if not vss_verify(
        SecretShare(participant.identifier, package.share), delivery.commitment
    ):
        raise DkgFailureError(
            "a share does not match its sender's commitment", delivery.sender
        )
    return package.share


def _result(
    participant: DkgParticipant, secret_share: int, commitments: list[tuple[Point, ...]]
) -> DkgResult:
    combined = _combine_commitments(commitments)
    verifying_shares = {
        identifier: evaluate_commitment(combined, identifier)
        for identifier in range(1, participant.participant_count + 1)
    }
    verifying_share = verifying_shares[participant.identifier]
    if base_multiply(secret_share) != verifying_share or combined[0].is_identity:
        raise DkgFailureError(
            "the derived share is inconsistent with the group commitments"
        )
    return DkgResult(
        participant.identifier,
        secret_share,
        verifying_share,
        combined[0],
        verifying_shares,
    )


def _combine_commitments(commitments: list[tuple[Point, ...]]) -> tuple[Point, ...]:
    combined = list(commitments[0])
    for commitment in commitments[1:]:
        combined = [
            total.add(point) for total, point in zip(combined, commitment, strict=True)
        ]
    return tuple(combined)
