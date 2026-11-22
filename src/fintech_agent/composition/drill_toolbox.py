import secrets
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from fintech_agent.adapters.bundler_rpc import (
    BundlerRpcClient,
    JsonRpcClient,
    RpcCall,
    UserOperationSubmission,
)
from fintech_agent.adapters.foundry_toolkit import (
    ContractCall,
    ContractTransaction,
    Deployment,
    FoundryToolkit,
    FunctionCall,
)
from fintech_agent.application.enclave_node import KEY_SHARE_NAME
from fintech_agent.application.enclave_tenancy import PRIMARY_TENANT
from fintech_agent.application.key_share_record import KeyShareRecord
from fintech_agent.application.sign_user_operation import (
    SigningRequest,
    SignUserOperation,
)
from fintech_agent.composition.local_enclaves import (
    EnclaveSet,
    TenantSlot,
    build_signing_setup,
    tenant_storage,
)
from fintech_agent.domain.frost.aggregation import (
    ParticipantCommitment,
    SchnorrSignature,
    SigningPackage,
    aggregate,
    compute_binding_factors,
    compute_group_commitment,
    verify_signature,
)
from fintech_agent.domain.frost.signing import (
    SigningParticipant,
    commit,
    compute_signature_share,
    fresh_nonce_randomness,
)
from fintech_agent.domain.session_call import Execution, encode_batch_payload
from fintech_agent.domain.session_policy import SessionPolicy
from fintech_agent.domain.user_operation import (
    GasLimits,
    GasPrices,
    PaymasterFields,
    UserOperation,
)

ENTRY_POINT = "0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108"
SINGLE_CALL_MODE = "0x" + "00" * 32
BATCH_CALL_MODE = "0x01" + "00" * 31
VALIDATOR_MODULE_TYPE = "1"
EXECUTOR_MODULE_TYPE = "2"
HOOK_MODULE_TYPE = "4"
ZERO_ADDRESS = "0x" + "00" * 20
GUARDIAN_THRESHOLD = 2
RECOVERY_DELAY_SECONDS = 3_600
COMPROMISED_QUORUM = (1, 2)
EXECUTE_SIGNATURE = "execute(bytes32,bytes)"
EXECUTE_AS_SESSION_SIGNATURE = "executeAsSession(bytes32,bytes)"
ACCOUNT_SALT = "0x" + "00" * 31 + "01"
GWEI = 10**9
ETHER = 10**18
DUMMY_SIGNATURE = bytes(64) + b"\x1b"
PLACEHOLDER_LIMITS = GasLimits(1_000_000, 1_000_000, 100_000)
DIRECT_SUBMISSION_LIMITS = GasLimits(300_000, 800_000, 100_000)
INSTALLATION_SUBMISSION_LIMITS = GasLimits(1_500_000, 800_000, 100_000)
CALL_GAS_MARGIN = 50_000
VERIFICATION_GAS_MARGIN = 150_000
PRE_VERIFICATION_GAS_MARGIN = 20_000
PAYMASTER_VERIFICATION_GAS = 200_000
PAYMASTER_POST_OPERATION_GAS = 50_000
PAYMASTER_DATA_PLACEHOLDER_LENGTH = 77
SPONSORSHIP_LIFETIME_SECONDS = 3_600
TIME_BYTES = 6
RECEIPT_ATTEMPTS = 60
RECEIPT_INTERVAL_SECONDS = 0.5
OPERATION_TUPLE = "(address,uint256,bytes,bytes,bytes32,uint256,bytes32,bytes,bytes)"
HASH_SIGNATURE = f"getUserOpHash({OPERATION_TUPLE})(bytes32)"
SPONSOR_HASH_SIGNATURE = f"getHash({OPERATION_TUPLE},uint48,uint48)(bytes32)"
HANDLE_OPS_SIGNATURE = f"handleOps({OPERATION_TUPLE}[],address)"


class DrillFailureError(Exception):
    pass


@dataclass(frozen=True)
class DrillEnvironment:
    foundry: FoundryToolkit
    node: JsonRpcClient
    bundler: BundlerRpcClient
    enclave_root: Path
    halt_enclave: Callable[[Path, int], None]
    halt_bundler: Callable[[], None]
    enclave_sets_directory: Path
    enclave_script: Path


@dataclass(frozen=True)
class DeployedContracts:
    factory: str
    validator: str
    target: str
    paymaster: str
    recovery: str
    policy_hook: str
    registry: str
    unattested: str
    oracle: str


@dataclass(frozen=True)
class Actors:
    deployer_key: str
    owner_key: str
    owner: str
    account: str
    sponsor_key: str
    guardian_keys: tuple[str, ...]
    attester_key: str


@dataclass(frozen=True)
class UnsignedRequest:
    init_code: bytes
    nonce: int
    call_data: bytes


@dataclass(frozen=True)
class ExecutionRequest:
    target: str
    inner: bytes
    value: int = 0


@dataclass(frozen=True)
class ModuleInstall:
    module_type: str
    module: str
    data: bytes


@dataclass(frozen=True)
class KeyedOperation:
    operation: UserOperation
    private_key: str


@dataclass(frozen=True)
class AccountContext:
    account: str
    enclave_root: Path
    policy: SessionPolicy | None
    tenant: str = PRIMARY_TENANT


@dataclass(frozen=True)
class OwnerOperation:
    init_code: bytes
    call_data: bytes


class DrillToolbox:
    def __init__(self, environment: DrillEnvironment) -> None:
        self.environment = environment
        self._contracts: DeployedContracts | None = None
        self._actors: Actors | None = None
        self.session_valid_until = 0
        self.enclave_root = environment.enclave_root
        self.enclave_tenant = PRIMARY_TENANT
        self.active_policy: SessionPolicy | None = None

    @property
    def contracts(self) -> DeployedContracts:
        if self._contracts is None:
            raise DrillFailureError("contracts are not deployed")
        return self._contracts

    @property
    def actors(self) -> Actors:
        if self._actors is None:
            raise DrillFailureError("actors are not prepared")
        return self._actors

    def deploy_everything(self) -> str:
        foundry = self.environment.foundry
        deployer_key = new_key()
        deployer = foundry.address_of(deployer_key)
        self._fund(deployer)
        attester_key = new_key()
        attester = foundry.address_of(attester_key)
        self._fund(attester)
        registry = foundry.deploy(
            Deployment(
                "contracts/FintechModuleRegistry.sol:FintechModuleRegistry",
                deployer_key,
            )
        )
        contracts_by_name = {
            "validator": "contracts/FrostSessionValidator.sol:FrostSessionValidator",
            "target": "contracts/dev/DevelopmentPingTarget.sol:DevelopmentPingTarget",
            "recovery": "contracts/SocialRecoveryModule.sol:SocialRecoveryModule",
            "oracle": "contracts/dev/DevelopmentSanctionsOracle.sol:DevelopmentSanctionsOracle",
            "unattested": "contracts/SocialRecoveryModule.sol:SocialRecoveryModule",
        }
        deployed = {
            name: foundry.deploy(Deployment(path, deployer_key))
            for name, path in contracts_by_name.items()
        }
        deployed["policy_hook"] = foundry.deploy(
            Deployment(
                "contracts/FintechPolicyHook.sol:FintechPolicyHook",
                deployer_key,
                (deployed["oracle"],),
            )
        )
        deployed["factory"] = foundry.deploy(
            Deployment(
                "contracts/FintechAccountFactory.sol:FintechAccountFactory",
                deployer_key,
                (registry, attester),
            )
        )
        sponsor_key = new_key()
        paymaster = foundry.deploy(
            Deployment(
                "contracts/FintechPaymaster.sol:FintechPaymaster",
                deployer_key,
                (deployer, foundry.address_of(sponsor_key)),
            )
        )
        owner_key = new_key()
        owner = foundry.address_of(owner_key)
        account = foundry.call(
            ContractCall(
                deployed["factory"],
                FunctionCall(
                    "getAddress(address,bytes32)(address)", (owner, ACCOUNT_SALT)
                ),
            )
        )
        self._contracts = DeployedContracts(
            deployed["factory"],
            deployed["validator"],
            deployed["target"],
            paymaster,
            deployed["recovery"],
            deployed["policy_hook"],
            registry,
            deployed["unattested"],
            deployed["oracle"],
        )
        guardian_keys = tuple(new_key() for _ in range(3))
        for key in guardian_keys:
            self._fund(foundry.address_of(key))
        self._actors = Actors(
            deployer_key,
            owner_key,
            owner,
            account,
            sponsor_key,
            guardian_keys,
            attester_key,
        )
        self.attest(self.contracts.validator, VALIDATOR_MODULE_TYPE)
        self.attest(self.contracts.recovery, EXECUTOR_MODULE_TYPE)
        self.attest(self.contracts.policy_hook, HOOK_MODULE_TYPE)
        self._fund(account)
        self._send(ENTRY_POINT, FunctionCall("depositTo(address)", (account,)), ETHER)
        self._send(paymaster, FunctionCall("deposit()"), ETHER)
        return (
            f"account {account} predicted; registry deployed; validator, recovery "
            "and hook attested; account and paymaster deposits funded"
        )

    def fund(self, address: str) -> None:
        self._fund(address)

    def send_as(self, transaction: ContractTransaction) -> None:
        self.environment.foundry.send(transaction)

    def chain_id(self) -> int:
        return int(self.environment.node.call(RpcCall("eth_chainId")), 16)

    def chain_time(self) -> int:
        block = self.environment.node.call(
            RpcCall("eth_getBlockByNumber", ("latest", False))
        )
        return int(block["timestamp"], 16)

    def advance_time(self, seconds: int) -> None:
        self.environment.node.call(RpcCall("evm_increaseTime", (seconds,)))
        self.environment.node.call(RpcCall("evm_mine"))

    def pings(self) -> int:
        return self.read_integer(
            ContractCall(self.contracts.target, FunctionCall("pings()(uint256)"))
        )

    def read_integer(self, call: ContractCall) -> int:
        return int(self.environment.foundry.call(call).split()[0])

    def entry_point_balance(self, address: str) -> int:
        return self.read_integer(
            ContractCall(
                ENTRY_POINT, FunctionCall("balanceOf(address)(uint256)", (address,))
            )
        )

    def execute_data(self, request: ExecutionRequest) -> bytes:
        return self._execution_data(EXECUTE_SIGNATURE, request)

    def session_execute_data(self, request: ExecutionRequest) -> bytes:
        return self._execution_data(EXECUTE_AS_SESSION_SIGNATURE, request)

    def ping_data(self) -> bytes:
        return self.execute_data(ExecutionRequest(self.contracts.target, self._ping()))

    def session_ping_data(self) -> bytes:
        return self.session_execute_data(
            ExecutionRequest(self.contracts.target, self._ping())
        )

    def _ping(self) -> bytes:
        return self.environment.foundry.encode_call(FunctionCall("ping()"))

    def _execution_data(self, signature: str, request: ExecutionRequest) -> bytes:
        packed = (
            bytes.fromhex(request.target.removeprefix("0x"))
            + request.value.to_bytes(32, "big")
            + request.inner
        )
        return self.environment.foundry.encode_call(
            FunctionCall(signature, (SINGLE_CALL_MODE, "0x" + packed.hex()))
        )

    def install_call(self, install: ModuleInstall) -> bytes:
        return self.environment.foundry.encode_call(
            FunctionCall(
                "installModule(uint256,address,bytes)",
                (install.module_type, install.module, "0x" + install.data.hex()),
            )
        )

    def set_sanctioned(self, counterparty: str, listed: bool) -> None:
        self._send(
            self.contracts.oracle,
            FunctionCall(
                "setSanctioned(address,bool)",
                (counterparty, "true" if listed else "false"),
            ),
            0,
        )

    def set_oracle_unavailable(self, unavailable: bool) -> None:
        self._send(
            self.contracts.oracle,
            FunctionCall("setUnavailable(bool)", ("true" if unavailable else "false",)),
            0,
        )

    def owner_ping_included_directly(self) -> None:
        operation = self.with_direct_limits(
            self.unsigned(
                UnsignedRequest(b"", self.next_owner_nonce(), self.ping_data())
            )
        )
        self.submit_directly(
            self.sign_with_key(KeyedOperation(operation, self.actors.owner_key))
        )

    def attest(self, module: str, module_type: str) -> None:
        self._send_as_attester(
            FunctionCall(
                "attest(address,uint256,uint48)",
                (module, str(1 << int(module_type)), "0"),
            )
        )

    def revoke_attestation(self, module: str) -> None:
        self._send_as_attester(FunctionCall("revoke(address)", (module,)))

    def registry_check(self, install: ModuleInstall) -> str:
        attester = self.environment.foundry.address_of(self.actors.attester_key)
        return self.environment.foundry.call(
            ContractCall(
                self.contracts.registry,
                FunctionCall(
                    "check(address,uint256,address[],uint256)",
                    (install.module, install.module_type, f"[{attester}]", "1"),
                ),
            )
        )

    def recovery_settings(self) -> bytes:
        foundry = self.environment.foundry
        guardians = [foundry.address_of(key) for key in self.actors.guardian_keys]
        return foundry.encode_arguments(
            FunctionCall(
                "f(address[],uint256,uint256)",
                (
                    "[" + ",".join(guardians) + "]",
                    str(GUARDIAN_THRESHOLD),
                    str(RECOVERY_DELAY_SECONDS),
                ),
            )
        )

    def is_installed(self, module_type: str, module: str) -> bool:
        installed = self.environment.foundry.call(
            ContractCall(
                self.actors.account,
                FunctionCall(
                    "isModuleInstalled(uint256,address,bytes)(bool)",
                    (module_type, module, "0x"),
                ),
            )
        )
        return installed == "true"

    def owner_installs(self, install: ModuleInstall) -> None:
        self.owner_submits(
            OwnerOperation(b"", self.self_call_data(self.install_call(install)))
        )

    def owner_uninstalls(self, install: ModuleInstall) -> None:
        uninstall = self.environment.foundry.encode_call(
            FunctionCall(
                "uninstallModule(uint256,address,bytes)",
                (install.module_type, install.module, "0x"),
            )
        )
        self.owner_submits(OwnerOperation(b"", self.self_call_data(uninstall)))

    def owner_install_included_directly(self, install: ModuleInstall) -> None:
        operation = self.unsigned(
            UnsignedRequest(
                b"",
                self.next_owner_nonce(),
                self.self_call_data(self.install_call(install)),
            )
        ).with_gas_limits(INSTALLATION_SUBMISSION_LIMITS)
        self.submit_directly(
            self.sign_with_key(KeyedOperation(operation, self.actors.owner_key))
        )

    def owner_executes_batch(self, executions: tuple[Execution, ...]) -> None:
        payload = encode_batch_payload(executions)
        call_data = self.environment.foundry.encode_call(
            FunctionCall(EXECUTE_SIGNATURE, (BATCH_CALL_MODE, "0x" + payload.hex()))
        )
        self.owner_submits(OwnerOperation(b"", call_data))

    def current_context(self) -> AccountContext:
        return AccountContext(
            self.actors.account,
            self.enclave_root,
            self.active_policy,
            self.enclave_tenant,
        )

    def switch_to(self, context: AccountContext) -> None:
        self._actors = replace(self.actors, account=context.account)
        self.enclave_root = context.enclave_root
        self.enclave_tenant = context.tenant
        self.active_policy = context.policy

    @property
    def enclaves(self) -> EnclaveSet:
        return EnclaveSet(self.enclave_root, self.enclave_tenant)

    @contextmanager
    def acting_as(self, context: AccountContext) -> Iterator[None]:
        saved = self.current_context()
        self.switch_to(context)
        try:
            yield
        finally:
            self.switch_to(saved)

    def predicted_account(self, salt: str) -> str:
        return self.environment.foundry.call(
            ContractCall(
                self.contracts.factory,
                FunctionCall(
                    "getAddress(address,bytes32)(address)", (self.actors.owner, salt)
                ),
            )
        )

    def creation_init_code(self, salt: str) -> bytes:
        creation = self.environment.foundry.encode_call(
            FunctionCall("createAccount(address,bytes32)", (self.actors.owner, salt))
        )
        return bytes.fromhex(self.contracts.factory.removeprefix("0x")) + creation

    def deposit_for_gas(self, address: str) -> None:
        self._send(ENTRY_POINT, FunctionCall("depositTo(address)", (address,)), ETHER)

    def owner_submits(self, request: OwnerOperation) -> None:
        operation = self.unsigned(
            UnsignedRequest(
                request.init_code, self.next_owner_nonce(), request.call_data
            )
        )
        signed = self.sign_with_key(
            KeyedOperation(self.estimated(operation), self.actors.owner_key)
        )
        self.submit_through_bundler(signed)

    def stored_session_key(self) -> bytes:
        stored = self.environment.foundry.call(
            ContractCall(
                self.contracts.validator,
                FunctionCall(
                    "sessionPublicKeyOf(address)(bytes)", (self.actors.account,)
                ),
            )
        )
        return bytes.fromhex(stored.removeprefix("0x"))

    def stored_session_window(self) -> tuple[int, int]:
        stored = self.environment.foundry.call(
            ContractCall(
                self.contracts.validator,
                FunctionCall(
                    "sessionWindowOf(address)(uint48,uint48)", (self.actors.account,)
                ),
            )
        )
        valid_after, valid_until = (line.split()[0] for line in stored.splitlines())
        return int(valid_after), int(valid_until)

    def native_balance(self, address: str) -> int:
        return int(
            self.environment.node.call(RpcCall("eth_getBalance", (address, "latest"))),
            16,
        )

    def spent_in_window(self, asset: str) -> int:
        return self.read_integer(
            ContractCall(
                self.contracts.policy_hook,
                FunctionCall(
                    "spentInWindow(address,address)(uint256)",
                    (self.actors.account, asset),
                ),
            )
        )

    def self_call_data(self, inner: bytes) -> bytes:
        return self.execute_data(ExecutionRequest(self.actors.account, inner))

    def unsigned(self, request: UnsignedRequest) -> UserOperation:
        price = int(self.environment.node.call(RpcCall("eth_gasPrice")), 16)
        return UserOperation(
            sender=self.actors.account,
            nonce=request.nonce,
            init_code=request.init_code,
            call_data=request.call_data,
            gas_limits=PLACEHOLDER_LIMITS,
            gas_prices=GasPrices(2 * price + GWEI, GWEI),
        )

    def estimated(self, operation: UserOperation) -> UserOperation:
        placeholder = operation.with_signature(DUMMY_SIGNATURE)
        estimate = self.environment.bundler.estimate_gas(
            UserOperationSubmission(placeholder.as_rpc(), ENTRY_POINT)
        )
        return operation.with_gas_limits(
            GasLimits(
                int(estimate["callGasLimit"], 16) + CALL_GAS_MARGIN,
                int(estimate["verificationGasLimit"], 16) + VERIFICATION_GAS_MARGIN,
                int(estimate["preVerificationGas"], 16) + PRE_VERIFICATION_GAS_MARGIN,
            )
        )

    def with_direct_limits(self, operation: UserOperation) -> UserOperation:
        return operation.with_gas_limits(DIRECT_SUBMISSION_LIMITS)

    def operation_hash(self, operation: UserOperation) -> bytes:
        output = self.environment.foundry.call(
            ContractCall(
                ENTRY_POINT,
                FunctionCall(HASH_SIGNATURE, (operation.as_packed_tuple(),)),
            )
        )
        return bytes.fromhex(output.removeprefix("0x"))

    def sign_with_key(self, keyed: KeyedOperation) -> UserOperation:
        digest = self.operation_hash(keyed.operation)
        signature = self.environment.foundry.sign_hash(keyed.private_key, digest)
        return keyed.operation.with_signature(signature)

    def sponsored(self, operation: UserOperation) -> UserOperation:
        contracts, actors = self.contracts, self.actors
        valid_until = self.chain_time() + SPONSORSHIP_LIFETIME_SECONDS
        placeholder = PaymasterFields(
            contracts.paymaster,
            PAYMASTER_VERIFICATION_GAS,
            PAYMASTER_POST_OPERATION_GAS,
            bytes(PAYMASTER_DATA_PLACEHOLDER_LENGTH),
        )
        draft = operation.with_paymaster(placeholder)
        sponsor_hash = self.environment.foundry.call(
            ContractCall(
                contracts.paymaster,
                FunctionCall(
                    SPONSOR_HASH_SIGNATURE,
                    (draft.as_packed_tuple(), str(valid_until), "0"),
                ),
            )
        )
        signature = self.environment.foundry.sign_hash(
            actors.sponsor_key, bytes.fromhex(sponsor_hash.removeprefix("0x"))
        )
        data = (
            valid_until.to_bytes(TIME_BYTES, "big")
            + (0).to_bytes(TIME_BYTES, "big")
            + signature
        )
        return operation.with_paymaster(
            PaymasterFields(
                contracts.paymaster,
                PAYMASTER_VERIFICATION_GAS,
                PAYMASTER_POST_OPERATION_GAS,
                data,
            )
        )

    def next_nonce(self, key: int) -> int:
        return self.read_integer(
            ContractCall(
                ENTRY_POINT,
                FunctionCall(
                    "getNonce(address,uint192)(uint256)",
                    (self.actors.account, str(key)),
                ),
            )
        )

    def next_owner_nonce(self) -> int:
        return self.next_nonce(0)

    def next_session_nonce(self) -> int:
        return self.next_nonce(int(self.contracts.validator, 16) << 32)

    def session_operation(self, nonce: int) -> UserOperation:
        unsigned = self.unsigned(UnsignedRequest(b"", nonce, self.session_ping_data()))
        return self.estimated(unsigned)

    def estimated_session_operation(self, call_data: bytes) -> UserOperation:
        unsigned = self.unsigned(
            UnsignedRequest(b"", self.next_session_nonce(), call_data)
        )
        return self.estimated(unsigned)

    def direct_session_operation(self, call_data: bytes) -> UserOperation:
        unsigned = self.unsigned(
            UnsignedRequest(b"", self.next_session_nonce(), call_data)
        )
        return self.with_direct_limits(unsigned)

    def quorum_signed(self, operation: UserOperation) -> UserOperation:
        digest = self.operation_hash(operation)
        setup = build_signing_setup(self.enclaves)
        signature = SignUserOperation(setup).execute(SigningRequest(digest, operation))
        return operation.with_signature(signature.serialize())

    def compromised_quorum_signed(self, operation: UserOperation) -> UserOperation:
        digest = self.operation_hash(operation)
        records = [
            KeyShareRecord.deserialize(
                tenant_storage(
                    TenantSlot(self.enclave_root, identifier, self.enclave_tenant)
                ).unseal(KEY_SHARE_NAME)
            )
            for identifier in COMPROMISED_QUORUM
        ]
        committed = {
            record.identifier: commit(record.secret_share, fresh_nonce_randomness())
            for record in records
        }
        package = SigningPackage(
            tuple(
                ParticipantCommitment(
                    identifier,
                    commitments.hiding_nonce_commitment,
                    commitments.binding_nonce_commitment,
                )
                for identifier, (_, commitments) in committed.items()
            ),
            digest,
            records[0].group_public_key,
        )
        shares = {
            record.identifier: compute_signature_share(
                package,
                SigningParticipant(
                    record.identifier,
                    record.secret_share,
                    committed[record.identifier][0],
                ),
            )
            for record in records
        }
        return operation.with_signature(aggregate(package, shares).serialize())

    def forge_with_one_share(self, digest: bytes) -> SchnorrSignature:
        record = KeyShareRecord.deserialize(
            tenant_storage(
                TenantSlot(self.enclave_root, 1, self.enclave_tenant)
            ).unseal(KEY_SHARE_NAME)
        )
        nonces, commitments = commit(record.secret_share, fresh_nonce_randomness())
        package = SigningPackage(
            (
                ParticipantCommitment(
                    1,
                    commitments.hiding_nonce_commitment,
                    commitments.binding_nonce_commitment,
                ),
            ),
            digest,
            record.group_public_key,
        )
        share = compute_signature_share(
            package, SigningParticipant(1, record.secret_share, nonces)
        )
        group_commitment = compute_group_commitment(
            package, compute_binding_factors(package)
        )
        forged = SchnorrSignature(group_commitment, share)
        if verify_signature(forged, package):
            raise DrillFailureError(
                "a single share unexpectedly forms a valid signature"
            )
        return forged

    def submit_through_bundler(self, operation: UserOperation) -> dict[str, object]:
        bundler = self.environment.bundler
        operation_hash = bundler.send_user_operation(
            UserOperationSubmission(operation.as_rpc(), ENTRY_POINT)
        )
        for _ in range(RECEIPT_ATTEMPTS):
            receipt = bundler.user_operation_receipt(operation_hash)
            if receipt is not None:
                if not receipt.get("success"):
                    raise DrillFailureError("the operation was included but reverted")
                return receipt
            time.sleep(RECEIPT_INTERVAL_SECONDS)
        raise DrillFailureError("no receipt arrived for the submitted operation")

    def simulate_directly(self, operation: UserOperation) -> None:
        deployer = self.environment.foundry.address_of(self.actors.deployer_key)
        self.environment.foundry.call(
            ContractCall(
                ENTRY_POINT,
                FunctionCall(
                    HANDLE_OPS_SIGNATURE,
                    ("[" + operation.as_packed_tuple() + "]", deployer),
                ),
            )
        )

    def submit_directly(self, operation: UserOperation) -> None:
        deployer = self.environment.foundry.address_of(self.actors.deployer_key)
        self.environment.foundry.send(
            ContractTransaction(
                ContractCall(
                    ENTRY_POINT,
                    FunctionCall(
                        HANDLE_OPS_SIGNATURE,
                        ("[" + operation.as_packed_tuple() + "]", deployer),
                    ),
                ),
                self.actors.deployer_key,
            )
        )

    def _fund(self, address: str) -> None:
        self.environment.node.call(
            RpcCall("anvil_setBalance", (address, hex(100 * ETHER)))
        )

    def _send_as_attester(self, function: FunctionCall) -> None:
        self.environment.foundry.send(
            ContractTransaction(
                ContractCall(self.contracts.registry, function),
                self.actors.attester_key,
            )
        )

    def _send(self, target: str, function: FunctionCall, value: int) -> None:
        self.environment.foundry.send(
            ContractTransaction(
                ContractCall(target, function),
                self.actors.deployer_key,
                str(value),
            )
        )


def new_key() -> str:
    return "0x" + secrets.token_hex(32)


def is_rejection(failure: Exception, code: str) -> bool:
    text = str(failure)
    return code in text or code.encode().hex() in text.lower()
