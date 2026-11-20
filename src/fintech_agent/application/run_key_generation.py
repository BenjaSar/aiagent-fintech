from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fintech_agent.domain.frost.dkg import Round1Package

from .key_generation_ports import (
    CeremonyRequest,
    ConfirmRequest,
    FinishRequest,
    KeyGenerationGateway,
    PublicKeyGeneration,
    Round2Request,
    SealedRound2Package,
)
from .ports import EnclaveRefusedError, EnclaveUnavailableError
from .sign_user_operation import GroupConfiguration, fresh_session_identifier

ENCLAVE_FAILURES = (EnclaveUnavailableError, EnclaveRefusedError)


class KeyGenerationFailedError(Exception):
    def __init__(self, message: str, reporter: int | None = None) -> None:
        super().__init__(message)
        self.reporter = reporter


@dataclass(frozen=True)
class KeyGenerationSetup:
    threshold: int
    gateways: Mapping[int, KeyGenerationGateway]
    ceremony_identifier_source: Callable[[], bytes] = fresh_session_identifier


class RunKeyGeneration:
    def __init__(self, setup: KeyGenerationSetup) -> None:
        self._setup = setup

    def execute(self) -> GroupConfiguration:
        ceremony = self._setup.ceremony_identifier_source()
        try:
            return self._run(ceremony)
        except KeyGenerationFailedError:
            self._abandon(ceremony)
            raise

    def _run(self, ceremony: bytes) -> GroupConfiguration:
        gateways = self._setup.gateways
        request = CeremonyRequest(ceremony, self._setup.threshold, len(gateways))
        round1 = self._each(lambda identifier: self._begin(identifier, request))
        sealed = self._each(
            lambda identifier: self._round2(
                identifier, Round2Request(ceremony, _others(round1, identifier))
            )
        )
        outcomes = self._each(
            lambda identifier: self._finish(
                identifier,
                FinishRequest(
                    ceremony,
                    _others(round1, identifier),
                    {
                        sender: packages[identifier]
                        for sender, packages in sealed.items()
                        if sender != identifier
                    },
                ),
            )
        )
        configuration = self._agreed(outcomes)
        self._each(
            lambda identifier: self._confirm(
                identifier,
                ConfirmRequest(ceremony, configuration.group_public_key),
            )
        )
        return configuration

    def _each[Result](self, action: Callable[[int], Result]) -> dict[int, Result]:
        results: dict[int, Result] = {}
        for identifier in sorted(self._setup.gateways):
            try:
                results[identifier] = action(identifier)
            except ENCLAVE_FAILURES as failure:
                raise KeyGenerationFailedError(
                    f"enclave {identifier}: {failure}", identifier
                ) from failure
        return results

    def _begin(self, identifier: int, request: CeremonyRequest) -> Round1Package:
        return self._setup.gateways[identifier].begin_ceremony(request)

    def _round2(
        self, identifier: int, request: Round2Request
    ) -> dict[int, SealedRound2Package]:
        return self._setup.gateways[identifier].produce_round2(request)

    def _confirm(self, identifier: int, request: ConfirmRequest) -> None:
        self._setup.gateways[identifier].confirm_ceremony(request)

    def _finish(self, identifier: int, request: FinishRequest) -> PublicKeyGeneration:
        return self._setup.gateways[identifier].finish_ceremony(request)

    def _agreed(
        self, outcomes: Mapping[int, PublicKeyGeneration]
    ) -> GroupConfiguration:
        reference = outcomes[min(outcomes)]
        for identifier, outcome in outcomes.items():
            if outcome.group_public_key != reference.group_public_key or dict(
                outcome.verifying_shares
            ) != dict(reference.verifying_shares):
                raise KeyGenerationFailedError(
                    f"enclave {identifier} derived a different group key", identifier
                )
        return GroupConfiguration(
            reference.group_public_key,
            self._setup.threshold,
            dict(reference.verifying_shares),
        )

    def _abandon(self, ceremony: bytes) -> None:
        for gateway in self._setup.gateways.values():
            try:
                gateway.abandon_ceremony(ceremony)
            except ENCLAVE_FAILURES:
                continue


def _others[Result](
    packages: Mapping[int, Result], identifier: int
) -> dict[int, Result]:
    return {
        sender: package for sender, package in packages.items() if sender != identifier
    }
