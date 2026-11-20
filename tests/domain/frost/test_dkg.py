import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace

import pytest

from fintech_agent.domain.frost.aggregation import (
    ParticipantCommitment,
    SchnorrSignature,
    SigningPackage,
    aggregate,
    verify_signature,
)
from fintech_agent.domain.frost.dkg import (
    DkgFailureError,
    DkgParticipant,
    DkgResult,
    Part1Randomness,
    ReceivedPackages,
    Round1Package,
    Round1Secret,
    Round2Package,
    Round2Secret,
    dkg_part1,
    dkg_part2,
    dkg_part3,
    fresh_part1_randomness,
    transcript_digest,
)
from fintech_agent.domain.frost.group import GROUP_ORDER, base_multiply
from fintech_agent.domain.frost.keygen import SecretShare, combine_shares
from fintech_agent.domain.frost.signing import (
    NonceRandomness,
    SigningParticipant,
    commit,
    compute_signature_share,
)


def derive_scalar(label: str) -> int:
    digest = hashlib.sha256(label.encode()).digest()
    return int.from_bytes(digest, "big") % (GROUP_ORDER - 1) + 1


def randomness_for(identifier: int, threshold: int) -> Part1Randomness:
    return Part1Randomness(
        coefficients=tuple(
            derive_scalar(f"coefficient-{identifier}-{index}")
            for index in range(threshold)
        ),
        proof_nonce=derive_scalar(f"proof-nonce-{identifier}"),
    )


@dataclass(frozen=True)
class DkgRun:
    participants: dict[int, DkgParticipant]
    round1_secrets: dict[int, Round1Secret]
    round1_packages: dict[int, Round1Package]
    round2_secrets: dict[int, Round2Secret]
    round2_outgoing: dict[int, dict[int, Round2Package]]


def others(
    packages: Mapping[int, Round1Package], identifier: int
) -> dict[int, Round1Package]:
    return {
        sender: package for sender, package in packages.items() if sender != identifier
    }


def incoming(run: DkgRun, receiver: int) -> dict[int, Round2Package]:
    return {
        sender: outgoing[receiver]
        for sender, outgoing in run.round2_outgoing.items()
        if sender != receiver
    }


def run_through_part2(threshold: int, participant_count: int) -> DkgRun:
    participants = {
        identifier: DkgParticipant(identifier, threshold, participant_count)
        for identifier in range(1, participant_count + 1)
    }
    part1 = {
        identifier: dkg_part1(participant, randomness_for(identifier, threshold))
        for identifier, participant in participants.items()
    }
    round1_secrets = {identifier: result[0] for identifier, result in part1.items()}
    round1_packages = {identifier: result[1] for identifier, result in part1.items()}
    part2 = {
        identifier: dkg_part2(
            round1_secrets[identifier], others(round1_packages, identifier)
        )
        for identifier in participants
    }
    return DkgRun(
        participants,
        round1_secrets,
        round1_packages,
        {identifier: result[0] for identifier, result in part2.items()},
        {identifier: result[1] for identifier, result in part2.items()},
    )


def finish(run: DkgRun) -> dict[int, DkgResult]:
    return {
        identifier: dkg_part3(
            run.round2_secrets[identifier],
            ReceivedPackages(
                others(run.round1_packages, identifier), incoming(run, identifier)
            ),
        )
        for identifier in run.participants
    }


def secret_sum(run: DkgRun) -> int:
    return (
        sum(secret.coefficients[0] for secret in run.round1_secrets.values())
        % GROUP_ORDER
    )


@pytest.mark.parametrize(("threshold", "participant_count"), [(2, 3), (3, 5)])
def test_every_participant_derives_the_same_group_key(
    threshold: int, participant_count: int
) -> None:
    run = run_through_part2(threshold, participant_count)

    results = finish(run)

    group_keys = {result.group_public_key for result in results.values()}
    assert group_keys == {base_multiply(secret_sum(run))}


@pytest.mark.parametrize(("threshold", "participant_count"), [(2, 3), (3, 5)])
def test_verifying_shares_match_each_participants_secret_share(
    threshold: int, participant_count: int
) -> None:
    results = finish(run_through_part2(threshold, participant_count))

    for identifier, result in results.items():
        assert result.verifying_share == base_multiply(result.secret_share)
        assert result.verifying_shares[identifier] == result.verifying_share
    assert (
        len(
            {
                tuple(sorted(result.verifying_shares.items(), key=lambda item: item[0]))
                for result in results.values()
            }
        )
        == 1
    )


@pytest.mark.parametrize(("threshold", "participant_count"), [(2, 3), (3, 5)])
def test_any_threshold_of_shares_reconstructs_the_summed_secret(
    threshold: int, participant_count: int
) -> None:
    run = run_through_part2(threshold, participant_count)
    results = finish(run)
    chosen = [
        SecretShare(identifier, results[identifier].secret_share)
        for identifier in range(participant_count, participant_count - threshold, -1)
    ]

    assert combine_shares(chosen, threshold) == secret_sum(run)


def sign_with_quorum(
    results: Mapping[int, DkgResult], quorum: tuple[int, ...]
) -> tuple[SchnorrSignature, SigningPackage]:
    message = hashlib.sha256(b"user operation hash").digest()
    committed = {
        identifier: commit(
            results[identifier].secret_share,
            NonceRandomness(
                hashlib.sha256(f"hiding-{identifier}".encode()).digest(),
                hashlib.sha256(f"binding-{identifier}".encode()).digest(),
            ),
        )
        for identifier in quorum
    }
    package = SigningPackage(
        tuple(
            ParticipantCommitment(
                identifier,
                committed[identifier][1].hiding_nonce_commitment,
                committed[identifier][1].binding_nonce_commitment,
            )
            for identifier in quorum
        ),
        message,
        results[quorum[0]].group_public_key,
    )
    shares = {
        identifier: compute_signature_share(
            package,
            SigningParticipant(
                identifier, results[identifier].secret_share, committed[identifier][0]
            ),
        )
        for identifier in quorum
    }
    return aggregate(package, shares), package


@pytest.mark.parametrize(
    ("threshold", "participant_count", "quorum"),
    [(2, 3, (2, 3)), (2, 3, (1, 3)), (3, 5, (1, 4, 5)), (3, 5, (2, 3, 4))],
)
def test_dkg_shares_produce_a_signature_that_verifies_under_the_group_key(
    threshold: int, participant_count: int, quorum: tuple[int, ...]
) -> None:
    results = finish(run_through_part2(threshold, participant_count))

    signature, package = sign_with_quorum(results, quorum)

    assert verify_signature(signature, package)


def test_signing_below_the_threshold_does_not_verify() -> None:
    results = finish(run_through_part2(3, 5))
    full_signature, package = sign_with_quorum(results, (1, 4, 5))
    partial = SchnorrSignature(
        full_signature.group_commitment, (full_signature.response + 1) % GROUP_ORDER
    )

    assert not verify_signature(partial, package)


def test_a_forged_proof_of_knowledge_names_its_sender() -> None:
    run = run_through_part2(2, 3)
    forged = replace(
        run.round1_packages[2],
        proof_of_knowledge=SchnorrSignature(
            run.round1_packages[2].proof_of_knowledge.group_commitment,
            (run.round1_packages[2].proof_of_knowledge.response + 1) % GROUP_ORDER,
        ),
    )
    packages = {**others(run.round1_packages, 1), 2: forged}

    with pytest.raises(DkgFailureError) as failure:
        dkg_part2(run.round1_secrets[1], packages)

    assert failure.value.culprit == 2


def test_a_proof_for_another_participants_key_is_rejected() -> None:
    run = run_through_part2(2, 3)
    borrowed = replace(
        run.round1_packages[2],
        proof_of_knowledge=run.round1_packages[3].proof_of_knowledge,
    )
    packages = {**others(run.round1_packages, 1), 2: borrowed}

    with pytest.raises(DkgFailureError) as failure:
        dkg_part2(run.round1_secrets[1], packages)

    assert failure.value.culprit == 2


def test_a_commitment_of_the_wrong_length_is_rejected() -> None:
    run = run_through_part2(3, 5)
    shortened = replace(
        run.round1_packages[2], commitment=run.round1_packages[2].commitment[:2]
    )
    packages = {**others(run.round1_packages, 1), 2: shortened}

    with pytest.raises(DkgFailureError) as failure:
        dkg_part2(run.round1_secrets[1], packages)

    assert failure.value.culprit == 2


def test_part2_requires_exactly_the_other_participants_packages() -> None:
    run = run_through_part2(2, 3)
    missing = {2: run.round1_packages[2]}
    own_included = {**others(run.round1_packages, 1), 1: run.round1_packages[1]}

    with pytest.raises(DkgFailureError):
        dkg_part2(run.round1_secrets[1], missing)
    with pytest.raises(DkgFailureError):
        dkg_part2(run.round1_secrets[1], own_included)


def test_a_tampered_share_names_its_sender() -> None:
    run = run_through_part2(2, 3)
    honest = incoming(run, 1)
    tampered = {
        **honest,
        3: replace(honest[3], share=(honest[3].share + 1) % GROUP_ORDER),
    }

    with pytest.raises(DkgFailureError) as failure:
        dkg_part3(
            run.round2_secrets[1],
            ReceivedPackages(others(run.round1_packages, 1), tampered),
        )

    assert failure.value.culprit == 3


def test_a_share_addressed_to_someone_else_is_rejected() -> None:
    run = run_through_part2(2, 3)
    misaddressed = {**incoming(run, 1), 2: run.round2_outgoing[2][3]}

    with pytest.raises(DkgFailureError):
        dkg_part3(
            run.round2_secrets[1],
            ReceivedPackages(others(run.round1_packages, 1), misaddressed),
        )


def test_part3_requires_a_share_from_every_other_participant() -> None:
    run = run_through_part2(2, 3)
    incomplete = {2: incoming(run, 1)[2]}

    with pytest.raises(DkgFailureError):
        dkg_part3(
            run.round2_secrets[1],
            ReceivedPackages(others(run.round1_packages, 1), incomplete),
        )


def test_transcript_digest_detects_an_inconsistent_round_one_view() -> None:
    run = run_through_part2(2, 3)
    altered = {**run.round1_packages, 2: run.round1_packages[3]}

    assert transcript_digest(run.round1_packages) == transcript_digest(
        dict(reversed(run.round1_packages.items()))
    )
    assert transcript_digest(run.round1_packages) != transcript_digest(altered)


def test_participant_parameters_are_validated() -> None:
    with pytest.raises(DkgFailureError):
        DkgParticipant(0, 2, 3)
    with pytest.raises(DkgFailureError):
        DkgParticipant(4, 2, 3)
    with pytest.raises(DkgFailureError):
        DkgParticipant(1, 1, 3)
    with pytest.raises(DkgFailureError):
        DkgParticipant(1, 4, 3)


def test_randomness_must_match_the_threshold_and_be_non_zero() -> None:
    participant = DkgParticipant(1, 3, 5)

    with pytest.raises(DkgFailureError):
        dkg_part1(
            participant,
            replace(
                randomness_for(1, 3), coefficients=randomness_for(1, 2).coefficients
            ),
        )
    with pytest.raises(DkgFailureError):
        dkg_part1(participant, replace(randomness_for(1, 3), proof_nonce=0))
    with pytest.raises(DkgFailureError):
        zero_secret = (0, *randomness_for(1, 3).coefficients[1:])
        dkg_part1(participant, replace(randomness_for(1, 3), coefficients=zero_secret))


def test_fresh_randomness_has_the_threshold_length_and_never_repeats() -> None:
    first = fresh_part1_randomness(3)
    second = fresh_part1_randomness(3)

    assert len(first.coefficients) == 3
    assert first != second
    assert all(0 < value < GROUP_ORDER for value in first.coefficients)
