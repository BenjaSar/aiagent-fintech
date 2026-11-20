from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

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
from fintech_agent.domain.frost.group import deserialize_scalar, serialize_scalar

from .enclave_node import KEY_SHARE_NAME
from .key_generation_ports import (
    CeremonyRequest,
    ConfirmRequest,
    FinishRequest,
    PublicKeyGeneration,
    Round2Request,
    SealedRound2Package,
)
from .key_share_record import KeyShareRecord
from .ports import (
    ChannelMessage,
    EnclaveRefusedError,
    SealedDataNotFoundError,
    SealedStorage,
    SecureChannel,
    SecureChannelError,
)

IDENTIFIER_LENGTH = 2


@dataclass(frozen=True)
class DkgEnclaveSetup:
    identifier: int
    roster_size: int
    channel: SecureChannel
    storage: SealedStorage
    randomness_source: Callable[[int], Part1Randomness] = fresh_part1_randomness


@dataclass(frozen=True)
class KeyGenerationOutcome:
    public: PublicKeyGeneration
    record: KeyShareRecord


@dataclass
class _CeremonyState:
    ceremony_identifier: bytes
    participant: DkgParticipant
    round1_secret: Round1Secret
    own_package: Round1Package
    round2_secret: Round2Secret | None = None
    digest: bytes | None = None
    pending: KeyGenerationOutcome | None = None


class DkgEnclave:
    def __init__(self, setup: DkgEnclaveSetup) -> None:
        self._setup = setup
        self._state: _CeremonyState | None = None

    def begin(self, request: CeremonyRequest) -> Round1Package:
        self._require_no_key_share()
        if request.participant_count != self._setup.roster_size:
            raise EnclaveRefusedError("the participant count differs from the roster")
        participant = self._participant_for(request)
        secret, package = dkg_part1(
            participant, self._setup.randomness_source(request.threshold)
        )
        self._state = _CeremonyState(
            request.ceremony_identifier, participant, secret, package
        )
        return package

    def produce_round2(self, request: Round2Request) -> dict[int, SealedRound2Package]:
        state = self._active(request.ceremony_identifier)
        if state.round2_secret is not None:
            raise EnclaveRefusedError("round two was already produced")
        try:
            secret, outgoing = dkg_part2(state.round1_secret, request.round1_packages)
        except DkgFailureError as failure:
            raise _refusal(failure) from failure
        state.round2_secret = secret
        state.digest = self._digest(state, request.round1_packages)
        return {
            receiver: self._seal(state, package)
            for receiver, package in outgoing.items()
        }

    def finish(self, request: FinishRequest) -> PublicKeyGeneration:
        state = self._active(request.ceremony_identifier)
        self._require_no_key_share()
        if state.round2_secret is None or state.digest is None:
            raise EnclaveRefusedError("round two has not been produced")
        if self._digest(state, request.round1_packages) != state.digest:
            raise EnclaveRefusedError("the round one view changed after round two")
        received = ReceivedPackages(
            request.round1_packages, self._open_all(state, request.sealed_packages)
        )
        try:
            result = dkg_part3(state.round2_secret, received)
        except DkgFailureError as failure:
            raise _refusal(failure) from failure
        state.pending = self._outcome(state, result)
        return state.pending.public

    def confirm(self, request: ConfirmRequest) -> KeyGenerationOutcome:
        state = self._active(request.ceremony_identifier)
        self._require_no_key_share()
        if state.pending is None:
            raise EnclaveRefusedError("the ceremony has not finished")
        if state.pending.public.group_public_key != request.group_public_key:
            raise EnclaveRefusedError("the confirmed group key differs from this one")
        self._setup.storage.seal(KEY_SHARE_NAME, state.pending.record.serialize())
        self._state = None
        return state.pending

    def abandon(self, ceremony_identifier: bytes) -> None:
        if self._state and self._state.ceremony_identifier == ceremony_identifier:
            self._state = None

    def _participant_for(self, request: CeremonyRequest) -> DkgParticipant:
        try:
            return DkgParticipant(
                self._setup.identifier, request.threshold, request.participant_count
            )
        except DkgFailureError as failure:
            raise _refusal(failure) from failure

    def _active(self, ceremony_identifier: bytes) -> _CeremonyState:
        if (
            self._state is None
            or self._state.ceremony_identifier != ceremony_identifier
        ):
            raise EnclaveRefusedError("no such ceremony is in progress")
        return self._state

    def _require_no_key_share(self) -> None:
        try:
            self._setup.storage.unseal(KEY_SHARE_NAME)
        except SealedDataNotFoundError:
            return
        raise EnclaveRefusedError("this enclave already holds a key share")

    def _digest(
        self, state: _CeremonyState, others: Mapping[int, Round1Package]
    ) -> bytes:
        return transcript_digest({**others, self._setup.identifier: state.own_package})

    def _associated_data(self, state: _CeremonyState, route: tuple[int, int]) -> bytes:
        sender, receiver = route
        return (
            state.ceremony_identifier
            + sender.to_bytes(IDENTIFIER_LENGTH, "big")
            + receiver.to_bytes(IDENTIFIER_LENGTH, "big")
            + (state.digest or b"")
        )

    def _seal(
        self, state: _CeremonyState, package: Round2Package
    ) -> SealedRound2Package:
        route = (package.sender, package.receiver)
        message = ChannelMessage(
            package.sender,
            package.receiver,
            self._associated_data(state, route),
            serialize_scalar(package.share),
        )
        return SealedRound2Package(
            package.sender, package.receiver, self._setup.channel.seal(message)
        )

    def _open_all(
        self, state: _CeremonyState, sealed: Mapping[int, SealedRound2Package]
    ) -> dict[int, Round2Package]:
        return {
            sender: self._open(state, package) for sender, package in sealed.items()
        }

    def _open(
        self, state: _CeremonyState, package: SealedRound2Package
    ) -> Round2Package:
        if package.receiver != self._setup.identifier:
            raise EnclaveRefusedError(
                f"a package from participant {package.sender} is for someone else"
            )
        route = (package.sender, package.receiver)
        message = ChannelMessage(
            package.sender,
            package.receiver,
            self._associated_data(state, route),
            package.sealed,
        )
        try:
            share = deserialize_scalar(self._setup.channel.open(message))
        except (SecureChannelError, ValueError) as failure:
            raise EnclaveRefusedError(
                f"a package from participant {package.sender} was not accepted"
            ) from failure
        return Round2Package(package.sender, package.receiver, share)

    def _outcome(
        self, state: _CeremonyState, result: DkgResult
    ) -> KeyGenerationOutcome:
        record = KeyShareRecord(
            result.identifier,
            state.participant.threshold,
            result.secret_share,
            result.group_public_key,
        )
        public = PublicKeyGeneration(
            result.identifier, result.group_public_key, result.verifying_shares
        )
        return KeyGenerationOutcome(public, record)


def _refusal(failure: DkgFailureError) -> EnclaveRefusedError:
    suffix = f" (participant {failure.culprit})" if failure.culprit else ""
    return EnclaveRefusedError(str(failure) + suffix)
