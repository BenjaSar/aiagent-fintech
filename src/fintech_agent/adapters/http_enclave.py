import hmac
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from fintech_agent.application.enclave_service import EnclaveService
from fintech_agent.application.key_generation_ports import (
    CeremonyRequest,
    ConfirmRequest,
    FinishRequest,
    PublicKeyGeneration,
    Round2Request,
    SealedRound2Package,
)
from fintech_agent.application.ports import (
    AttestationEvidence,
    CommitmentRequest,
    EnclaveRefusedError,
    EnclaveUnavailableError,
    ShareRequest,
)
from fintech_agent.domain.frost.dkg import Round1Package
from fintech_agent.domain.frost.group import Point
from fintech_agent.domain.frost.signing import NonceCommitments
from fintech_agent.domain.session_policy import SessionPolicy

from .wire import (
    Payload,
    decode_commitments,
    decode_evidence,
    decode_operation,
    decode_package,
    decode_policy,
    decode_public_generation,
    decode_round1_map,
    decode_round1_package,
    decode_sealed_map,
    decode_share,
    encode_commitments,
    encode_evidence,
    encode_operation,
    encode_package,
    encode_policy,
    encode_public_generation,
    encode_round1_map,
    encode_round1_package,
    encode_sealed_map,
    encode_share,
)

LOOPBACK_ADDRESS = "127.0.0.1"
MAXIMUM_BODY_LENGTH = 65536
DEFAULT_TIMEOUT_SECONDS = 5.0
STATUS_OK = 200
STATUS_BAD_REQUEST = 400
STATUS_UNAUTHORIZED = 401
STATUS_NOT_FOUND = 404
STATUS_REFUSED = 409
STATUS_TOO_LARGE = 413

Route = Callable[[EnclaveService, Payload], dict[str, Any]]


def _commitments_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    session = bytes.fromhex(payload["session"])
    return encode_commitments(service.commit(session))


def _share_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    session = bytes.fromhex(payload["session"])
    package = decode_package(payload["package"])
    operation = payload.get("operation")
    decoded = None if operation is None else decode_operation(operation)
    return encode_share(service.sign(session, package, decoded))


def _policy_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    service.issue_policy(decode_policy(payload["policy"]))
    return {}


def _release_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    service.release(bytes.fromhex(payload["session"]))
    return {}


def _attest_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    return encode_evidence(service.attest(bytes.fromhex(payload["challenge"])))


def _begin_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    request = CeremonyRequest(
        bytes.fromhex(payload["ceremony"]),
        int(payload["threshold"]),
        int(payload["participant_count"]),
    )
    return encode_round1_package(service.begin_ceremony(request))


def _round2_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    request = Round2Request(
        bytes.fromhex(payload["ceremony"]), decode_round1_map(payload["round1"])
    )
    return encode_sealed_map(service.produce_round2(request))


def _finish_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    request = FinishRequest(
        bytes.fromhex(payload["ceremony"]),
        decode_round1_map(payload["round1"]),
        decode_sealed_map(payload["sealed"]),
    )
    return encode_public_generation(service.finish_ceremony(request))


def _confirm_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    service.confirm_ceremony(
        ConfirmRequest(
            bytes.fromhex(payload["ceremony"]),
            Point.deserialize(bytes.fromhex(payload["group_public_key"])),
        )
    )
    return {}


def _abandon_route(service: EnclaveService, payload: Payload) -> dict[str, Any]:
    service.abandon_ceremony(bytes.fromhex(payload["ceremony"]))
    return {}


ROUTES: dict[str, Route] = {
    "/commitments": _commitments_route,
    "/signature-share": _share_route,
    "/policy": _policy_route,
    "/release": _release_route,
    "/attest": _attest_route,
    "/ceremony/begin": _begin_route,
    "/ceremony/round2": _round2_route,
    "/ceremony/finish": _finish_route,
    "/ceremony/confirm": _confirm_route,
    "/ceremony/abandon": _abandon_route,
}


@dataclass(frozen=True)
class ServedEnclave:
    service: EnclaveService
    token: bytes


def build_server(served: ServedEnclave, port: int) -> HTTPServer:
    class Handler(_EnclaveHandler):
        enclave = served

    return HTTPServer((LOOPBACK_ADDRESS, port), Handler)


class _EnclaveHandler(BaseHTTPRequestHandler):
    enclave: ServedEnclave

    def do_POST(self) -> None:
        length = self._content_length()
        body = self.rfile.read(length) if length <= MAXIMUM_BODY_LENGTH else b""
        route = ROUTES.get(self.path)
        if not self._is_authorized():
            self._reply(STATUS_UNAUTHORIZED, {"error": "unauthorized"})
        elif route is None:
            self._reply(STATUS_NOT_FOUND, {"error": "unknown route"})
        elif length > MAXIMUM_BODY_LENGTH:
            self._reply(STATUS_TOO_LARGE, {"error": "body too large"})
        else:
            self._run(route, body)

    def _content_length(self) -> int:
        try:
            return max(0, int(self.headers.get("Content-Length", "0")))
        except ValueError:
            return 0

    def log_message(self, format: str, *arguments: Any) -> None:
        return None

    def _run(self, route: Route, body: bytes) -> None:
        try:
            payload = json.loads(body or b"{}")
            self._reply(STATUS_OK, route(self.enclave.service, payload))
        except EnclaveRefusedError as refusal:
            self._reply(STATUS_REFUSED, {"error": str(refusal)})
        except (ValueError, KeyError, TypeError) as malformed:
            self._reply(STATUS_BAD_REQUEST, {"error": str(malformed)})

    def _is_authorized(self) -> bool:
        presented = self.headers.get("Authorization", "").encode()
        expected = b"Bearer " + self.enclave.token
        return hmac.compare_digest(presented, expected)

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@dataclass(frozen=True)
class HttpEndpoint:
    base_url: str
    token: bytes
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


class _HttpClient:
    def __init__(self, endpoint: HttpEndpoint) -> None:
        self._endpoint = endpoint

    def _decoded[Decoded](
        self, decoder: Callable[[Payload], Decoded], reply: Payload
    ) -> Decoded:
        try:
            return decoder(reply)
        except (ValueError, KeyError, TypeError) as malformed:
            raise EnclaveUnavailableError("the enclave sent a malformed reply") from (
                malformed
            )

    def _post(self, path: str, payload: Payload) -> dict[str, Any]:
        request = urllib.request.Request(
            self._endpoint.base_url + path,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self._endpoint.token.decode(),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._endpoint.timeout_seconds
            ) as response:
                return dict(json.loads(response.read()))
        except urllib.error.HTTPError as refusal:
            raise EnclaveRefusedError(self._error_text(refusal)) from refusal
        except (OSError, ValueError) as failure:
            raise EnclaveUnavailableError(str(failure)) from failure

    def _error_text(self, refusal: urllib.error.HTTPError) -> str:
        try:
            return str(json.loads(refusal.read()).get("error", refusal.code))
        except ValueError:
            return str(refusal.code)


class HttpEnclaveGateway(_HttpClient):
    def request_commitments(self, request: CommitmentRequest) -> NonceCommitments:
        reply = self._post(
            "/commitments", {"session": request.session_identifier.hex()}
        )
        return self._decoded(decode_commitments, reply)

    def request_signature_share(self, request: ShareRequest) -> int:
        reply = self._post(
            "/signature-share",
            {
                "session": request.session_identifier.hex(),
                "package": encode_package(request.signing_package),
                "operation": None
                if request.operation is None
                else encode_operation(request.operation),
            },
        )
        return self._decoded(decode_share, reply)

    def release_session(self, session_identifier: bytes) -> None:
        self._post("/release", {"session": session_identifier.hex()})

    def attest(self, challenge: bytes) -> AttestationEvidence:
        reply = self._post("/attest", {"challenge": challenge.hex()})
        return self._decoded(decode_evidence, reply)


class HttpPolicyGateway(_HttpClient):
    def issue_policy(self, policy: SessionPolicy) -> None:
        self._post("/policy", {"policy": encode_policy(policy)})


class HttpKeyGenerationGateway(_HttpClient):
    def begin_ceremony(self, request: CeremonyRequest) -> Round1Package:
        reply = self._post(
            "/ceremony/begin",
            {
                "ceremony": request.ceremony_identifier.hex(),
                "threshold": request.threshold,
                "participant_count": request.participant_count,
            },
        )
        return self._decoded(decode_round1_package, reply)

    def produce_round2(self, request: Round2Request) -> dict[int, SealedRound2Package]:
        reply = self._post(
            "/ceremony/round2",
            {
                "ceremony": request.ceremony_identifier.hex(),
                "round1": encode_round1_map(request.round1_packages),
            },
        )
        return self._decoded(decode_sealed_map, reply)

    def finish_ceremony(self, request: FinishRequest) -> PublicKeyGeneration:
        reply = self._post(
            "/ceremony/finish",
            {
                "ceremony": request.ceremony_identifier.hex(),
                "round1": encode_round1_map(request.round1_packages),
                "sealed": encode_sealed_map(request.sealed_packages),
            },
        )
        return self._decoded(decode_public_generation, reply)

    def confirm_ceremony(self, request: ConfirmRequest) -> None:
        self._post(
            "/ceremony/confirm",
            {
                "ceremony": request.ceremony_identifier.hex(),
                "group_public_key": request.group_public_key.serialize().hex(),
            },
        )

    def abandon_ceremony(self, ceremony_identifier: bytes) -> None:
        self._post("/ceremony/abandon", {"ceremony": ceremony_identifier.hex()})
