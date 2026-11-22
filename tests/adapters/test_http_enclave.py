import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from fintech_agent.adapters.http_enclave import (
    LOOPBACK_ADDRESS,
    HttpEnclaveGateway,
    HttpEndpoint,
    HttpPolicyGateway,
    ServedEnclave,
    build_server,
)
from fintech_agent.adapters.in_memory import (
    InMemorySealedStorage,
    SimulatedAttestationProvider,
)
from fintech_agent.adapters.wire import decode_operation, encode_operation
from fintech_agent.application.enclave_node import EnclaveDependencies, EnclaveNode
from fintech_agent.application.enclave_service import EnclaveService, ServiceParts
from fintech_agent.application.policy_authorizer import (
    AuthorizerParts,
    PolicyAuthorizer,
)
from fintech_agent.application.ports import (
    CommitmentRequest,
    EnclaveGateway,
    EnclaveRefusedError,
    EnclaveUnavailableError,
)
from fintech_agent.application.sign_user_operation import (
    QuorumUnavailableError,
    SigningFailedError,
    SigningRequest,
    SignUserOperation,
)
from fintech_agent.domain.user_operation_hash import user_operation_hash
from tests.application.fixtures import (
    USER_OPERATION_HASH,
    build_node,
    build_setup,
    record_of,
    service_for,
    tenanted,
)
from tests.application.test_sign_user_operation import verifies
from tests.policy_fixtures import (
    NOW,
    PAYMASTER,
    TARGET,
    build_operation,
    build_policy,
    single,
    with_paymaster,
)

TOKEN = b"0123456789abcdef0123456789abcdef"
CONNECTION_DROPPED = -1


@contextmanager
def serving(node: EnclaveNode, token: bytes = TOKEN) -> Iterator[str]:
    server = build_server(ServedEnclave(tenanted(service_for(node), token)), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{LOOPBACK_ADDRESS}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def gateway_for(base_url: str, token: bytes = TOKEN) -> HttpEnclaveGateway:
    return HttpEnclaveGateway(HttpEndpoint(base_url, token, timeout_seconds=2.0))


def raw_post(base_url: str, path: str, headers: dict[str, str], body: bytes) -> int:
    request = urllib.request.Request(
        base_url + path, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return int(response.status)
    except urllib.error.HTTPError as failure:
        return failure.code
    except ConnectionError:
        return CONNECTION_DROPPED


def test_a_quorum_signs_over_localhost_http() -> None:
    with serving(build_node(1)) as first, serving(build_node(2)) as second:
        gateways: dict[int, EnclaveGateway] = {
            1: gateway_for(first),
            2: gateway_for(second),
        }

        signature = SignUserOperation(build_setup(gateways)).execute(
            SigningRequest(USER_OPERATION_HASH)
        )

    assert verifies(signature, USER_OPERATION_HASH)


def test_a_stopped_enclave_is_unavailable_and_two_stopped_halt_signing() -> None:
    with serving(build_node(1)) as first:
        with serving(build_node(2)) as second:
            second_url = second
        gateways: dict[int, EnclaveGateway] = {
            1: gateway_for(first),
            2: gateway_for(second_url),
        }

        with pytest.raises(EnclaveUnavailableError):
            gateways[2].request_commitments(CommitmentRequest(b"session"))
        with pytest.raises(QuorumUnavailableError):
            SignUserOperation(build_setup(gateways)).execute(
                SigningRequest(USER_OPERATION_HASH)
            )


def test_a_wrong_token_is_refused_and_no_nonces_are_created() -> None:
    node = build_node(1)
    with serving(node) as url:
        gateway = gateway_for(url, token=b"not-the-token-not-the-token-xxxx")

        with pytest.raises(EnclaveRefusedError):
            gateway.request_commitments(CommitmentRequest(b"session"))

    assert node.pending_session_count == 0


def test_requests_without_authorization_are_rejected() -> None:
    with serving(build_node(1)) as url:
        status = raw_post(url, "/commitments", {}, b'{"session": "00"}')

    assert status == 401


def test_unknown_routes_malformed_bodies_and_oversized_bodies_are_rejected() -> None:
    headers = {"Authorization": "Bearer " + TOKEN.decode()}
    with serving(build_node(1)) as url:
        unknown = raw_post(url, "/sign-anything", headers, b"{}")
        malformed = raw_post(url, "/commitments", headers, b'{"session": "zz"}')
        missing = raw_post(url, "/commitments", headers, b"{}")
        oversized = raw_post(url, "/commitments", headers, b"0" * 70000)

    assert (unknown, malformed, missing) == (404, 400, 400)
    assert oversized in (413, CONNECTION_DROPPED)


def test_a_second_commitment_for_a_session_is_refused_over_http() -> None:
    with serving(build_node(1)) as url:
        gateway = gateway_for(url)
        gateway.request_commitments(CommitmentRequest(b"session"))

        with pytest.raises(EnclaveRefusedError):
            gateway.request_commitments(CommitmentRequest(b"session"))


def test_the_server_binds_to_the_loopback_interface_only() -> None:
    served = ServedEnclave(tenanted(service_for(build_node(1)), TOKEN))
    server = build_server(served, 0)
    try:
        assert server.server_address[0] == LOOPBACK_ADDRESS
    finally:
        server.server_close()


def test_error_replies_are_json_with_a_reason() -> None:
    headers = {"Authorization": "Bearer " + TOKEN.decode()}
    with serving(build_node(1)) as url:
        request = urllib.request.Request(
            url + "/commitments", data=b"{}", headers=headers, method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(request, timeout=2.0)
        body = json.loads(failure.value.read())

    assert "error" in body


def enforcing_node(identifier: int) -> EnclaveService:
    authorizer = PolicyAuthorizer(AuthorizerParts(InMemorySealedStorage(), lambda: NOW))
    dependencies = EnclaveDependencies(SimulatedAttestationProvider(), authorizer)
    node = EnclaveNode(record_of(identifier), dependencies)
    return EnclaveService(
        ServiceParts(identifier, dependencies, signer=node, policy_issuer=authorizer)
    )


@contextmanager
def serving_service(service: EnclaveService) -> Iterator[str]:
    server = build_server(ServedEnclave(tenanted(service, TOKEN)), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{LOOPBACK_ADDRESS}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_a_user_operation_survives_the_wire_with_and_without_a_paymaster() -> None:
    plain = build_operation(single(TARGET, 3))

    assert decode_operation(encode_operation(plain)) == plain
    sponsored = with_paymaster(plain, PAYMASTER)
    assert decode_operation(encode_operation(sponsored)) == sponsored


def test_a_policy_is_issued_over_http_once_and_then_enforced() -> None:
    operation = build_operation(single(TARGET, 3))
    message = user_operation_hash(operation, build_policy().domain)
    with (
        serving_service(enforcing_node(1)) as first,
        serving_service(enforcing_node(2)) as second,
    ):
        for url in (first, second):
            HttpPolicyGateway(HttpEndpoint(url, TOKEN)).issue_policy(build_policy())
        with pytest.raises(EnclaveRefusedError):
            HttpPolicyGateway(HttpEndpoint(first, TOKEN)).issue_policy(build_policy())
        gateways: dict[int, EnclaveGateway] = {
            1: gateway_for(first),
            2: gateway_for(second),
        }

        signature = SignUserOperation(build_setup(gateways)).execute(
            SigningRequest(message, operation)
        )
        with pytest.raises(SigningFailedError):
            SignUserOperation(build_setup(gateways)).execute(SigningRequest(message))

    assert verifies(signature, message)


def test_a_service_without_a_policy_issuer_refuses_policies() -> None:
    with serving(build_node(1)) as url, pytest.raises(EnclaveRefusedError):
        HttpPolicyGateway(HttpEndpoint(url, TOKEN)).issue_policy(build_policy())


def test_a_malformed_policy_is_a_bad_request_not_a_crash() -> None:
    with serving_service(enforcing_node(1)) as url:
        status = raw_post(
            url,
            "/policy",
            {"Authorization": "Bearer " + TOKEN.decode()},
            b'{"policy": {"chain_id": 1}}',
        )

    assert status == 400
