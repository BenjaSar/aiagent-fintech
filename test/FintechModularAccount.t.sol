// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FintechAccountFactory} from "../contracts/FintechAccountFactory.sol";
import {FintechModuleRegistry} from "../contracts/FintechModuleRegistry.sol";
import {IERC7484Registry} from "../contracts/interfaces/IERC7484Registry.sol";
import {FintechModularAccount} from "../contracts/FintechModularAccount.sol";
import {FintechPolicyHook} from "../contracts/FintechPolicyHook.sol";
import {DevelopmentSanctionsOracle} from "../contracts/dev/DevelopmentSanctionsOracle.sol";
import {FrostSessionValidator} from "../contracts/FrostSessionValidator.sol";
import {PackedUserOperation} from "../contracts/interfaces/PackedUserOperation.sol";
import {EXECUTOR_TYPES, HOOK_TYPES, TestAttester, VALIDATOR_TYPES} from "./support/TestAttester.sol";

interface AccountCheats {
    function etch(address target, bytes calldata code) external;
    function readFile(string calldata path) external view returns (string memory);
    function parseBytes(string calldata value) external pure returns (bytes memory);
    function sign(uint256 privateKey, bytes32 digest) external pure returns (uint8 v, bytes32 r, bytes32 s);
    function addr(uint256 privateKey) external pure returns (address);
    function deal(address target, uint256 amount) external;
    function warp(uint256 timestamp) external;
    function prank(address caller) external;
    function toString(bytes32 value) external pure returns (string memory);
    function expectRevert(bytes calldata revertData) external;
}

interface IEntryPointForAccounts {
    function handleOps(PackedUserOperation[] calldata operations, address payable beneficiary) external;

    function getUserOpHash(PackedUserOperation calldata operation) external view returns (bytes32);

    function depositTo(address account) external payable;

    function getNonce(address sender, uint192 key) external view returns (uint256);
}

contract SessionTarget {
    uint256 public pings;

    function ping() external {
        pings += 1;
    }
}

contract FintechModularAccountTest {
    AccountCheats private constant VM = AccountCheats(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
    address private constant ENTRY_POINT = 0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108;
    address private constant SENDER_CREATOR = 0x449ED7C3e6Fee6a97311d4b55475DF59C44AdD33;
    address payable private constant BENEFICIARY = payable(address(0xBEEF));
    uint256 private constant OWNER_KEY = 0xA11CE;
    uint256 private constant STRANGER_KEY = 0xBAD;
    bytes32 private constant SALT = bytes32(uint256(1));
    bytes32 private constant SINGLE_CALL_MODE = bytes32(0);
    uint48 private constant VALID_AFTER = 0;
    uint48 private constant VALID_UNTIL = 2_000_000_000;
    bytes private constant GROUP_PUBLIC_KEY = hex"02f37c34b66ced1fb51c34a90bdae006901f10625cc06c4f64663b0eae87d87b4f";
    bytes private constant SESSION_SIGNATURE =
        hex"028601f8ec01552ac986d52d7f23043435369ba0b7747613796b76eddfbe1c02d1421ec29a53916f7dade818ee9e846d2f13104d3f76f0ec9de44a8bada604d30d";
    bytes private constant PLAIN_EXECUTE_SIGNATURE =
        hex"0399fcc69caf66b6a05a0f221e850d373aa59d4b7a29b115d7a2aba5fe27cae60363524300cbeef07ce74e6b611df0aff03ebc55847ddd0a47d39a3bed276dbf88";

    FintechAccountFactory private factory;
    FintechModuleRegistry private registry;
    TestAttester private attester;
    FrostSessionValidator private validator;
    SessionTarget private target;
    FintechPolicyHook private policyHook;
    DevelopmentSanctionsOracle private sanctionsOracle;
    address private owner;
    address private account;

    function setUp() public {
        VM.etch(ENTRY_POINT, VM.parseBytes(VM.readFile("fixtures/base-sepolia/EntryPoint-v0.8.0.runtime.hex")));
        VM.etch(SENDER_CREATOR, VM.parseBytes(VM.readFile("fixtures/base-sepolia/SenderCreator-v0.8.0.runtime.hex")));
        registry = new FintechModuleRegistry();
        attester = new TestAttester();
        factory = new FintechAccountFactory(registry, address(attester));
        validator = new FrostSessionValidator();
        target = new SessionTarget();
        sanctionsOracle = new DevelopmentSanctionsOracle();
        policyHook = new FintechPolicyHook(sanctionsOracle);
        attester.attest(registry, address(validator), VALIDATOR_TYPES);
        attester.attest(registry, address(policyHook), HOOK_TYPES);
        owner = VM.addr(OWNER_KEY);
        account = factory.getAddress(owner, SALT);
        VM.deal(address(this), 10 ether);
        IEntryPointForAccounts(ENTRY_POINT).depositTo{value: 1 ether}(account);
    }

    function creationCode() private view returns (bytes memory) {
        return abi.encodePacked(address(factory), abi.encodeCall(FintechAccountFactory.createAccount, (owner, SALT)));
    }

    function installCallData() private view returns (bytes memory) {
        bytes memory installData = abi.encodePacked(GROUP_PUBLIC_KEY, VALID_AFTER, VALID_UNTIL);
        bytes memory install =
            abi.encodeWithSignature("installModule(uint256,address,bytes)", 1, address(validator), installData);
        return abi.encodeWithSignature(
            "execute(bytes32,bytes)", SINGLE_CALL_MODE, abi.encodePacked(account, uint256(0), install)
        );
    }

    function uninstallCallData() private view returns (bytes memory) {
        bytes memory uninstall =
            abi.encodeWithSignature("uninstallModule(uint256,address,bytes)", 1, address(validator), hex"");
        return abi.encodeWithSignature(
            "execute(bytes32,bytes)", SINGLE_CALL_MODE, abi.encodePacked(account, uint256(0), uninstall)
        );
    }

    function plainExecutePingCallData() private view returns (bytes memory) {
        bytes memory ping = abi.encodeCall(SessionTarget.ping, ());
        return abi.encodeWithSignature(
            "execute(bytes32,bytes)", SINGLE_CALL_MODE, abi.encodePacked(address(target), uint256(0), ping)
        );
    }

    function pingCallData() private view returns (bytes memory) {
        bytes memory ping = abi.encodeCall(SessionTarget.ping, ());
        return abi.encodeWithSignature(
            "executeAsSession(bytes32,bytes)", SINGLE_CALL_MODE, abi.encodePacked(address(target), uint256(0), ping)
        );
    }

    function sessionNonce(uint64 sequence) private view returns (uint256) {
        return (uint256(uint160(address(validator))) << 96) | uint256(sequence);
    }

    function unsignedOperation(bytes memory initCode, uint256 nonce, bytes memory callData)
        private
        view
        returns (PackedUserOperation memory)
    {
        return PackedUserOperation({
            sender: account,
            nonce: nonce,
            initCode: initCode,
            callData: callData,
            accountGasLimits: bytes32((uint256(1_500_000) << 128) | uint256(1_500_000)),
            preVerificationGas: 100_000,
            gasFees: bytes32((uint256(1 gwei) << 128) | uint256(1 gwei)),
            paymasterAndData: "",
            signature: ""
        });
    }

    function signedBy(uint256 privateKey, PackedUserOperation memory operation)
        private
        view
        returns (PackedUserOperation memory)
    {
        bytes32 operationHash = IEntryPointForAccounts(ENTRY_POINT).getUserOpHash(operation);
        (uint8 v, bytes32 r, bytes32 s) = VM.sign(privateKey, operationHash);
        operation.signature = abi.encodePacked(r, s, v);
        return operation;
    }

    function submit(PackedUserOperation memory operation) private {
        PackedUserOperation[] memory operations = new PackedUserOperation[](1);
        operations[0] = operation;
        IEntryPointForAccounts(ENTRY_POINT).handleOps(operations, BENEFICIARY);
    }

    function expectRejection(PackedUserOperation memory operation, bytes4 reasonCode) private {
        try this.submitExternally(operation) {
            revert("operation should have been rejected");
        } catch (bytes memory reason) {
            require(bytes4(reason) == bytes4(keccak256("FailedOp(uint256,string)")), "expected a FailedOp revert");
            bytes memory encodedReason = new bytes(reason.length - 4);
            for (uint256 index = 4; index < reason.length; index++) {
                encodedReason[index - 4] = reason[index];
            }
            (, string memory message) = abi.decode(encodedReason, (uint256, string));
            require(bytes4(bytes(message)) == reasonCode, string.concat("unexpected rejection: ", message));
        }
    }

    function submitExternally(PackedUserOperation memory operation) external {
        submit(operation);
    }

    function installValidatorThroughTheOwner() private {
        submit(signedBy(OWNER_KEY, unsignedOperation(creationCode(), 0, installCallData())));
    }

    function testOwnerOperationCreatesTheAccountAndInstallsTheSessionValidator() public {
        installValidatorThroughTheOwner();
        FintechModularAccount deployed = FintechModularAccount(payable(account));
        require(account.code.length != 0, "account must exist");
        require(deployed.signer() == owner, "owner must be the signer");
        require(deployed.isModuleInstalled(1, address(validator), hex""), "validator must be installed");
        require(
            keccak256(validator.sessionPublicKeyOf(account)) == keccak256(GROUP_PUBLIC_KEY), "group key must be set"
        );
    }

    function testOwnerRevokesTheSessionKeyWithoutAnyCompanyInfrastructure() public {
        installValidatorThroughTheOwner();
        submit(signedBy(OWNER_KEY, unsignedOperation("", 1, uninstallCallData())));
        require(
            !FintechModularAccount(payable(account)).isModuleInstalled(1, address(validator), hex""),
            "validator must be uninstalled"
        );
        require(validator.sessionPublicKeyOf(account).length == 0, "session key must be cleared");
    }

    function testAnOperationSignedByAStrangerIsRejected() public {
        expectRejection(signedBy(STRANGER_KEY, unsignedOperation(creationCode(), 0, installCallData())), "AA24");
    }

    function testFactoryRefusesCallsThatDoNotComeFromTheSenderCreator() public {
        try factory.createAccount(owner, SALT) {
            revert("creation should have reverted");
        } catch (bytes memory reason) {
            require(bytes4(reason) == FintechAccountFactory.CallerIsNotTheSenderCreator.selector, "wrong revert reason");
        }
    }

    function testAccountAddressDoesNotDependOnTheCaller() public view {
        require(factory.getAddress(owner, SALT) == account, "prediction must be stable");
        require(factory.getAddress(VM.addr(STRANGER_KEY), SALT) != account, "different owner, different account");
    }

    function testSessionPathRejectsAMalformedSignature() public {
        installValidatorThroughTheOwner();
        PackedUserOperation memory operation = unsignedOperation("", sessionNonce(0), pingCallData());
        operation.signature = new bytes(65);
        expectRejection(operation, "AA24");
    }

    function testSessionPathRejectsAnOperationWhenNoSessionKeyIsInstalled() public {
        installValidatorThroughTheOwner();
        submit(signedBy(OWNER_KEY, unsignedOperation("", 1, uninstallCallData())));
        PackedUserOperation memory operation = unsignedOperation("", sessionNonce(0), pingCallData());
        operation.signature = new bytes(65);
        expectRejection(operation, "AA24");
    }

    function installPolicyHookThroughTheAccount() private {
        FintechPolicyHook.AssetRule[] memory rules = new FintechPolicyHook.AssetRule[](0);
        address[] memory targets = new address[](1);
        targets[0] = address(target);
        VM.prank(account);
        address[] memory payees = new address[](0);
        FintechModularAccount(payable(account))
            .installModule(4, address(policyHook), abi.encode(targets, rules, payees, false));
    }

    function quorumSignedSessionOperation() private view returns (PackedUserOperation memory operation) {
        operation = unsignedOperation("", sessionNonce(0), pingCallData());
        operation.signature = SESSION_SIGNATURE;
    }

    function testQuorumSignedSessionOperationExecutesThroughTheAccount() public {
        installValidatorThroughTheOwner();
        installPolicyHookThroughTheAccount();
        submit(quorumSignedSessionOperation());
        require(target.pings() == 1, "session operation must have executed");
    }

    function testSessionOperationCannotBeReplayed() public {
        installValidatorThroughTheOwner();
        installPolicyHookThroughTheAccount();
        submit(quorumSignedSessionOperation());
        expectRejection(quorumSignedSessionOperation(), "AA25");
    }

    function testSessionOperationIsRejectedAfterTheTtl() public {
        installValidatorThroughTheOwner();
        installPolicyHookThroughTheAccount();
        VM.warp(uint256(VALID_UNTIL) + 1);
        expectRejection(quorumSignedSessionOperation(), "AA22");
    }

    function testQuorumSignatureDoesNotCoverADifferentOperation() public {
        installValidatorThroughTheOwner();
        PackedUserOperation memory operation = quorumSignedSessionOperation();
        operation.callData = otherSessionCallData();
        expectRejection(operation, "AA24");
    }

    function otherSessionCallData() private view returns (bytes memory) {
        bytes memory ping = abi.encodeCall(SessionTarget.ping, ());
        return abi.encodeWithSignature(
            "executeAsSession(bytes32,bytes)", SINGLE_CALL_MODE, abi.encodePacked(address(this), uint256(0), ping)
        );
    }

    function testSessionOperationCallingPlainExecuteIsRefusedDespiteAValidQuorumSignature() public {
        installValidatorThroughTheOwner();
        installPolicyHookThroughTheAccount();
        PackedUserOperation memory operation = unsignedOperation("", sessionNonce(0), plainExecutePingCallData());
        operation.signature = PLAIN_EXECUTE_SIGNATURE;
        expectRejection(operation, "AA24");
        require(target.pings() == 0, "nothing may have executed");
    }

    function testSessionOperationWithoutCallDataIsRefused() public {
        installValidatorThroughTheOwner();
        PackedUserOperation memory operation = unsignedOperation("", sessionNonce(0), hex"");
        operation.signature = SESSION_SIGNATURE;
        expectRejection(operation, "AA24");
    }

    function testSessionExecutionFailsClosedWhenNoPolicyHookIsInstalled() public {
        installValidatorThroughTheOwner();
        expectExecutionRevert(quorumSignedSessionOperation());
        require(target.pings() == 0, "nothing may have executed without the hook");
    }

    function testOwnerOperationsStillWorkAfterThePolicyHookIsInstalled() public {
        installValidatorThroughTheOwner();
        installPolicyHookThroughTheAccount();
        submit(signedBy(OWNER_KEY, unsignedOperation("", 1, uninstallCallData())));
        require(
            !FintechModularAccount(payable(account)).isModuleInstalled(1, address(validator), hex""),
            "owner must still revoke the session key"
        );
    }

    function testOwnerCanUninstallThePolicyHookThroughAnOperation() public {
        installValidatorThroughTheOwner();
        installPolicyHookThroughTheAccount();
        bytes memory uninstall =
            abi.encodeWithSignature("uninstallModule(uint256,address,bytes)", 4, address(policyHook), hex"");
        bytes memory callData = abi.encodeWithSignature(
            "execute(bytes32,bytes)", SINGLE_CALL_MODE, abi.encodePacked(account, uint256(0), uninstall)
        );
        submit(signedBy(OWNER_KEY, unsignedOperation("", 1, callData)));
        require(
            !FintechModularAccount(payable(account)).isModuleInstalled(4, address(policyHook), hex""),
            "hook must be uninstalled"
        );
    }

    function expectExecutionRevert(PackedUserOperation memory operation) private {
        PackedUserOperation[] memory operations = new PackedUserOperation[](1);
        operations[0] = operation;
        IEntryPointForAccounts(ENTRY_POINT).handleOps(operations, BENEFICIARY);
    }

    address private constant OTHER_ATTESTER = address(0xA77E);
    bytes private constant POLICY_CHANGE_SIGNATURE =
        hex"022d4084573e5810c40e2ad334d28e665dc62ef24e75e4d18f07c616a98ff9cf2d369de1e41e4f4d5705ff1e87ae6efc09a8a291736e209da030390ef44fced418";

    function attesterPolicyCallData() private pure returns (bytes memory) {
        address[] memory attesters = new address[](1);
        attesters[0] = OTHER_ATTESTER;
        return abi.encodeWithSignature("setAttesterPolicy(address[],uint8)", attesters, uint8(1));
    }

    function installFromTheAccount(uint256 moduleType, address module, bytes memory data) private {
        VM.prank(account);
        FintechModularAccount(payable(account)).installModule(moduleType, module, data);
    }

    function expectInstallFailure(bytes memory reason, uint256 moduleType, address module) private {
        VM.expectRevert(reason);
        installFromTheAccount(moduleType, module, hex"");
    }

    function notMet() private pure returns (bytes memory) {
        return abi.encodeWithSelector(FintechModuleRegistry.AttestationThresholdNotMet.selector, 0, 1);
    }

    function testAnAccountTrustsTheFactoryAttesterWithAThresholdOfOne() public {
        installValidatorThroughTheOwner();
        (address[] memory attesters, uint8 threshold) = FintechModularAccount(payable(account)).attesterPolicy();
        require(attesters.length == 1 && attesters[0] == address(attester) && threshold == 1, "default policy");
        require(address(FintechModularAccount(payable(account)).registry()) == address(registry), "registry");
    }

    function testAnUnattestedValidatorCannotBeInstalled() public {
        installValidatorThroughTheOwner();
        FrostSessionValidator rogue = new FrostSessionValidator();
        expectInstallFailure(notMet(), 1, address(rogue));
        require(!FintechModularAccount(payable(account)).isModuleInstalled(1, address(rogue), hex""), "not installed");
    }

    function testAnUnattestedPolicyHookCannotBeInstalled() public {
        installValidatorThroughTheOwner();
        FintechPolicyHook rogue = new FintechPolicyHook(sanctionsOracle);
        expectInstallFailure(notMet(), 4, address(rogue));
        require(FintechModularAccount(payable(account)).hook() == address(0), "no hook");
    }

    function testAModuleWhoseAttestationWasRevokedCannotBeInstalled() public {
        installValidatorThroughTheOwner();
        FrostSessionValidator rogue = new FrostSessionValidator();
        attester.attest(registry, address(rogue), VALIDATOR_TYPES);
        attester.revoke(registry, address(rogue));
        expectInstallFailure(
            abi.encodeWithSelector(
                FintechModuleRegistry.AttestationWasRevoked.selector, address(rogue), address(attester)
            ),
            1,
            address(rogue)
        );
    }

    function testAModuleAttestedForAnotherTypeCannotBeInstalledAsAValidator() public {
        installValidatorThroughTheOwner();
        FrostSessionValidator rogue = new FrostSessionValidator();
        attester.attest(registry, address(rogue), EXECUTOR_TYPES);
        expectInstallFailure(
            abi.encodeWithSelector(
                FintechModuleRegistry.ModuleTypeMismatch.selector, address(rogue), address(attester), VALIDATOR_TYPES
            ),
            1,
            address(rogue)
        );
    }

    function testAnOwnerOperationInstallingAnUnattestedModuleChangesNothing() public {
        installValidatorThroughTheOwner();
        FrostSessionValidator rogue = new FrostSessionValidator();
        bytes memory install = abi.encodeWithSignature("installModule(uint256,address,bytes)", 1, address(rogue), hex"");
        bytes memory callData = abi.encodeWithSignature(
            "execute(bytes32,bytes)", SINGLE_CALL_MODE, abi.encodePacked(account, uint256(0), install)
        );
        submit(signedBy(OWNER_KEY, unsignedOperation("", 1, callData)));
        require(!FintechModularAccount(payable(account)).isModuleInstalled(1, address(rogue), hex""), "not installed");
        require(IEntryPointForAccounts(ENTRY_POINT).getNonce(account, 0) == 2, "the nonce is consumed");
    }

    function testTheOwnerCanReplaceTheTrustedAttesters() public {
        installValidatorThroughTheOwner();
        TestAttester second = new TestAttester();
        FrostSessionValidator replacement = new FrostSessionValidator();
        FrostSessionValidator formerlyTrusted = new FrostSessionValidator();
        attester.attest(registry, address(formerlyTrusted), VALIDATOR_TYPES);
        second.attest(registry, address(replacement), VALIDATOR_TYPES);
        address[] memory attesters = new address[](1);
        attesters[0] = address(second);
        VM.prank(account);
        FintechModularAccount(payable(account)).setAttesterPolicy(attesters, 1);
        expectInstallFailure(notMet(), 1, address(formerlyTrusted));
        installFromTheAccount(1, address(replacement), abi.encodePacked(GROUP_PUBLIC_KEY, VALID_AFTER, VALID_UNTIL));
        require(
            FintechModularAccount(payable(account)).isModuleInstalled(1, address(replacement), hex""),
            "a module attested by the new attester installs"
        );
    }

    function testAnInvalidAttesterPolicyBlocksInstallationUntilItIsFixed() public {
        installValidatorThroughTheOwner();
        address[] memory attesters = new address[](0);
        VM.prank(account);
        FintechModularAccount(payable(account)).setAttesterPolicy(attesters, 1);
        FrostSessionValidator rogue = new FrostSessionValidator();
        attester.attest(registry, address(rogue), VALIDATOR_TYPES);
        expectInstallFailure(
            abi.encodeWithSelector(FintechModuleRegistry.InvalidAttesterSet.selector), 1, address(rogue)
        );
        attesters = new address[](1);
        attesters[0] = address(attester);
        VM.prank(account);
        FintechModularAccount(payable(account)).setAttesterPolicy(attesters, 1);
        installFromTheAccount(1, address(rogue), abi.encodePacked(GROUP_PUBLIC_KEY, VALID_AFTER, VALID_UNTIL));
    }

    function testOnlyTheEntryPointOrTheAccountCanChangeTheAttesterPolicy() public {
        installValidatorThroughTheOwner();
        address[] memory attesters = new address[](1);
        attesters[0] = OTHER_ATTESTER;
        try FintechModularAccount(payable(account)).setAttesterPolicy(attesters, 1) {
            revert("a stranger changed the policy");
        } catch {}
        (address[] memory current,) = FintechModularAccount(payable(account)).attesterPolicy();
        require(current[0] == address(attester), "the policy must be unchanged");
    }

    function testSessionOperationChangingTheAttesterPolicyIsRefusedDespiteAValidQuorumSignature() public {
        installValidatorThroughTheOwner();
        installPolicyHookThroughTheAccount();
        PackedUserOperation memory operation = unsignedOperation("", sessionNonce(0), attesterPolicyCallData());
        operation.signature = POLICY_CHANGE_SIGNATURE;
        expectRejection(operation, "AA24");
        (address[] memory current,) = FintechModularAccount(payable(account)).attesterPolicy();
        require(current[0] == address(attester), "the policy must be unchanged");
    }

    function testRevokingAnAttestationDoesNotBlockTheOwnerFromUninstalling() public {
        installValidatorThroughTheOwner();
        attester.revoke(registry, address(validator));
        submit(signedBy(OWNER_KEY, unsignedOperation("", 1, uninstallCallData())));
        require(
            !FintechModularAccount(payable(account)).isModuleInstalled(1, address(validator), hex""),
            "uninstallation must not consult the registry"
        );
    }

    function testRevokingAnAttestationDoesNotStopAnAlreadyInstalledModule() public {
        installValidatorThroughTheOwner();
        installPolicyHookThroughTheAccount();
        attester.revoke(registry, address(validator));
        submit(quorumSignedSessionOperation());
        require(target.pings() == 1, "the install-time gate does not police use");
    }

    function testTheFactoryRefusesAZeroAttester() public {
        VM.expectRevert(abi.encodeWithSelector(FintechAccountFactory.InvalidAttester.selector));
        new FintechAccountFactory(registry, address(0));
    }

    function testTheAccountRefusesAZeroRegistry() public {
        VM.expectRevert(abi.encodeWithSelector(FintechModularAccount.InvalidRegistry.selector));
        new FintechAccountFactory(IERC7484Registry(address(0)), address(attester));
    }
}
