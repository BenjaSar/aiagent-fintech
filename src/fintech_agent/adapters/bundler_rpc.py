import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 10.0


class BundlerRpcError(Exception):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RpcCall:
    method: str
    parameters: tuple[Any, ...] = ()


@dataclass(frozen=True)
class UserOperationSubmission:
    user_operation: dict[str, Any]
    entry_point: str


class JsonRpcClient:
    def __init__(
        self, url: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds

    def chain_id(self) -> int:
        return int(str(self.call(RpcCall("eth_chainId"))), 16)

    def call(self, call: RpcCall) -> Any:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": call.method,
                "params": list(call.parameters),
            }
        ).encode()
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                reply = json.loads(response.read())
        except (OSError, ValueError) as failure:
            raise BundlerRpcError(f"the bundler is unreachable: {failure}") from failure
        return self._result_of(reply)

    def _result_of(self, reply: Any) -> Any:
        if not isinstance(reply, dict):
            raise BundlerRpcError("the bundler returned a malformed reply")
        error = reply.get("error")
        if error is not None:
            raise BundlerRpcError(str(error.get("message", error)), error.get("code"))
        if "result" not in reply:
            raise BundlerRpcError("the bundler reply has neither result nor error")
        return reply["result"]


class BundlerRpcClient(JsonRpcClient):
    def supported_entry_points(self) -> list[str]:
        result = self.call(RpcCall("eth_supportedEntryPoints"))
        if not isinstance(result, list):
            raise BundlerRpcError("the bundler returned an unexpected entry point list")
        return [str(address) for address in result]

    def estimate_gas(self, submission: UserOperationSubmission) -> dict[str, Any]:
        result = self.call(
            RpcCall(
                "eth_estimateUserOperationGas",
                (submission.user_operation, submission.entry_point),
            )
        )
        return dict(result)

    def send_user_operation(self, submission: UserOperationSubmission) -> str:
        return str(
            self.call(
                RpcCall(
                    "eth_sendUserOperation",
                    (submission.user_operation, submission.entry_point),
                )
            )
        )

    def user_operation_receipt(self, operation_hash: str) -> dict[str, Any] | None:
        result = self.call(RpcCall("eth_getUserOperationReceipt", (operation_hash,)))
        return None if result is None else dict(result)
