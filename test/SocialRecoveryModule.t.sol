// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FintechAccountFactory} from "../contracts/FintechAccountFactory.sol";
import {FintechModuleRegistry} from "../contracts/FintechModuleRegistry.sol";
import {FintechModularAccount} from "../contracts/FintechModularAccount.sol";
import {SocialRecoveryModule} from "../contracts/SocialRecoveryModule.sol";
import {EXECUTOR_TYPES, TestAttester} from "./support/TestAttester.sol";

interface RecoveryCheats {
    function etch(address target, bytes calldata code) external;
    function readFile(string calldata path) external view returns (string memory);
    function parseBytes(string calldata value) external pure returns (bytes memory);
    function prank(address caller) external;
    function warp(uint256 timestamp) external;
    function expectRevert(bytes4 selector) external;
    function expectRevert(bytes calldata reason) external;
}

contract SocialRecoveryModuleTest {
    RecoveryCheats private constant VM = RecoveryCheats(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
    address private constant ENTRY_POINT = 0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108;
    address private constant SENDER_CREATOR = 0x449ED7C3e6Fee6a97311d4b55475DF59C44AdD33;
    address private constant OWNER = address(0xA11CE);
    address private constant NEW_OWNER = address(0xB0B);
    address private constant OTHER_NEW_OWNER = address(0xC0C);
    address private constant FIRST_GUARDIAN = address(0x6001);
    address private constant SECOND_GUARDIAN = address(0x6002);
    address private constant THIRD_GUARDIAN = address(0x6003);
    address private constant STRANGER = address(0xBAD);
    bytes32 private constant SALT = bytes32(uint256(1));
    uint256 private constant EXECUTOR_TYPE = 2;
    uint256 private constant THRESHOLD = 2;
    uint256 private constant DELAY = 1 days;

    FintechAccountFactory private factory;
    FintechModuleRegistry private registry;
    TestAttester private attester;
    SocialRecoveryModule private module;
    FintechModularAccount private account;

    function setUp() public {
        VM.warp(1_000_000);
        VM.etch(ENTRY_POINT, VM.parseBytes(VM.readFile("fixtures/base-sepolia/EntryPoint-v0.8.0.runtime.hex")));
        VM.etch(SENDER_CREATOR, VM.parseBytes(VM.readFile("fixtures/base-sepolia/SenderCreator-v0.8.0.runtime.hex")));
        registry = new FintechModuleRegistry();
        attester = new TestAttester();
        factory = new FintechAccountFactory(registry, address(attester));
        VM.prank(SENDER_CREATOR);
        account = FintechModularAccount(payable(factory.createAccount(OWNER, SALT)));
        module = new SocialRecoveryModule();
        attester.attest(registry, address(module), EXECUTOR_TYPES);
        installModule(guardianList(), THRESHOLD, DELAY);
    }

    function guardianList() private pure returns (address[] memory guardians) {
        guardians = new address[](3);
        guardians[0] = FIRST_GUARDIAN;
        guardians[1] = SECOND_GUARDIAN;
        guardians[2] = THIRD_GUARDIAN;
    }

    function installModule(address[] memory guardians, uint256 threshold, uint256 delay) private {
        VM.prank(address(account));
        account.installModule(EXECUTOR_TYPE, address(module), abi.encode(guardians, threshold, delay));
    }

    function approve(address guardian, address newOwner) private {
        VM.prank(guardian);
        module.approveRecovery(address(account), newOwner);
    }

    function scheduleRecovery(address newOwner) private {
        approve(FIRST_GUARDIAN, newOwner);
        approve(SECOND_GUARDIAN, newOwner);
    }

    function testInstallationRecordsTheGuardiansThresholdAndDelay() public view {
        require(module.guardiansOf(address(account)).length == 3, "three guardians");
        require(module.thresholdOf(address(account)) == THRESHOLD, "threshold");
        require(module.delayOf(address(account)) == DELAY, "delay");
        require(account.isModuleInstalled(EXECUTOR_TYPE, address(module), ""), "installed as executor");
    }

    function testInstallationRejectsInvalidConfigurations() public {
        VM.prank(SENDER_CREATOR);
        account = FintechModularAccount(payable(factory.createAccount(STRANGER, SALT)));
        address[] memory guardians = guardianList();
        address[] memory duplicated = new address[](2);
        duplicated[0] = FIRST_GUARDIAN;
        duplicated[1] = FIRST_GUARDIAN;
        address[] memory withZero = new address[](2);
        withZero[0] = FIRST_GUARDIAN;

        expectInstallFailure(guardians, 0, DELAY);
        expectInstallFailure(guardians, 4, DELAY);
        expectInstallFailure(guardians, THRESHOLD, 1 hours - 1);
        expectInstallFailure(duplicated, 1, DELAY);
        expectInstallFailure(withZero, 1, DELAY);
    }

    function expectInstallFailure(address[] memory guardians, uint256 threshold, uint256 delay) private {
        VM.prank(address(account));
        try account.installModule(EXECUTOR_TYPE, address(module), abi.encode(guardians, threshold, delay)) {
            revert("installation should have failed");
        } catch (bytes memory reason) {
            require(bytes4(reason) == SocialRecoveryModule.InvalidConfiguration.selector, "wrong reason");
        }
    }

    function testASingleApprovalDoesNotScheduleRecovery() public {
        approve(FIRST_GUARDIAN, NEW_OWNER);
        require(module.readyAtOf(address(account), NEW_OWNER) == 0, "must not be scheduled");
    }

    function testReachingTheThresholdSchedulesRecoveryAfterTheDelay() public {
        scheduleRecovery(NEW_OWNER);
        require(module.readyAtOf(address(account), NEW_OWNER) == block.timestamp + DELAY, "ready time");
    }

    function testRecoveryCannotBeExecutedBeforeTheDelay() public {
        scheduleRecovery(NEW_OWNER);
        VM.warp(block.timestamp + DELAY - 1);
        VM.expectRevert(SocialRecoveryModule.RecoveryNotReady.selector);
        module.executeRecovery(address(account), NEW_OWNER);
    }

    function testAnyoneCanExecuteAReadyRecoveryAndTheOwnerChanges() public {
        scheduleRecovery(NEW_OWNER);
        VM.warp(block.timestamp + DELAY);
        VM.prank(STRANGER);
        module.executeRecovery(address(account), NEW_OWNER);
        require(account.signer() == NEW_OWNER, "owner must have rotated");
    }

    function testAnExecutedRecoveryCannotBeReplayed() public {
        scheduleRecovery(NEW_OWNER);
        VM.warp(block.timestamp + DELAY);
        module.executeRecovery(address(account), NEW_OWNER);
        VM.expectRevert(SocialRecoveryModule.RecoveryNotReady.selector);
        module.executeRecovery(address(account), NEW_OWNER);
    }

    function testApprovalsForDifferentOwnersDoNotCombine() public {
        approve(FIRST_GUARDIAN, NEW_OWNER);
        approve(SECOND_GUARDIAN, OTHER_NEW_OWNER);
        VM.warp(block.timestamp + DELAY);
        VM.expectRevert(SocialRecoveryModule.RecoveryNotReady.selector);
        module.executeRecovery(address(account), NEW_OWNER);
    }

    function testOnlyGuardiansCanApprove() public {
        VM.prank(STRANGER);
        VM.expectRevert(SocialRecoveryModule.NotAGuardian.selector);
        module.approveRecovery(address(account), NEW_OWNER);
    }

    function testTheOwnerIsNotAGuardianUnlessChosen() public {
        VM.prank(OWNER);
        VM.expectRevert(SocialRecoveryModule.NotAGuardian.selector);
        module.approveRecovery(address(account), NEW_OWNER);
    }

    function testAGuardianCannotApproveTwice() public {
        approve(FIRST_GUARDIAN, NEW_OWNER);
        VM.prank(FIRST_GUARDIAN);
        VM.expectRevert(SocialRecoveryModule.AlreadyApproved.selector);
        module.approveRecovery(address(account), NEW_OWNER);
    }

    function testTheZeroAddressCannotBecomeTheOwner() public {
        VM.prank(FIRST_GUARDIAN);
        VM.expectRevert(SocialRecoveryModule.InvalidNewOwner.selector);
        module.approveRecovery(address(account), address(0));
    }

    function testTheAccountCanCancelAPendingRecovery() public {
        scheduleRecovery(NEW_OWNER);
        VM.prank(address(account));
        module.cancelRecovery();
        VM.warp(block.timestamp + DELAY);
        VM.expectRevert(SocialRecoveryModule.RecoveryNotReady.selector);
        module.executeRecovery(address(account), NEW_OWNER);
    }

    function testGuardiansCanStartAgainAfterACancellation() public {
        scheduleRecovery(NEW_OWNER);
        VM.prank(address(account));
        module.cancelRecovery();
        scheduleRecovery(NEW_OWNER);
        VM.warp(block.timestamp + DELAY);
        module.executeRecovery(address(account), NEW_OWNER);
        require(account.signer() == NEW_OWNER, "second attempt must work");
    }

    function testGuardiansOfOneAccountHaveNoPowerOverAnother() public {
        VM.prank(SENDER_CREATOR);
        FintechModularAccount other = FintechModularAccount(payable(factory.createAccount(STRANGER, SALT)));
        VM.prank(FIRST_GUARDIAN);
        VM.expectRevert(SocialRecoveryModule.NotAGuardian.selector);
        module.approveRecovery(address(other), NEW_OWNER);
    }

    function testRotationIsRefusedForCallersThatAreNotInstalledExecutors() public {
        VM.prank(STRANGER);
        VM.expectRevert(abi.encodeWithSelector(FintechModularAccount.CallerIsNotAnInstalledExecutor.selector, STRANGER));
        account.rotateOwner(NEW_OWNER);
    }

    function testTheAccountCannotRotateItsOwnerThroughASelfCall() public {
        VM.prank(address(account));
        VM.expectRevert(
            abi.encodeWithSelector(FintechModularAccount.CallerIsNotAnInstalledExecutor.selector, address(account))
        );
        account.rotateOwner(NEW_OWNER);
    }

    function testUninstallingTheModuleClearsGuardiansAndBlocksPendingRecoveries() public {
        scheduleRecovery(NEW_OWNER);
        VM.prank(address(account));
        account.uninstallModule(EXECUTOR_TYPE, address(module), "");
        require(module.guardiansOf(address(account)).length == 0, "guardians must be cleared");
        VM.warp(block.timestamp + DELAY);
        VM.expectRevert(SocialRecoveryModule.RecoveryNotReady.selector);
        module.executeRecovery(address(account), NEW_OWNER);
    }

    function testAnAlreadyConfiguredAccountCannotBeReconfigured() public {
        VM.prank(address(account));
        VM.expectRevert(SocialRecoveryModule.AlreadyConfigured.selector);
        module.onInstall(abi.encode(guardianList(), THRESHOLD, DELAY));
    }
}
