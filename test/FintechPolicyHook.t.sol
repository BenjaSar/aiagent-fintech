// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FintechAccountFactory} from "../contracts/FintechAccountFactory.sol";
import {FintechModuleRegistry} from "../contracts/FintechModuleRegistry.sol";
import {FintechModularAccount} from "../contracts/FintechModularAccount.sol";
import {FintechPolicyHook} from "../contracts/FintechPolicyHook.sol";
import {DevelopmentSanctionsOracle} from "../contracts/dev/DevelopmentSanctionsOracle.sol";
import {ISanctionsOracle} from "../contracts/interfaces/ISanctionsOracle.sol";
import {HOOK_TYPES, TestAttester} from "./support/TestAttester.sol";
import {Execution} from "@openzeppelin/contracts/interfaces/draft-IERC7579.sol";

interface HookCheats {
    function etch(address target, bytes calldata code) external;
    function readFile(string calldata path) external view returns (string memory);
    function parseBytes(string calldata value) external pure returns (bytes memory);
    function prank(address caller) external;
    function deal(address target, uint256 amount) external;
    function warp(uint256 timestamp) external;
    function expectRevert() external;
    function expectRevert(bytes4 selector) external;
    function startPrank(address caller) external;
    function stopPrank() external;
    function expectRevert(bytes calldata reason) external;
}

contract Sink {
    uint256 public pings;

    receive() external payable {}

    function ping() external {
        pings += 1;
    }
}

contract SimpleToken {
    mapping(address owner => uint256 amount) public balanceOf;
    mapping(address owner => mapping(address spender => uint256 amount)) public allowance;

    function mint(address recipient, uint256 amount) external {
        balanceOf[recipient] += amount;
    }

    function transfer(address recipient, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[recipient] += amount;
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transferFrom(address from, address recipient, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[recipient] += amount;
        return true;
    }
}

contract AllowanceDrainer {
    function drain(SimpleToken token, address victim, uint256 amount) external {
        token.transferFrom(victim, address(this), amount);
    }
}

contract FintechPolicyHookTest {
    HookCheats private constant VM = HookCheats(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
    address private constant ENTRY_POINT = 0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108;
    address private constant SENDER_CREATOR = 0x449ED7C3e6Fee6a97311d4b55475DF59C44AdD33;
    address private constant OWNER = address(0xA11CE);
    address private constant RECIPIENT = address(0xBEEF);
    bytes32 private constant SALT = bytes32(uint256(1));
    bytes32 private constant SINGLE_MODE = bytes32(0);
    bytes32 private constant BATCH_MODE = bytes32(uint256(1) << 248);
    bytes32 private constant DELEGATE_MODE = bytes32(uint256(0xff) << 248);
    bytes32 private constant TRY_MODE = bytes32(uint256(1) << 240);
    bytes32 private constant SELECTOR_MODE = bytes32(uint256(1) << 176);
    bytes32 private constant PAYLOAD_MODE = bytes32(uint256(1));
    bytes32 private constant UNKNOWN_CALL_TYPE_MODE = bytes32(uint256(2) << 248);
    uint256 private constant HOOK_TYPE = 4;
    uint256 private constant NATIVE_CAP = 1 ether;
    uint256 private constant TOKEN_CAP = 1000e6;
    uint256 private constant ONE_DAY = 1 days;
    uint256 private constant START = 1_000_000;

    FintechModuleRegistry private registry;
    TestAttester private attester;
    FintechPolicyHook private hook;
    DevelopmentSanctionsOracle private oracle;
    FintechModularAccount private account;
    Sink private sink;
    SimpleToken private token;
    AllowanceDrainer private drainer;

    function setUp() public {
        VM.warp(START);
        VM.etch(ENTRY_POINT, VM.parseBytes(VM.readFile("fixtures/base-sepolia/EntryPoint-v0.8.0.runtime.hex")));
        VM.etch(SENDER_CREATOR, VM.parseBytes(VM.readFile("fixtures/base-sepolia/SenderCreator-v0.8.0.runtime.hex")));
        registry = new FintechModuleRegistry();
        attester = new TestAttester();
        FintechAccountFactory factory = new FintechAccountFactory(registry, address(attester));
        VM.prank(SENDER_CREATOR);
        account = FintechModularAccount(payable(factory.createAccount(OWNER, SALT)));
        oracle = new DevelopmentSanctionsOracle();
        hook = new FintechPolicyHook(oracle);
        attester.attest(registry, address(hook), HOOK_TYPES);
        sink = new Sink();
        token = new SimpleToken();
        drainer = new AllowanceDrainer();
        VM.deal(address(account), 10 ether);
        token.mint(address(account), 10_000e6);
        installHook(configuration(NATIVE_CAP, TOKEN_CAP));
    }

    function configuration(uint256 nativeCap, uint256 tokenCap) private view returns (bytes memory) {
        address[] memory targets = new address[](2);
        targets[0] = address(sink);
        targets[1] = address(drainer);
        FintechPolicyHook.AssetRule[] memory rules = new FintechPolicyHook.AssetRule[](2);
        rules[0] = FintechPolicyHook.AssetRule(address(0), nativeCap);
        rules[1] = FintechPolicyHook.AssetRule(address(token), tokenCap);
        return abi.encode(targets, rules, payees(), false);
    }

    function payees() private pure returns (address[] memory empty) {
        empty = new address[](0);
    }

    function installHook(bytes memory data) private {
        VM.prank(address(account));
        account.installModule(HOOK_TYPE, address(hook), data);
    }

    function single(address target, uint256 value, bytes memory callData) private pure returns (bytes memory) {
        return abi.encodePacked(target, value, callData);
    }

    function pingExecution() private view returns (bytes memory) {
        return single(address(sink), 0, abi.encodeCall(Sink.ping, ()));
    }

    function sendValue(uint256 amount) private view returns (bytes memory) {
        return single(address(sink), amount, "");
    }

    function tokenTransfer(uint256 amount) private view returns (bytes memory) {
        return single(address(token), 0, abi.encodeCall(SimpleToken.transfer, (RECIPIENT, amount)));
    }

    function run(bytes32 mode, bytes memory executionCalldata) private {
        VM.prank(ENTRY_POINT);
        account.executeAsSession(mode, executionCalldata);
    }

    function runSingle(bytes memory executionCalldata) private {
        run(SINGLE_MODE, executionCalldata);
    }

    function expectFailure(bytes memory reason, bytes32 mode, bytes memory executionCalldata) private {
        VM.expectRevert(reason);
        run(mode, executionCalldata);
    }

    function expectFailure(bytes4 selector, bytes32 mode, bytes memory executionCalldata) private {
        VM.expectRevert(selector);
        run(mode, executionCalldata);
    }

    function batchOf(Execution[] memory executions) private pure returns (bytes memory) {
        return abi.encode(executions);
    }

    function twoValueTransfers(uint256 first, uint256 second) private view returns (bytes memory) {
        Execution[] memory executions = new Execution[](2);
        executions[0] = Execution(address(sink), first, "");
        executions[1] = Execution(address(sink), second, "");
        return batchOf(executions);
    }

    function testAnAllowlistedTargetCanBeCalled() public {
        runSingle(pingExecution());
        require(sink.pings() == 1, "ping must execute");
    }

    function testATargetOutsideTheAllowlistIsRefused() public {
        bytes memory execution = single(RECIPIENT, 0, "");
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.TargetNotAllowed.selector, RECIPIENT), SINGLE_MODE, execution
        );
    }

    function testTheAccountCannotCallItselfFromASession() public {
        bytes memory install = abi.encodeWithSignature("installModule(uint256,address,bytes)", 2, address(sink), hex"");
        bytes memory execution = single(address(account), 0, install);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.ForbiddenTarget.selector, address(account)), SINGLE_MODE, execution
        );
    }

    function testTheHookCannotBeReconfiguredFromASession() public {
        bytes memory reconfigure = abi.encodeCall(FintechPolicyHook.allowTarget, (RECIPIENT));
        bytes memory execution = single(address(hook), 0, reconfigure);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.ForbiddenTarget.selector, address(hook)), SINGLE_MODE, execution
        );
    }

    function testTheEntryPointCannotBeCalledFromASession() public {
        bytes memory withdraw = abi.encodeWithSignature("withdrawTo(address,uint256)", RECIPIENT, 1 ether);
        bytes memory execution = single(ENTRY_POINT, 0, withdraw);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.ForbiddenTarget.selector, ENTRY_POINT), SINGLE_MODE, execution
        );
    }

    function testTheZeroAddressIsNeverATarget() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.ForbiddenTarget.selector, address(0)),
            SINGLE_MODE,
            single(address(0), 0, "")
        );
    }

    function testDelegateCallModeIsRefused() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.UnsupportedMode.selector, DELEGATE_MODE),
            DELEGATE_MODE,
            abi.encodePacked(address(sink), hex"1234")
        );
    }

    function testTryExecutionTypeIsRefused() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.UnsupportedMode.selector, TRY_MODE), TRY_MODE, pingExecution()
        );
    }

    function testNonZeroModeSelectorAndPayloadAreRefused() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.UnsupportedMode.selector, SELECTOR_MODE),
            SELECTOR_MODE,
            pingExecution()
        );
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.UnsupportedMode.selector, PAYLOAD_MODE),
            PAYLOAD_MODE,
            pingExecution()
        );
    }

    function testUnknownCallTypeIsRefused() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.UnsupportedMode.selector, UNKNOWN_CALL_TYPE_MODE),
            UNKNOWN_CALL_TYPE_MODE,
            pingExecution()
        );
    }

    function testTruncatedSingleExecutionIsRefused() public {
        VM.prank(ENTRY_POINT);
        try account.executeAsSession(SINGLE_MODE, abi.encodePacked(address(sink), uint16(7))) {
            revert("truncated execution must fail");
        } catch {}
        require(sink.pings() == 0, "nothing may execute");
    }

    function testMalformedBatchIsRefused() public {
        VM.prank(ENTRY_POINT);
        try account.executeAsSession(
            BATCH_MODE, hex"00000000000000000000000000000000000000000000000000000000000000ff"
        ) {
            revert("malformed batch must fail");
        } catch {}
        require(sink.pings() == 0, "nothing may execute");
    }

    function testEmptyBatchIsRefused() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.EmptyExecution.selector), BATCH_MODE, batchOf(new Execution[](0))
        );
    }

    function testOversizedBatchIsRefused() public {
        Execution[] memory executions = new Execution[](17);
        for (uint256 index = 0; index < executions.length; index++) {
            executions[index] = Execution(address(sink), 0, abi.encodeCall(Sink.ping, ()));
        }
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.TooManyExecutions.selector, 17), BATCH_MODE, batchOf(executions)
        );
    }

    function testBatchOfAllowedCallsExecutesEveryCall() public {
        Execution[] memory executions = new Execution[](3);
        for (uint256 index = 0; index < 3; index++) {
            executions[index] = Execution(address(sink), 0, abi.encodeCall(Sink.ping, ()));
        }
        run(BATCH_MODE, batchOf(executions));
        require(sink.pings() == 3, "every call must execute");
    }

    function testOneForbiddenCallInABatchRefusesTheWholeBatch() public {
        Execution[] memory executions = new Execution[](2);
        executions[0] = Execution(address(sink), 0, abi.encodeCall(Sink.ping, ()));
        executions[1] = Execution(address(account), 0, hex"");
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.ForbiddenTarget.selector, address(account)),
            BATCH_MODE,
            batchOf(executions)
        );
        require(sink.pings() == 0, "the allowed call must not have run");
    }

    function testNativeValueWithinTheCapIsSent() public {
        runSingle(sendValue(0.4 ether));
        require(address(sink).balance == 0.4 ether, "value must arrive");
        require(hook.spentInWindow(address(account), address(0)) == 0.4 ether, "spend must be recorded");
    }

    function testNativeValueAboveTheCapIsRefused() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.CapExceeded.selector, address(0), 1 ether + 1, NATIVE_CAP),
            SINGLE_MODE,
            sendValue(1 ether + 1)
        );
    }

    function testASpendExactlyAtTheCapIsAllowed() public {
        runSingle(sendValue(NATIVE_CAP));
        require(address(sink).balance == NATIVE_CAP, "full cap must be spendable once");
    }

    function testSplittingAcrossOperationsCannotBeatTheCap() public {
        runSingle(sendValue(0.6 ether));
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.CapExceeded.selector, address(0), 0.5 ether, 0.4 ether),
            SINGLE_MODE,
            sendValue(0.5 ether)
        );
    }

    function testSplittingAcrossABatchCannotBeatTheCap() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.CapExceeded.selector, address(0), 1.2 ether, NATIVE_CAP),
            BATCH_MODE,
            twoValueTransfers(0.6 ether, 0.6 ether)
        );
    }

    function testTheWindowRollsInsteadOfResettingAtAFixedHour() public {
        runSingle(sendValue(0.6 ether));
        VM.warp(START + 12 hours);
        runSingle(sendValue(0.4 ether));
        VM.warp(START + ONE_DAY - 1);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.CapExceeded.selector, address(0), 0.1 ether, 0),
            SINGLE_MODE,
            sendValue(0.1 ether)
        );
        VM.warp(START + ONE_DAY);
        runSingle(sendValue(0.6 ether));
        require(
            hook.spentInWindow(address(account), address(0)) == 1 ether, "second window must hold both later spends"
        );
    }

    function testSpendsOlderThanTheWindowStopCounting() public {
        runSingle(sendValue(NATIVE_CAP));
        VM.warp(START + ONE_DAY);
        require(hook.spentInWindow(address(account), address(0)) == 0, "expired spend must not count");
        runSingle(sendValue(NATIVE_CAP));
    }

    function testManySmallSpendsInTheSameBlockShareOneWindowEvent() public {
        for (uint256 index = 0; index < 40; index++) {
            runSingle(sendValue(1 gwei));
        }
        require(hook.spentInWindow(address(account), address(0)) == 40 gwei, "all spends must count");
    }

    function testAFullWindowRefusesFurtherSpendsInsteadOfForgettingThem() public {
        for (uint256 index = 0; index < 32; index++) {
            VM.warp(START + index);
            runSingle(sendValue(1 gwei));
        }
        VM.warp(START + 32);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.WindowFull.selector, address(0)), SINGLE_MODE, sendValue(1 gwei)
        );
        VM.warp(START + ONE_DAY + 1);
        runSingle(sendValue(1 gwei));
    }

    function testTokenTransferWithinTheCapIsCountedAndSent() public {
        runSingle(tokenTransfer(400e6));
        require(token.balanceOf(RECIPIENT) == 400e6, "tokens must arrive");
        require(hook.spentInWindow(address(account), address(token)) == 400e6, "spend must be recorded");
    }

    function testTokenTransferAboveTheCapIsRefused() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.CapExceeded.selector, address(token), TOKEN_CAP + 1, TOKEN_CAP),
            SINGLE_MODE,
            tokenTransfer(TOKEN_CAP + 1)
        );
    }

    function testTokenApprovalCountsAgainstTheCap() public {
        bytes memory approval = single(address(token), 0, abi.encodeCall(SimpleToken.approve, (RECIPIENT, 700e6)));
        runSingle(approval);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.CapExceeded.selector, address(token), 400e6, 300e6),
            SINGLE_MODE,
            tokenTransfer(400e6)
        );
    }

    function testUnlimitedApprovalIsRefused() public {
        bytes memory approval =
            single(address(token), 0, abi.encodeCall(SimpleToken.approve, (RECIPIENT, type(uint256).max)));
        VM.expectRevert();
        run(SINGLE_MODE, approval);
    }

    function testOtherTokenFunctionsAreRefused() public {
        bytes memory pull =
            single(address(token), 0, abi.encodeCall(SimpleToken.transferFrom, (address(this), RECIPIENT, 1)));
        expectFailure(
            abi.encodeWithSelector(
                FintechPolicyHook.UnsupportedTokenCall.selector, address(token), SimpleToken.transferFrom.selector
            ),
            SINGLE_MODE,
            pull
        );
    }

    function testTokenCallsWithTrailingBytesAreRefused() public {
        bytes memory padded = abi.encodePacked(abi.encodeCall(SimpleToken.transfer, (RECIPIENT, 1)), uint8(0));
        expectFailure(
            abi.encodeWithSelector(
                FintechPolicyHook.UnsupportedTokenCall.selector, address(token), SimpleToken.transfer.selector
            ),
            SINGLE_MODE,
            single(address(token), 0, padded)
        );
    }

    function testTokenCallsWithoutADataSelectorAreRefused() public {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.UnsupportedTokenCall.selector, address(token), bytes4(0)),
            SINGLE_MODE,
            single(address(token), 0, hex"")
        );
    }

    function testNativeValueSentToATokenIsRefused() public {
        bytes memory execution = single(address(token), 1 wei, abi.encodeCall(SimpleToken.transfer, (RECIPIENT, 1)));
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.AssetNotAllowed.selector, address(token)), SINGLE_MODE, execution
        );
    }

    function testValueIsRefusedWhenNoNativeCapIsConfigured() public {
        VM.prank(address(account));
        hook.setCap(address(0), 0);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.AssetNotAllowed.selector, address(0)), SINGLE_MODE, sendValue(1)
        );
        runSingle(pingExecution());
    }

    function testAllowedTargetCannotDrainATrackedTokenThroughAnAllowance() public {
        VM.prank(address(account));
        token.approve(address(drainer), 500e6);
        bytes memory execution =
            single(address(drainer), 0, abi.encodeCall(AllowanceDrainer.drain, (token, address(account), 500e6)));
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.BalanceDropExceedsDeclaration.selector, address(token), 500e6, 0),
            SINGLE_MODE,
            execution
        );
        require(token.balanceOf(address(drainer)) == 0, "the drain must have been reverted");
    }

    function testDeclaredTransferMayLowerTheBalanceByTheDeclaredAmount() public {
        uint256 before = token.balanceOf(address(account));
        runSingle(tokenTransfer(250e6));
        require(before - token.balanceOf(address(account)) == 250e6, "balance must drop by exactly the declared amount");
    }

    function testNonSessionCallsPassThroughWithoutEffect() public {
        VM.prank(address(account));
        require(hook.preCheck(OWNER, 0, hex"").length == 0, "empty data");
        VM.prank(address(account));
        require(hook.preCheck(OWNER, 0, hex"aabbcc").length == 0, "short data");
        VM.prank(address(account));
        require(
            hook.preCheck(OWNER, 0, abi.encodeWithSignature("execute(bytes32,bytes)", SINGLE_MODE, hex"")).length == 0,
            "execute"
        );
        VM.prank(address(account));
        hook.postCheck(hex"");
    }

    function testAnAccountWithoutAPolicyCannotRunSessionCalls() public {
        bytes memory messageData =
            abi.encodeWithSignature("executeAsSession(bytes32,bytes)", SINGLE_MODE, pingExecution());
        VM.prank(RECIPIENT);
        VM.expectRevert(abi.encodeWithSelector(FintechPolicyHook.NotConfigured.selector, RECIPIENT));
        hook.preCheck(ENTRY_POINT, 0, messageData);
    }

    function testPostCheckRejectsAContextFromAnotherAccount() public {
        address[] memory assets = new address[](0);
        bytes memory context = abi.encode(RECIPIENT, assets, new uint256[](0), new uint256[](0));
        VM.expectRevert(abi.encodeWithSelector(FintechPolicyHook.CallerIsNotTheContextAccount.selector, address(this)));
        hook.postCheck(context);
    }

    function testPostCheckRejectsInconsistentContexts() public {
        address[] memory assets = new address[](1);
        bytes memory context = abi.encode(address(this), assets, new uint256[](0), new uint256[](1));
        VM.expectRevert(FintechPolicyHook.InvalidContext.selector);
        hook.postCheck(context);
    }

    function testInstallingTwiceIsRefused() public {
        VM.prank(address(account));
        VM.expectRevert(FintechPolicyHook.AlreadyConfigured.selector);
        hook.onInstall(configuration(NATIVE_CAP, TOKEN_CAP));
    }

    function testOnlyTheHookTypeIsReported() public view {
        require(hook.isModuleType(HOOK_TYPE), "hook type");
        require(!hook.isModuleType(1), "validator type");
        require(!hook.isModuleType(2), "executor type");
        require(hook.isInitialized(address(account)), "configured");
    }

    function testUninstallClearsThePolicyAndNeverReverts() public {
        runSingle(sendValue(0.5 ether));
        VM.prank(address(account));
        account.uninstallModule(HOOK_TYPE, address(hook), hex"");
        require(!hook.isInitialized(address(account)), "policy must be cleared");
        require(!hook.isTargetAllowed(address(account), address(sink)), "allowlist must be cleared");
        require(hook.capOf(address(account), address(0)) == 0, "caps must be cleared");
        VM.prank(address(account));
        hook.onUninstall(hex"");
        VM.prank(address(account));
        hook.onUninstall(hex"ff");
    }

    function testUninstallingTheHookAlsoStopsSessionExecution() public {
        VM.prank(address(account));
        account.uninstallModule(HOOK_TYPE, address(hook), hex"");
        VM.prank(ENTRY_POINT);
        VM.expectRevert(FintechModularAccount.PolicyHookNotInstalled.selector);
        account.executeAsSession(SINGLE_MODE, pingExecution());
    }

    function testReinstallingAfterUninstallStartsFromANewPolicy() public {
        VM.prank(address(account));
        account.uninstallModule(HOOK_TYPE, address(hook), hex"");
        installHook(configuration(2 ether, TOKEN_CAP));
        require(hook.capOf(address(account), address(0)) == 2 ether, "new cap");
        runSingle(sendValue(2 ether));
    }

    function testOnlyTheAccountItselfChangesItsOwnPolicy() public {
        VM.prank(address(account));
        hook.allowTarget(RECIPIENT);
        require(hook.isTargetAllowed(address(account), RECIPIENT), "target allowed");
        VM.prank(address(account));
        hook.revokeTarget(RECIPIENT);
        require(!hook.isTargetAllowed(address(account), RECIPIENT), "target revoked");
        VM.prank(RECIPIENT);
        VM.expectRevert(abi.encodeWithSelector(FintechPolicyHook.NotConfigured.selector, RECIPIENT));
        hook.allowTarget(address(0x1234));
    }

    function testCapsCanBeLoweredAndRemoved() public {
        VM.prank(address(account));
        hook.setCap(address(0), 0.1 ether);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.CapExceeded.selector, address(0), 0.2 ether, 0.1 ether),
            SINGLE_MODE,
            sendValue(0.2 ether)
        );
        VM.prank(address(account));
        hook.setCap(address(token), 0);
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.TargetNotAllowed.selector, address(token)),
            SINGLE_MODE,
            tokenTransfer(1)
        );
    }

    function testInvalidConfigurationsAreRefused() public {
        VM.startPrank(address(account));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowTarget(address(0));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowTarget(address(account));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowTarget(address(hook));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowTarget(ENTRY_POINT);
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowTarget(address(token));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.setCap(address(sink), 1);
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.setCap(address(0x9999), uint256(type(uint208).max) + 1);
        VM.stopPrank();
    }

    function testAtMostEightAssetsCanBeTracked() public {
        VM.startPrank(address(account));
        for (uint256 index = 0; index < 6; index++) {
            hook.setCap(address(uint160(0x7000 + index)), 1);
        }
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.setCap(address(0x8000), 1);
        VM.stopPrank();
    }

    function testCappingTheSameAssetTwiceUpdatesItWithoutAddingAnEntry() public {
        VM.startPrank(address(account));
        for (uint256 index = 0; index < 20; index++) {
            hook.setCap(address(token), 500e6 + index);
        }
        VM.stopPrank();
        require(hook.capOf(address(account), address(token)) == 500e6 + 19, "latest cap wins");
    }

    function testNonCanonicalBatchEncodingIsAccountedForExactlyAsItIsExecuted() public {
        Execution[] memory executions = new Execution[](1);
        executions[0] = Execution(address(sink), 0.3 ether, "");
        bytes memory canonical = abi.encode(executions);
        bytes memory shifted = abi.encodePacked(uint256(0x40), uint256(0xdeadbeef), sliceFrom(canonical, 0x20));
        run(BATCH_MODE, shifted);
        require(address(sink).balance == 0.3 ether, "the shifted batch must execute");
        require(hook.spentInWindow(address(account), address(0)) == 0.3 ether, "the same value must be counted");
    }

    function testNonCanonicalOuterEncodingIsAccountedForExactlyAsItIsExecuted() public {
        bytes memory execution = sendValue(0.3 ether);
        bytes memory encoded = abi.encodePacked(
            ISessionExecutionSelector.executeAsSessionSelector(),
            SINGLE_MODE,
            uint256(0x60),
            uint256(0xdeadbeef),
            uint256(execution.length),
            execution,
            new bytes((32 - execution.length % 32) % 32)
        );
        VM.prank(ENTRY_POINT);
        (bool succeeded,) = address(account).call(encoded);
        require(succeeded, "the shifted outer encoding must execute");
        require(address(sink).balance == 0.3 ether, "value must arrive");
        require(hook.spentInWindow(address(account), address(0)) == 0.3 ether, "the same value must be counted");
    }

    function sliceFrom(bytes memory data, uint256 start) private pure returns (bytes memory result) {
        result = new bytes(data.length - start);
        for (uint256 index = 0; index < result.length; index++) {
            result[index] = data[start + index];
        }
    }

    function expectSanctioned(address counterparty, bytes memory executionCalldata) private {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.SanctionedAddress.selector, counterparty),
            SINGLE_MODE,
            executionCalldata
        );
    }

    function expectUnavailable(bytes memory executionCalldata) private {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.OracleUnavailable.selector), SINGLE_MODE, executionCalldata
        );
    }

    function approval(uint256 amount) private view returns (bytes memory) {
        return single(address(token), 0, abi.encodeCall(SimpleToken.approve, (RECIPIENT, amount)));
    }

    function testASanctionedAllowlistedTargetIsRefused() public {
        oracle.setSanctioned(address(sink), true);
        expectSanctioned(address(sink), pingExecution());
        expectSanctioned(address(sink), sendValue(0.1 ether));
        require(sink.pings() == 0, "nothing may execute");
        require(hook.spentInWindow(address(account), address(0)) == 0, "the window must not be charged");
    }

    function testListingAndDelistingTakeEffectImmediately() public {
        runSingle(pingExecution());
        oracle.setSanctioned(address(sink), true);
        expectSanctioned(address(sink), pingExecution());
        oracle.setSanctioned(address(sink), false);
        runSingle(pingExecution());
        require(sink.pings() == 2, "the delisted target works again");
    }

    function testATokenTransferToASanctionedRecipientIsRefused() public {
        oracle.setSanctioned(RECIPIENT, true);
        expectSanctioned(RECIPIENT, tokenTransfer(1e6));
        require(token.balanceOf(RECIPIENT) == 0, "no tokens may move");
        require(hook.spentInWindow(address(account), address(token)) == 0, "the window must not be charged");
    }

    function testATokenApprovalForASanctionedSpenderIsRefused() public {
        oracle.setSanctioned(RECIPIENT, true);
        expectSanctioned(RECIPIENT, approval(1e6));
    }

    function testAnUnlistedRecipientIsNotAffectedByAListedOne() public {
        oracle.setSanctioned(address(0xDEAD), true);
        runSingle(tokenTransfer(1e6));
        require(token.balanceOf(RECIPIENT) == 1e6, "the transfer must pass");
    }

    function testASanctionedTokenContractIsRefused() public {
        oracle.setSanctioned(address(token), true);
        expectSanctioned(address(token), tokenTransfer(1e6));
    }

    function testOneSanctionedExecutionRefusesTheWholeBatch() public {
        oracle.setSanctioned(RECIPIENT, true);
        Execution[] memory executions = new Execution[](2);
        executions[0] = Execution(address(sink), 0, abi.encodeCall(Sink.ping, ()));
        executions[1] = Execution(address(token), 0, abi.encodeCall(SimpleToken.transfer, (RECIPIENT, 1e6)));
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.SanctionedAddress.selector, RECIPIENT),
            BATCH_MODE,
            batchOf(executions)
        );
        require(sink.pings() == 0, "the first execution must not survive");
    }

    function testASanctionedTargetIsReportedBeforeTheAllowlistCheck() public {
        oracle.setSanctioned(RECIPIENT, true);
        expectSanctioned(RECIPIENT, single(RECIPIENT, 0, ""));
    }

    function testATokenCallWithADirtyAddressWordIsRefused() public {
        bytes memory dirty = abi.encodePacked(
            SimpleToken.transfer.selector, bytes32(uint256(uint160(RECIPIENT)) | (uint256(1) << 200)), uint256(1)
        );
        expectFailure(
            abi.encodeWithSelector(
                FintechPolicyHook.UnsupportedTokenCall.selector, address(token), SimpleToken.transfer.selector
            ),
            SINGLE_MODE,
            single(address(token), 0, dirty)
        );
    }

    function testAnOracleOutageRefusesSessionExecutionAndChargesNothing() public {
        oracle.setUnavailable(true);
        expectUnavailable(pingExecution());
        expectUnavailable(sendValue(0.1 ether));
        expectUnavailable(tokenTransfer(1e6));
        require(sink.pings() == 0, "nothing may execute");
        require(hook.spentInWindow(address(account), address(0)) == 0, "the window must not be charged");
        oracle.setUnavailable(false);
        runSingle(pingExecution());
    }

    function testAnOracleThatRunsOutOfGasIsUnavailable() public {
        VM.etch(address(oracle), address(new GasBurningOracle()).code);
        expectUnavailable(pingExecution());
    }

    function testAnOracleThatRevertsWithoutDataIsUnavailable() public {
        VM.etch(address(oracle), address(new SilentlyRevertingOracle()).code);
        expectUnavailable(pingExecution());
    }

    function testAnOracleThatAnswersWithAnInvalidBooleanFailsClosed() public {
        VM.etch(address(oracle), address(new ReturnsTwoOracle()).code);
        VM.prank(ENTRY_POINT);
        VM.expectRevert();
        account.executeAsSession(SINGLE_MODE, pingExecution());
        require(sink.pings() == 0, "nothing may execute");
    }

    function testAnOracleThatAnswersWithNothingFailsClosed() public {
        VM.etch(address(oracle), address(new ReturnsNothingOracle()).code);
        VM.prank(ENTRY_POINT);
        VM.expectRevert();
        account.executeAsSession(SINGLE_MODE, pingExecution());
    }

    function testAnOracleWithoutCodeFailsClosed() public {
        VM.etch(address(oracle), hex"");
        VM.prank(ENTRY_POINT);
        VM.expectRevert();
        account.executeAsSession(SINGLE_MODE, pingExecution());
        require(sink.pings() == 0, "nothing may execute");
    }

    function testOwnerOperationsDoNotDependOnTheOracle() public {
        oracle.setUnavailable(true);
        VM.prank(ENTRY_POINT);
        account.execute(SINGLE_MODE, pingExecution());
        require(sink.pings() == 1, "the owner path must keep working");
    }

    function testTheHookCanBeUninstalledDuringAnOracleOutage() public {
        oracle.setUnavailable(true);
        VM.prank(address(account));
        account.uninstallModule(HOOK_TYPE, address(hook), hex"");
        require(!hook.isInitialized(address(account)), "the policy must be cleared");
    }

    function testTheHookRefusesAnOracleWithoutCode() public {
        VM.expectRevert(FintechPolicyHook.InvalidOracle.selector);
        new FintechPolicyHook(ISanctionsOracle(address(0xDEAD)));
        VM.expectRevert(FintechPolicyHook.InvalidOracle.selector);
        new FintechPolicyHook(ISanctionsOracle(address(0)));
    }

    function testTheOracleIsFixedAtDeployment() public view {
        require(address(hook.oracle()) == address(oracle), "the oracle must be the deployed one");
    }

    function paymentConfiguration() private view returns (bytes memory) {
        address[] memory targets = new address[](2);
        targets[0] = address(sink);
        targets[1] = address(drainer);
        FintechPolicyHook.AssetRule[] memory rules = new FintechPolicyHook.AssetRule[](2);
        rules[0] = FintechPolicyHook.AssetRule(address(0), NATIVE_CAP);
        rules[1] = FintechPolicyHook.AssetRule(address(token), TOKEN_CAP);
        address[] memory approved = new address[](2);
        approved[0] = address(sink);
        approved[1] = RECIPIENT;
        return abi.encode(targets, rules, approved, true);
    }

    function installPaymentPolicy() private {
        VM.prank(address(account));
        account.uninstallModule(HOOK_TYPE, address(hook), hex"");
        installHook(paymentConfiguration());
    }

    function transferTo(address recipient, uint256 amount) private view returns (bytes memory) {
        return single(address(token), 0, abi.encodeCall(SimpleToken.transfer, (recipient, amount)));
    }

    function expectPayeeRefused(address payee, bytes memory executionCalldata) private {
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.PayeeNotAllowed.selector, payee), SINGLE_MODE, executionCalldata
        );
    }

    function testAPaymentScopeIsDeclaredAtInstallation() public {
        require(!hook.isPaymentScope(address(account)), "the default scope is not a payment scope");
        installPaymentPolicy();
        require(hook.isPaymentScope(address(account)), "the payment scope is declared");
        require(hook.isPayeeAllowed(address(account), RECIPIENT), "the payee is approved");
        require(!hook.isPayeeAllowed(address(account), address(0xDEAD)), "a stranger is not a payee");
    }

    function testAPaymentScopeRefusesATokenTransferToAnUnapprovedPayee() public {
        installPaymentPolicy();
        expectPayeeRefused(address(0xDEAD), transferTo(address(0xDEAD), 1e6));
        require(token.balanceOf(address(0xDEAD)) == 0, "no tokens may move");
        require(hook.spentInWindow(address(account), address(token)) == 0, "the window must not be charged");
    }

    function testAPaymentScopeRefusesAValueTransferToAnAllowlistedNonPayee() public {
        installPaymentPolicy();
        require(hook.isTargetAllowed(address(account), address(drainer)), "the target is allowlisted");
        expectPayeeRefused(address(drainer), single(address(drainer), 0.1 ether, ""));
        require(address(drainer).balance == 0, "no value may move");
    }

    function testAPaymentScopeRefusesAnApprovalEvenToAnApprovedPayee() public {
        installPaymentPolicy();
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.ApprovalNotAllowed.selector, address(token)),
            SINGLE_MODE,
            approval(1e6)
        );
        require(token.allowance(address(account), RECIPIENT) == 0, "no allowance may be granted");
    }

    function testAPaymentScopeRefusesCallDataOnAnApprovedPayee() public {
        installPaymentPolicy();
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.CallDataNotAllowed.selector, address(sink)),
            SINGLE_MODE,
            pingExecution()
        );
        require(sink.pings() == 0, "nothing may execute");
    }

    function testAPaymentScopeAllowsValueOnlyPaymentsAndTokenTransfersToPayees() public {
        installPaymentPolicy();
        runSingle(sendValue(0.1 ether));
        runSingle(tokenTransfer(1e6));
        require(address(sink).balance == 0.1 ether, "the payee was paid");
        require(token.balanceOf(RECIPIENT) == 1e6, "the token payee was paid");
        require(hook.spentInWindow(address(account), address(0)) == 0.1 ether, "the window is charged");
    }

    function testASanctionedPayeeIsRefusedBeforeThePayeeCheck() public {
        installPaymentPolicy();
        oracle.setSanctioned(RECIPIENT, true);
        expectSanctioned(RECIPIENT, tokenTransfer(1e6));
        oracle.setSanctioned(address(0xDEAD), true);
        expectSanctioned(address(0xDEAD), transferTo(address(0xDEAD), 1e6));
    }

    function testOnePaymentToAnUnapprovedPayeeRefusesTheWholeBatch() public {
        installPaymentPolicy();
        Execution[] memory executions = new Execution[](2);
        executions[0] = Execution(address(sink), 0.1 ether, "");
        executions[1] = Execution(address(token), 0, abi.encodeCall(SimpleToken.transfer, (address(0xDEAD), 1e6)));
        expectFailure(
            abi.encodeWithSelector(FintechPolicyHook.PayeeNotAllowed.selector, address(0xDEAD)),
            BATCH_MODE,
            batchOf(executions)
        );
        require(address(sink).balance == 0, "the first payment must not survive");
    }

    function testTheAccountCanChangeItsOwnPayeeAllowlist() public {
        installPaymentPolicy();
        VM.prank(address(account));
        hook.revokePayee(RECIPIENT);
        expectPayeeRefused(RECIPIENT, tokenTransfer(1e6));
        VM.prank(address(account));
        hook.allowPayee(RECIPIENT);
        runSingle(tokenTransfer(1e6));
        require(token.balanceOf(RECIPIENT) == 1e6, "the restored payee is paid");
    }

    function testOnlyTheAccountChangesItsOwnPayees() public {
        installPaymentPolicy();
        VM.prank(RECIPIENT);
        VM.expectRevert(abi.encodeWithSelector(FintechPolicyHook.NotConfigured.selector, RECIPIENT));
        hook.allowPayee(address(0x1234));
    }

    function testInfrastructureCannotBeApprovedAsAPayee() public {
        installPaymentPolicy();
        VM.startPrank(address(account));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowPayee(address(0));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowPayee(address(account));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowPayee(address(hook));
        VM.expectRevert(FintechPolicyHook.InvalidConfiguration.selector);
        hook.allowPayee(ENTRY_POINT);
        VM.stopPrank();
    }

    function testAPaymentPolicyWithoutPayeesCanPayNobody() public {
        VM.prank(address(account));
        account.uninstallModule(HOOK_TYPE, address(hook), hex"");
        address[] memory targets = new address[](1);
        targets[0] = address(sink);
        FintechPolicyHook.AssetRule[] memory rules = new FintechPolicyHook.AssetRule[](1);
        rules[0] = FintechPolicyHook.AssetRule(address(0), NATIVE_CAP);
        installHook(abi.encode(targets, rules, payees(), true));
        expectPayeeRefused(address(sink), sendValue(0.1 ether));
    }

    function testAPaymentScopeCanBeUninstalledAndIsThenGone() public {
        installPaymentPolicy();
        VM.prank(address(account));
        account.uninstallModule(HOOK_TYPE, address(hook), hex"");
        require(!hook.isInitialized(address(account)), "the policy must be cleared");
        require(!hook.isPaymentScope(address(account)), "the payment scope must be cleared");
        require(!hook.isPayeeAllowed(address(account), RECIPIENT), "the payees must be cleared");
    }

    function testANonPaymentScopeKeepsItsWiderPowers() public {
        require(!hook.isPaymentScope(address(account)), "the default scope is not a payment scope");
        runSingle(pingExecution());
        runSingle(approval(1e6));
        require(sink.pings() == 1, "call data is allowed outside a payment scope");
        require(token.allowance(address(account), RECIPIENT) == 1e6, "approvals are allowed outside it");
    }
}

library ISessionExecutionSelector {
    function executeAsSessionSelector() internal pure returns (bytes4) {
        return bytes4(keccak256("executeAsSession(bytes32,bytes)"));
    }
}

contract GasBurningOracle {
    fallback() external {
        while (true) {}
    }
}

contract SilentlyRevertingOracle {
    fallback() external {
        assembly {
            revert(0, 0)
        }
    }
}

contract ReturnsTwoOracle {
    fallback() external {
        assembly {
            mstore(0, 2)
            return(0, 32)
        }
    }
}

contract ReturnsNothingOracle {
    fallback() external {
        assembly {
            return(0, 0)
        }
    }
}
