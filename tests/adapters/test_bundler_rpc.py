import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from fintech_agent.adapters.bundler_rpc import (
    BundlerRpcClient,
    BundlerRpcError,
    RpcCall,
)

ENTRY_POINT = "0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108"


@contextmanager
def fake_bundler(respond: Callable[[dict[str, Any]], bytes]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(length))
            body = respond(request)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *arguments: Any) -> None:
            return None

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def answer(result: Any) -> Callable[[dict[str, Any]], bytes]:
    return lambda request: json.dumps(
        {"jsonrpc": "2.0", "id": request["id"], "result": result}
    ).encode()


def test_supported_entry_points_and_chain_id_are_decoded() -> None:
    def respond(request: dict[str, Any]) -> bytes:
        results = {"eth_supportedEntryPoints": [ENTRY_POINT], "eth_chainId": "0x7a69"}
        return answer(results[request["method"]])(request)

    with fake_bundler(respond) as url:
        client = BundlerRpcClient(url)

        assert client.supported_entry_points() == [ENTRY_POINT]
        assert client.chain_id() == 31337


def test_the_request_carries_the_method_and_parameters() -> None:
    seen: list[dict[str, Any]] = []

    def respond(request: dict[str, Any]) -> bytes:
        seen.append(request)
        return answer("ok")(request)

    with fake_bundler(respond) as url:
        BundlerRpcClient(url).call(RpcCall("eth_example", ("a", 1)))

    assert seen[0]["method"] == "eth_example"
    assert seen[0]["params"] == ["a", 1]


def test_an_rpc_error_is_raised_with_its_code() -> None:
    error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32500, "message": "AA23"}}

    with (
        fake_bundler(lambda request: json.dumps(error).encode()) as url,
        pytest.raises(BundlerRpcError) as failure,
    ):
        BundlerRpcClient(url).call(RpcCall("eth_sendUserOperation"))

    assert failure.value.code == -32500
    assert "AA23" in str(failure.value)


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"jsonrpc": "2.0"}'])
def test_malformed_replies_are_rejected(body: bytes) -> None:
    def respond(request: dict[str, Any]) -> bytes:
        return body

    with fake_bundler(respond) as url, pytest.raises(BundlerRpcError):
        BundlerRpcClient(url).call(RpcCall("eth_chainId"))


def test_an_unexpected_entry_point_list_is_rejected() -> None:
    with fake_bundler(answer("0x1")) as url, pytest.raises(BundlerRpcError):
        BundlerRpcClient(url).supported_entry_points()


def test_an_unreachable_bundler_is_reported() -> None:
    with fake_bundler(answer("0x1")) as url:
        stopped_url = url

    with pytest.raises(BundlerRpcError):
        BundlerRpcClient(stopped_url, timeout_seconds=1.0).chain_id()
