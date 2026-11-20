import hashlib
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT))

from fintech_agent.domain.frost.aggregation import (
    ParticipantCommitment,
    SigningPackage,
    aggregate,
)
from fintech_agent.domain.frost.group import Point
from fintech_agent.domain.frost.signing import (
    NonceRandomness,
    SigningParticipant,
    commit,
    compute_signature_share,
)
from tests.domain.frost.vectors import FROST_SECP256K1_VECTOR as VECTOR

QUORUM = (1, 3)


def derived_randomness(label: str) -> bytes:
    return hashlib.sha256(label.encode()).digest()


def main(message_hex: str) -> None:
    message = bytes.fromhex(message_hex)
    group_public_key = Point.deserialize(bytes.fromhex(VECTOR["group_public_key"]))
    secret_shares = {
        identifier: int(VECTOR[f"P{identifier} participant_share"], 16)
        for identifier in QUORUM
    }
    committed = {
        identifier: commit(
            secret_shares[identifier],
            NonceRandomness(
                hiding=derived_randomness(f"session-hiding-{identifier}"),
                binding=derived_randomness(f"session-binding-{identifier}"),
            ),
        )
        for identifier in QUORUM
    }
    package = SigningPackage(
        tuple(
            ParticipantCommitment(
                identifier,
                committed[identifier][1].hiding_nonce_commitment,
                committed[identifier][1].binding_nonce_commitment,
            )
            for identifier in QUORUM
        ),
        message,
        group_public_key,
    )
    signature_shares = {
        identifier: compute_signature_share(
            package,
            SigningParticipant(
                identifier, secret_shares[identifier], committed[identifier][0]
            ),
        )
        for identifier in QUORUM
    }
    print(aggregate(package, signature_shares).serialize().hex())


main(sys.argv[1])
