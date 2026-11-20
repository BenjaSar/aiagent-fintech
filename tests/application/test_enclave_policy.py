import pytest

from fintech_agent.adapters.in_memory import (
    InMemorySealedStorage,
    InProcessEnclaveGateway,
    SimulatedAttestationProvider,
)
from fintech_agent.application.enclave_node import EnclaveDependencies, EnclaveNode
from fintech_agent.application.policy_authorizer import (
    AuthorizerParts,
    PolicyAuthorizer,
)
from fintech_agent.application.ports import EnclaveGateway
from fintech_agent.application.sign_user_operation import (
    SigningFailedError,
    SigningRequest,
    SignUserOperation,
)
from fintech_agent.domain.user_operation import UserOperation
from fintech_agent.domain.user_operation_hash import user_operation_hash
from tests.application.fixtures import (
    THRESHOLD,
    build_setup,
    group_public_key,
    record_of,
    signature_verifies,
)
from tests.policy_fixtures import (
    ETHER,
    NOW,
    STRANGER,
    TARGET,
    build_operation,
    build_policy,
    single,
)

IDENTIFIERS = (1, 2, 3)


def build_nodes(issue_policy: bool = True) -> dict[int, EnclaveNode]:
    nodes: dict[int, EnclaveNode] = {}
    for identifier in IDENTIFIERS:
        authorizer = PolicyAuthorizer(
            AuthorizerParts(InMemorySealedStorage(), lambda: NOW)
        )
        if issue_policy:
            authorizer.issue(build_policy())
        nodes[identifier] = EnclaveNode(
            record_of(identifier),
            EnclaveDependencies(SimulatedAttestationProvider(), authorizer),
        )
    return nodes


def sign(nodes: dict[int, EnclaveNode], request: SigningRequest) -> bytes:
    gateways: dict[int, EnclaveGateway] = {
        identifier: InProcessEnclaveGateway(node) for identifier, node in nodes.items()
    }
    return SignUserOperation(build_setup(gateways)).execute(request).serialize()


def message_of(operation: UserOperation) -> bytes:
    return user_operation_hash(operation, build_policy().domain)


def refusal_reason(nodes: dict[int, EnclaveNode], request: SigningRequest) -> str:
    with pytest.raises(SigningFailedError) as failure:
        sign(nodes, request)
    assert failure.value.culprit in IDENTIFIERS
    return str(failure.value.__cause__)


def test_a_compliant_operation_gets_a_valid_threshold_signature() -> None:
    operation = build_operation(single(TARGET, ETHER // 10))
    message = message_of(operation)
    nodes = build_nodes()
    gateways: dict[int, EnclaveGateway] = {
        identifier: InProcessEnclaveGateway(node) for identifier, node in nodes.items()
    }

    signature = SignUserOperation(build_setup(gateways)).execute(
        SigningRequest(message, operation)
    )

    assert signature_verifies(signature, message, group_public_key())


def test_a_hash_without_its_operation_is_not_signed() -> None:
    operation = build_operation(single(TARGET))

    reason = refusal_reason(build_nodes(), SigningRequest(message_of(operation)))

    assert "no user operation" in reason


def test_an_operation_that_breaks_the_policy_is_not_signed() -> None:
    operation = build_operation(single(STRANGER))

    reason = refusal_reason(
        build_nodes(), SigningRequest(message_of(operation), operation)
    )

    assert "not allowed" in reason


def test_a_hash_that_does_not_belong_to_the_operation_is_not_signed() -> None:
    honest = build_operation(single(TARGET))
    harmful = build_operation(single(STRANGER))

    reason = refusal_reason(build_nodes(), SigningRequest(message_of(honest), harmful))

    assert "does not match" in reason


def test_an_enclave_without_a_policy_signs_nothing() -> None:
    operation = build_operation(single(TARGET))

    reason = refusal_reason(
        build_nodes(issue_policy=False),
        SigningRequest(message_of(operation), operation),
    )

    assert "no session policy" in reason


def test_a_refusal_leaves_no_unused_nonces_behind() -> None:
    nodes = build_nodes()
    operation = build_operation(single(STRANGER))

    refusal_reason(nodes, SigningRequest(message_of(operation), operation))

    assert all(node.pending_session_count == 0 for node in nodes.values())


def test_every_enclave_that_signs_counts_the_spend() -> None:
    nodes = build_nodes()
    first = build_operation(single(TARGET, ETHER * 6 // 10))
    second = build_operation(single(TARGET, ETHER * 5 // 10))
    sign(nodes, SigningRequest(message_of(first), first))

    reason = refusal_reason(nodes, SigningRequest(message_of(second), second))

    assert "rolling cap" in reason
    assert THRESHOLD == 2
