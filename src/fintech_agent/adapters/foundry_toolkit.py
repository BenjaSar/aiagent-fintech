import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FoundryError(Exception):
    pass


@dataclass(frozen=True)
class FoundryConfiguration:
    binaries: Path
    project_root: Path
    rpc_url: str


@dataclass(frozen=True)
class FunctionCall:
    signature: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractCall:
    target: str
    function: FunctionCall


@dataclass(frozen=True)
class ContractTransaction:
    call: ContractCall
    private_key: str
    value: str = "0"


@dataclass(frozen=True)
class Deployment:
    contract: str
    private_key: str
    constructor_arguments: tuple[str, ...] = ()


class FoundryToolkit:
    def __init__(self, configuration: FoundryConfiguration) -> None:
        self._configuration = configuration

    def call(self, call: ContractCall) -> str:
        return self._cast(
            [
                "call",
                call.target,
                call.function.signature,
                *call.function.arguments,
                "--rpc-url",
                self._configuration.rpc_url,
            ]
        )

    def send(self, transaction: ContractTransaction) -> str:
        function = transaction.call.function
        return self._cast(
            [
                "send",
                transaction.call.target,
                function.signature,
                *function.arguments,
                "--value",
                transaction.value,
                "--private-key",
                transaction.private_key,
                "--rpc-url",
                self._configuration.rpc_url,
            ]
        )

    def encode_call(self, function: FunctionCall) -> bytes:
        encoded = self._cast(["calldata", function.signature, *function.arguments])
        return bytes.fromhex(encoded.removeprefix("0x"))

    def encode_arguments(self, function: FunctionCall) -> bytes:
        encoded = self._cast(["abi-encode", function.signature, *function.arguments])
        return bytes.fromhex(encoded.removeprefix("0x"))

    def error_selector(self, error_signature: str) -> str:
        return self._cast(["sig", error_signature]).removeprefix("0x").lower()

    def sign_hash(self, private_key: str, digest: bytes) -> bytes:
        signature = self._cast(
            [
                "wallet",
                "sign",
                "--no-hash",
                "--private-key",
                private_key,
                "0x" + digest.hex(),
            ]
        )
        return bytes.fromhex(signature.removeprefix("0x"))

    def address_of(self, private_key: str) -> str:
        return self._cast(["wallet", "address", "--private-key", private_key])

    def deploy(self, deployment: Deployment) -> str:
        output = self._run(
            [
                str(self._configuration.binaries / "forge.exe"),
                "create",
                deployment.contract,
                "--rpc-url",
                self._configuration.rpc_url,
                "--private-key",
                deployment.private_key,
                "--broadcast",
                "--json",
                *self._constructor_flags(deployment),
            ],
            self._configuration.project_root,
        )
        try:
            return str(json.loads(output)["deployedTo"])
        except (ValueError, KeyError) as failure:
            raise FoundryError(f"unexpected forge output: {output[:200]}") from failure

    def _constructor_flags(self, deployment: Deployment) -> list[str]:
        if not deployment.constructor_arguments:
            return []
        return ["--constructor-args", *deployment.constructor_arguments]

    def _cast(self, arguments: list[str]) -> str:
        return self._run(
            [str(self._configuration.binaries / "cast.exe"), *arguments],
            self._configuration.project_root,
        )

    def _run(self, command: list[str], working_directory: Path) -> str:
        completed = subprocess.run(
            command,
            cwd=working_directory,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise FoundryError(completed.stderr.strip() or completed.stdout.strip())
        return completed.stdout.strip()
