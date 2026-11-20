from collections.abc import Mapping
from typing import Any

from fintech_agent.application.key_generation_ports import (
    PublicKeyGeneration,
    SealedRound2Package,
)
from fintech_agent.application.ports import AttestationEvidence
from fintech_agent.domain.frost.aggregation import (
    ParticipantCommitment,
    SchnorrSignature,
    SigningPackage,
)
from fintech_agent.domain.frost.dkg import Round1Package
from fintech_agent.domain.frost.group import (
    Point,
    deserialize_scalar,
    serialize_scalar,
)
from fintech_agent.domain.frost.signing import NonceCommitments
from fintech_agent.domain.session_policy import SessionPolicy
from fintech_agent.domain.session_policy_document import (
    policy_from_document,
    policy_to_document,
)
from fintech_agent.domain.user_operation import (
    GasLimits,
    GasPrices,
    PaymasterFields,
    UserOperation,
)

Payload = Mapping[str, Any]


def encode_commitments(commitments: NonceCommitments) -> dict[str, str]:
    return {
        "hiding": commitments.hiding_nonce_commitment.serialize().hex(),
        "binding": commitments.binding_nonce_commitment.serialize().hex(),
    }


def decode_commitments(payload: Payload) -> NonceCommitments:
    return NonceCommitments(
        Point.deserialize(bytes.fromhex(payload["hiding"])),
        Point.deserialize(bytes.fromhex(payload["binding"])),
    )


def encode_package(package: SigningPackage) -> dict[str, Any]:
    return {
        "message": package.message.hex(),
        "group_public_key": package.group_public_key.serialize().hex(),
        "commitments": [
            {"identifier": commitment.identifier, **_encode_pair(commitment)}
            for commitment in package.commitments
        ],
    }


def decode_package(payload: Payload) -> SigningPackage:
    return SigningPackage(
        tuple(_decode_participant(entry) for entry in payload["commitments"]),
        bytes.fromhex(payload["message"]),
        Point.deserialize(bytes.fromhex(payload["group_public_key"])),
    )


def encode_operation(operation: UserOperation) -> dict[str, Any]:
    paymaster = operation.paymaster
    return {
        "sender": operation.sender,
        "nonce": str(operation.nonce),
        "init_code": operation.init_code.hex(),
        "call_data": operation.call_data.hex(),
        "gas_limits": [
            operation.gas_limits.call_gas_limit,
            operation.gas_limits.verification_gas_limit,
            operation.gas_limits.pre_verification_gas,
        ],
        "gas_prices": [
            operation.gas_prices.max_fee_per_gas,
            operation.gas_prices.max_priority_fee_per_gas,
        ],
        "paymaster": None
        if paymaster is None
        else {
            "address": paymaster.address,
            "verification_gas_limit": paymaster.verification_gas_limit,
            "post_operation_gas_limit": paymaster.post_operation_gas_limit,
            "data": paymaster.data.hex(),
        },
    }


def decode_operation(payload: Payload) -> UserOperation:
    paymaster = payload["paymaster"]
    call_gas, verification_gas, pre_verification_gas = payload["gas_limits"]
    max_fee, max_priority_fee = payload["gas_prices"]
    return UserOperation(
        sender=str(payload["sender"]),
        nonce=int(payload["nonce"]),
        init_code=bytes.fromhex(payload["init_code"]),
        call_data=bytes.fromhex(payload["call_data"]),
        gas_limits=GasLimits(
            int(call_gas), int(verification_gas), int(pre_verification_gas)
        ),
        gas_prices=GasPrices(int(max_fee), int(max_priority_fee)),
        paymaster=None
        if paymaster is None
        else PaymasterFields(
            str(paymaster["address"]),
            int(paymaster["verification_gas_limit"]),
            int(paymaster["post_operation_gas_limit"]),
            bytes.fromhex(paymaster["data"]),
        ),
    )


def encode_policy(policy: SessionPolicy) -> dict[str, Any]:
    return policy_to_document(policy)


def decode_policy(payload: Payload) -> SessionPolicy:
    return policy_from_document(payload)


def encode_share(share: int) -> dict[str, str]:
    return {"share": serialize_scalar(share).hex()}


def decode_share(payload: Payload) -> int:
    return deserialize_scalar(bytes.fromhex(payload["share"]))


def encode_evidence(evidence: AttestationEvidence) -> dict[str, Any]:
    return {
        "identifier": evidence.identifier,
        "challenge": evidence.challenge.hex(),
        "document": evidence.document.hex(),
    }


def decode_evidence(payload: Payload) -> AttestationEvidence:
    return AttestationEvidence(
        int(payload["identifier"]),
        bytes.fromhex(payload["challenge"]),
        bytes.fromhex(payload["document"]),
    )


def encode_round1_package(package: Round1Package) -> dict[str, Any]:
    return {
        "identifier": package.identifier,
        "commitment": [point.serialize().hex() for point in package.commitment],
        "proof": package.proof_of_knowledge.serialize().hex(),
    }


def decode_round1_package(payload: Payload) -> Round1Package:
    return Round1Package(
        int(payload["identifier"]),
        tuple(Point.deserialize(bytes.fromhex(item)) for item in payload["commitment"]),
        SchnorrSignature.deserialize(bytes.fromhex(payload["proof"])),
    )


def encode_round1_map(packages: Mapping[int, Round1Package]) -> dict[str, Any]:
    return {
        str(identifier): encode_round1_package(package)
        for identifier, package in packages.items()
    }


def decode_round1_map(payload: Payload) -> dict[int, Round1Package]:
    return {
        int(identifier): decode_round1_package(entry)
        for identifier, entry in payload.items()
    }


def encode_sealed_package(package: SealedRound2Package) -> dict[str, Any]:
    return {
        "sender": package.sender,
        "receiver": package.receiver,
        "sealed": package.sealed.hex(),
    }


def decode_sealed_package(payload: Payload) -> SealedRound2Package:
    return SealedRound2Package(
        int(payload["sender"]),
        int(payload["receiver"]),
        bytes.fromhex(payload["sealed"]),
    )


def encode_sealed_map(packages: Mapping[int, SealedRound2Package]) -> dict[str, Any]:
    return {
        str(identifier): encode_sealed_package(package)
        for identifier, package in packages.items()
    }


def decode_sealed_map(payload: Payload) -> dict[int, SealedRound2Package]:
    return {
        int(identifier): decode_sealed_package(entry)
        for identifier, entry in payload.items()
    }


def encode_public_generation(public: PublicKeyGeneration) -> dict[str, Any]:
    return {
        "identifier": public.identifier,
        "group_public_key": public.group_public_key.serialize().hex(),
        "verifying_shares": {
            str(identifier): point.serialize().hex()
            for identifier, point in public.verifying_shares.items()
        },
    }


def decode_public_generation(payload: Payload) -> PublicKeyGeneration:
    return PublicKeyGeneration(
        int(payload["identifier"]),
        Point.deserialize(bytes.fromhex(payload["group_public_key"])),
        {
            int(identifier): Point.deserialize(bytes.fromhex(encoded))
            for identifier, encoded in payload["verifying_shares"].items()
        },
    )


def _encode_pair(commitment: ParticipantCommitment) -> dict[str, str]:
    return {
        "hiding": commitment.hiding_nonce_commitment.serialize().hex(),
        "binding": commitment.binding_nonce_commitment.serialize().hex(),
    }


def _decode_participant(entry: Payload) -> ParticipantCommitment:
    commitments = decode_commitments(entry)
    return ParticipantCommitment(
        int(entry["identifier"]),
        commitments.hiding_nonce_commitment,
        commitments.binding_nonce_commitment,
    )
