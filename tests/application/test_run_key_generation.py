from dataclasses import dataclass, replace

import pytest

from fintech_agent.adapters.in_memory import (
    InMemorySealedStorage,
    InProcessEnclaveGateway,
    InProcessKeyGenerationGateway,
    SimulatedAttestationVerifier,
)
from fintech_agent.adapters.x25519_channel import (
    KeyPair,
    X25519ChaChaChannel,
    generate_identity,
)
from fintech_agent.application.dkg_enclave import DkgEnclave, DkgEnclaveSetup
from fintech_agent.application.enclave_node import KEY_SHARE_NAME
from fintech_agent.application.enclave_service import EnclaveService, ServiceParts
from fintech_agent.application.key_generation_ports import (
    CeremonyRequest,
    ConfirmRequest,
    FinishRequest,
    KeyGenerationGateway,
    PublicKeyGeneration,
    Round2Request,
    SealedRound2Package,
)
from fintech_agent.application.ports import (
    CommitmentRequest,
    EnclaveGateway,
    EnclaveRefusedError,
    EnclaveUnavailableError,
    SealedDataNotFoundError,
)
from fintech_agent.application.run_key_generation import (
    KeyGenerationFailedError,
    KeyGenerationSetup,
    RunKeyGeneration,
)
from fintech_agent.application.sign_user_operation import (
    GroupConfiguration,
    SigningRequest,
    SigningSetup,
    SignUserOperation,
)
from fintech_agent.domain.frost.dkg import Round1Package
from fintech_agent.domain.frost.group import GENERATOR
from tests.application.fixtures import (
    USER_OPERATION_HASH,
    permissive_dependencies,
    signature_verifies,
)

THRESHOLD = 2
IDENTIFIERS = (1, 2, 3)


@dataclass(frozen=True)
class Enclaves:
    services: dict[int, EnclaveService]
    storages: dict[int, InMemorySealedStorage]
    identities: dict[int, KeyPair]
    roster: dict[int, bytes]

    def gateways(self) -> dict[int, KeyGenerationGateway]:
        return {
            identifier: InProcessKeyGenerationGateway(service)
            for identifier, service in self.services.items()
        }

    def holders_of_key_shares(self) -> list[int]:
        holders = []
        for identifier, storage in self.storages.items():
            try:
                storage.unseal(KEY_SHARE_NAME)
                holders.append(identifier)
            except SealedDataNotFoundError:
                continue
        return holders


def build_service(
    identifier: int, enclaves: Enclaves, storage: InMemorySealedStorage
) -> EnclaveService:
    channel = X25519ChaChaChannel(enclaves.identities[identifier], enclaves.roster)
    return EnclaveService(
        ServiceParts(
            identifier,
            permissive_dependencies(),
            ceremony=DkgEnclave(
                DkgEnclaveSetup(identifier, len(enclaves.roster), channel, storage)
            ),
        )
    )


def build_enclaves() -> Enclaves:
    identities = {identifier: generate_identity() for identifier in IDENTIFIERS}
    roster = {identifier: pair.public_key for identifier, pair in identities.items()}
    storages = {identifier: InMemorySealedStorage() for identifier in IDENTIFIERS}
    skeleton = Enclaves({}, storages, identities, roster)
    services = {
        identifier: build_service(identifier, skeleton, storages[identifier])
        for identifier in IDENTIFIERS
    }
    return Enclaves(services, storages, identities, roster)


def run(
    gateways: dict[int, KeyGenerationGateway],
) -> GroupConfiguration:
    return RunKeyGeneration(KeyGenerationSetup(THRESHOLD, gateways)).execute()


def test_the_ceremony_leaves_every_enclave_with_a_share_and_one_group_key() -> None:
    enclaves = build_enclaves()

    configuration = run(enclaves.gateways())

    assert enclaves.holders_of_key_shares() == [1, 2, 3]
    assert configuration.threshold == THRESHOLD
    assert set(configuration.verifying_shares) == set(IDENTIFIERS)


def test_the_enclaves_sign_with_the_key_they_generated_together() -> None:
    enclaves = build_enclaves()
    configuration = run(enclaves.gateways())
    signing_gateways: dict[int, EnclaveGateway] = {
        identifier: InProcessEnclaveGateway(service)
        for identifier, service in enclaves.services.items()
    }
    setup = SigningSetup(
        configuration,
        signing_gateways,
        SimulatedAttestationVerifier(),
        allow_simulated_attestation=True,
    )

    signature = SignUserOperation(setup).execute(SigningRequest(USER_OPERATION_HASH))

    assert signature_verifies(
        signature, USER_OPERATION_HASH, configuration.group_public_key
    )


def test_two_ceremonies_produce_different_keys() -> None:
    first = run(build_enclaves().gateways())
    second = run(build_enclaves().gateways())

    assert first.group_public_key != second.group_public_key


class TamperingGateway:
    def __init__(self, inner: KeyGenerationGateway) -> None:
        self._inner = inner

    def begin_ceremony(self, request: CeremonyRequest) -> Round1Package:
        return self._inner.begin_ceremony(request)

    def produce_round2(self, request: Round2Request) -> dict[int, SealedRound2Package]:
        packages = self._inner.produce_round2(request)
        return {
            receiver: self._altered(package) for receiver, package in packages.items()
        }

    def finish_ceremony(self, request: FinishRequest) -> PublicKeyGeneration:
        return self._inner.finish_ceremony(request)

    def confirm_ceremony(self, request: ConfirmRequest) -> None:
        self._inner.confirm_ceremony(request)

    def abandon_ceremony(self, ceremony_identifier: bytes) -> None:
        self._inner.abandon_ceremony(ceremony_identifier)

    def _altered(self, package: SealedRound2Package) -> SealedRound2Package:
        flipped = bytearray(package.sealed)
        flipped[-1] ^= 0x01
        return replace(package, sealed=bytes(flipped))


def test_a_tampered_round_two_package_aborts_and_no_enclave_keeps_a_share() -> None:
    enclaves = build_enclaves()
    gateways = enclaves.gateways()
    gateways[3] = TamperingGateway(gateways[3])

    with pytest.raises(KeyGenerationFailedError) as failure:
        run(gateways)

    assert "participant 3" in str(failure.value)
    assert enclaves.holders_of_key_shares() == []


class EquivocatingGateway:
    def __init__(self, inner: KeyGenerationGateway, substitute: Round1Package) -> None:
        self._inner = inner
        self._substitute = substitute

    def begin_ceremony(self, request: CeremonyRequest) -> Round1Package:
        return self._inner.begin_ceremony(request)

    def produce_round2(self, request: Round2Request) -> dict[int, SealedRound2Package]:
        shown = dict(request.round1_packages)
        shown[3] = self._substitute
        return self._inner.produce_round2(replace(request, round1_packages=shown))

    def finish_ceremony(self, request: FinishRequest) -> PublicKeyGeneration:
        return self._inner.finish_ceremony(request)

    def confirm_ceremony(self, request: ConfirmRequest) -> None:
        self._inner.confirm_ceremony(request)

    def abandon_ceremony(self, ceremony_identifier: bytes) -> None:
        self._inner.abandon_ceremony(ceremony_identifier)


def test_showing_one_enclave_a_different_round_one_view_is_detected() -> None:
    enclaves = build_enclaves()
    other_ceremony = build_enclaves().gateways()
    substitute = other_ceremony[3].begin_ceremony(CeremonyRequest(b"x" * 16, 2, 3))
    gateways = enclaves.gateways()
    gateways[2] = EquivocatingGateway(gateways[2], substitute)

    with pytest.raises(KeyGenerationFailedError):
        run(gateways)

    assert enclaves.holders_of_key_shares() == []


class DisagreeingGateway(TamperingGateway):
    def produce_round2(self, request: Round2Request) -> dict[int, SealedRound2Package]:
        return self._inner.produce_round2(request)

    def finish_ceremony(self, request: FinishRequest) -> PublicKeyGeneration:
        genuine = self._inner.finish_ceremony(request)
        return replace(genuine, group_public_key=GENERATOR)


def test_enclaves_that_derive_different_group_keys_abort_before_any_is_stored() -> None:
    enclaves = build_enclaves()
    gateways = enclaves.gateways()
    gateways[1] = DisagreeingGateway(gateways[1])

    with pytest.raises(KeyGenerationFailedError):
        run(gateways)

    assert enclaves.holders_of_key_shares() == []


class UnreachableKeyGenerationGateway:
    def begin_ceremony(self, request: CeremonyRequest) -> Round1Package:
        raise EnclaveUnavailableError("connection refused")

    def produce_round2(self, request: Round2Request) -> dict[int, SealedRound2Package]:
        raise EnclaveUnavailableError("connection refused")

    def finish_ceremony(self, request: FinishRequest) -> PublicKeyGeneration:
        raise EnclaveUnavailableError("connection refused")

    def confirm_ceremony(self, request: ConfirmRequest) -> None:
        raise EnclaveUnavailableError("connection refused")

    def abandon_ceremony(self, ceremony_identifier: bytes) -> None:
        raise EnclaveUnavailableError("connection refused")


def test_an_unreachable_enclave_aborts_the_ceremony() -> None:
    enclaves = build_enclaves()
    gateways = enclaves.gateways()
    gateways[2] = UnreachableKeyGenerationGateway()

    with pytest.raises(KeyGenerationFailedError) as failure:
        run(gateways)

    assert failure.value.reporter == 2
    assert enclaves.holders_of_key_shares() == []


def test_an_enclave_that_already_holds_a_key_refuses_a_new_ceremony() -> None:
    enclaves = build_enclaves()
    run(enclaves.gateways())

    with pytest.raises(KeyGenerationFailedError):
        run(enclaves.gateways())


def test_the_participant_count_must_match_the_roster() -> None:
    enclaves = build_enclaves()
    gateways = enclaves.gateways()
    del gateways[3]

    with pytest.raises(KeyGenerationFailedError):
        run(gateways)


def test_a_threshold_below_two_is_refused() -> None:
    enclaves = build_enclaves()

    with pytest.raises(KeyGenerationFailedError):
        RunKeyGeneration(KeyGenerationSetup(1, enclaves.gateways())).execute()


def test_confirming_a_different_group_key_is_refused_and_stores_nothing() -> None:
    enclaves = build_enclaves()
    ceremony = b"c" * 16
    gateways = enclaves.gateways()
    round1 = {
        identifier: gateway.begin_ceremony(CeremonyRequest(ceremony, 2, 3))
        for identifier, gateway in gateways.items()
    }
    sealed = {
        identifier: gateway.produce_round2(
            Round2Request(
                ceremony,
                {
                    other: package
                    for other, package in round1.items()
                    if other != identifier
                },
            )
        )
        for identifier, gateway in gateways.items()
    }
    finished = gateways[1].finish_ceremony(
        FinishRequest(
            ceremony,
            {other: package for other, package in round1.items() if other != 1},
            {sender: sealed[sender][1] for sender in (2, 3)},
        )
    )
    assert finished.group_public_key != GENERATOR

    with pytest.raises(EnclaveRefusedError):
        gateways[1].confirm_ceremony(ConfirmRequest(ceremony, GENERATOR))
    assert enclaves.holders_of_key_shares() == []


def test_requests_for_an_unknown_ceremony_are_refused() -> None:
    gateways = build_enclaves().gateways()

    with pytest.raises(EnclaveRefusedError):
        gateways[1].produce_round2(Round2Request(b"unknown", {}))
    with pytest.raises(EnclaveRefusedError):
        gateways[1].confirm_ceremony(ConfirmRequest(b"unknown", GENERATOR))


def test_signing_is_refused_before_a_key_exists() -> None:
    enclaves = build_enclaves()
    gateway = InProcessEnclaveGateway(enclaves.services[1])

    with pytest.raises(EnclaveRefusedError):
        gateway.request_commitments(CommitmentRequest(b"session"))


def others_of(
    packages: dict[int, Round1Package], identifier: int
) -> dict[int, Round1Package]:
    return {
        other: package for other, package in packages.items() if other != identifier
    }


def test_inconsistent_round_one_views_cannot_finish_even_if_the_coordinator_hides_it() -> (
    None
):
    enclaves = build_enclaves()
    twin = build_service(3, enclaves, InMemorySealedStorage())
    ceremony = b"d" * 16
    request = CeremonyRequest(ceremony, 2, 3)
    first, second = enclaves.services[1], enclaves.services[2]
    genuine, forked = enclaves.services[3], twin
    packages = {
        1: first.begin_ceremony(request),
        2: second.begin_ceremony(request),
        3: genuine.begin_ceremony(request),
    }
    forked_package = forked.begin_ceremony(request)
    first_view = others_of(packages, 1)
    second_view = {**others_of(packages, 2), 3: forked_package}
    first.produce_round2(Round2Request(ceremony, first_view))
    sealed_by_second = second.produce_round2(Round2Request(ceremony, second_view))
    sealed_by_genuine = genuine.produce_round2(
        Round2Request(ceremony, others_of(packages, 3))
    )

    with pytest.raises(EnclaveRefusedError):
        first.finish_ceremony(
            FinishRequest(
                ceremony,
                first_view,
                {2: sealed_by_second[1], 3: sealed_by_genuine[1]},
            )
        )
