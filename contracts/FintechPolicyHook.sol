// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/interfaces/IERC20.sol";
import {ERC4337Utils} from "@openzeppelin/contracts/account/utils/draft-ERC4337Utils.sol";
import {
    ERC7579Utils,
    Mode,
    CallType,
    ExecType,
    ModeSelector,
    ModePayload,
    eqCallType,
    eqExecType,
    eqModeSelector,
    eqModePayload
} from "@openzeppelin/contracts/account/utils/draft-ERC7579Utils.sol";
import {Execution, IERC7579Hook, MODULE_TYPE_HOOK} from "@openzeppelin/contracts/interfaces/draft-IERC7579.sol";
import {ISanctionsOracle} from "./interfaces/ISanctionsOracle.sol";
import {ISessionExecution} from "./interfaces/ISessionExecution.sol";

contract FintechPolicyHook is IERC7579Hook {
    struct AssetRule {
        address asset;
        uint256 cap;
    }

    struct Spending {
        address[] assets;
        uint256[] windowAmounts;
        uint256[] outflows;
    }

    struct Movement {
        address asset;
        uint256 amount;
        bool leavesAccount;
    }

    struct Window {
        uint64 head;
        uint64 tail;
    }

    address public constant NATIVE_ASSET = address(0);
    uint256 public constant WINDOW_SECONDS = 1 days;
    uint256 public constant MAXIMUM_ASSETS = 8;
    uint256 public constant MAXIMUM_EXECUTIONS = 16;
    uint256 public constant MAXIMUM_WINDOW_EVENTS = 32;
    uint256 private constant TOKEN_CALL_LENGTH = 68;
    uint256 private constant AMOUNT_BITS = 208;
    uint256 private constant ORACLE_GAS = 150_000;
    uint256 private constant ADDRESS_BITS = 160;

    ISanctionsOracle public immutable oracle;

    mapping(address account => uint256 epoch) private epochs;
    mapping(bytes32 scope => bool configured) private configuredScopes;
    mapping(bytes32 scope => mapping(address target => bool allowed)) private allowedTargets;
    mapping(bytes32 scope => mapping(address asset => uint256 cap)) private caps;
    mapping(bytes32 scope => address[] assets) private assetLists;
    mapping(bytes32 scope => mapping(address asset => Window window)) private windows;
    mapping(bytes32 scope => mapping(address asset => mapping(uint256 position => uint256 packed))) private events;

    event PolicyConfigured(address indexed account, address[] targets, AssetRule[] rules);
    event PolicyCleared(address indexed account);
    event TargetAllowed(address indexed account, address indexed target);
    event TargetRevoked(address indexed account, address indexed target);
    event CapSet(address indexed account, address indexed asset, uint256 cap);

    error AlreadyConfigured();
    error NotConfigured(address account);
    error InvalidConfiguration();
    error UnsupportedMode(bytes32 mode);
    error EmptyExecution();
    error TooManyExecutions(uint256 count);
    error ForbiddenTarget(address target);
    error TargetNotAllowed(address target);
    error AssetNotAllowed(address asset);
    error UnsupportedTokenCall(address token, bytes4 selector);
    error CapExceeded(address asset, uint256 requested, uint256 remaining);
    error WindowFull(address asset);
    error BalanceDropExceedsDeclaration(address asset, uint256 drop, uint256 declared);
    error InvalidContext();
    error InvalidOracle();
    error SanctionedAddress(address counterparty);
    error OracleUnavailable();
    error CallerIsNotTheContextAccount(address caller);

    constructor(ISanctionsOracle oracle_) {
        if (address(oracle_).code.length == 0) revert InvalidOracle();
        oracle = oracle_;
    }

    function onInstall(bytes calldata data) external {
        bytes32 scope = scopeOf(msg.sender);
        if (configuredScopes[scope]) revert AlreadyConfigured();
        (address[] memory targets, AssetRule[] memory rules) = abi.decode(data, (address[], AssetRule[]));
        configuredScopes[scope] = true;
        for (uint256 index = 0; index < targets.length; index++) {
            addTarget(scope, targets[index]);
        }
        for (uint256 index = 0; index < rules.length; index++) {
            addRule(scope, rules[index]);
        }
        emit PolicyConfigured(msg.sender, targets, rules);
    }

    function onUninstall(bytes calldata) external {
        epochs[msg.sender] += 1;
        emit PolicyCleared(msg.sender);
    }

    function isModuleType(uint256 moduleTypeId) external pure returns (bool) {
        return moduleTypeId == MODULE_TYPE_HOOK;
    }

    function isInitialized(address account) external view returns (bool) {
        return configuredScopes[scopeOf(account)];
    }

    function allowTarget(address target) external {
        bytes32 scope = requireConfigured(msg.sender);
        addTarget(scope, target);
        emit TargetAllowed(msg.sender, target);
    }

    function revokeTarget(address target) external {
        bytes32 scope = requireConfigured(msg.sender);
        delete allowedTargets[scope][target];
        emit TargetRevoked(msg.sender, target);
    }

    function setCap(address asset, uint256 cap) external {
        bytes32 scope = requireConfigured(msg.sender);
        if (cap == 0) {
            removeAsset(scope, asset);
        } else {
            addRule(scope, AssetRule(asset, cap));
        }
        emit CapSet(msg.sender, asset, cap);
    }

    function isTargetAllowed(address account, address target) external view returns (bool) {
        return allowedTargets[scopeOf(account)][target];
    }

    function capOf(address account, address asset) external view returns (uint256) {
        return caps[scopeOf(account)][asset];
    }

    function spentInWindow(address account, address asset) external view returns (uint256 spent) {
        bytes32 scope = scopeOf(account);
        Window memory window = windows[scope][asset];
        (, spent) = liveEvents(scope, asset, window);
    }

    function resolveExecutions(bytes32 rawMode, bytes calldata executionCalldata)
        external
        pure
        returns (Execution[] memory executions)
    {
        Mode mode = Mode.wrap(rawMode);
        (CallType callType, ExecType execType, ModeSelector selector, ModePayload payload) =
            ERC7579Utils.decodeMode(mode);
        if (
            !eqExecType(execType, ERC7579Utils.EXECTYPE_DEFAULT)
                || !eqModeSelector(selector, ModeSelector.wrap(bytes4(0)))
                || !eqModePayload(payload, ModePayload.wrap(bytes22(0)))
        ) revert UnsupportedMode(rawMode);
        if (eqCallType(callType, ERC7579Utils.CALLTYPE_SINGLE)) return resolveSingle(executionCalldata);
        if (eqCallType(callType, ERC7579Utils.CALLTYPE_BATCH)) return resolveBatch(executionCalldata);
        revert UnsupportedMode(rawMode);
    }

    function preCheck(address, uint256, bytes calldata messageData) external returns (bytes memory) {
        if (!isSessionExecution(messageData)) return "";
        bytes32 scope = requireConfigured(msg.sender);
        Execution[] memory executions = decodeSessionExecutions(messageData);
        Spending memory spending = measureSpending(scope, executions);
        chargeWindows(scope, spending);
        return abi.encode(msg.sender, spending.assets, balancesOf(msg.sender, spending.assets), spending.outflows);
    }

    function postCheck(bytes calldata hookData) external view {
        if (hookData.length == 0) return;
        (address account, address[] memory assets, uint256[] memory balancesBefore, uint256[] memory declared) =
            abi.decode(hookData, (address, address[], uint256[], uint256[]));
        if (account != msg.sender) revert CallerIsNotTheContextAccount(msg.sender);
        if (assets.length != balancesBefore.length || assets.length != declared.length) revert InvalidContext();
        uint256[] memory afterwards = balancesOf(account, assets);
        for (uint256 index = 0; index < assets.length; index++) {
            requireDropWithinDeclaration(assets[index], balancesBefore[index], afterwards[index], declared[index]);
        }
    }

    function isSessionExecution(bytes calldata messageData) private pure returns (bool) {
        return messageData.length >= 4 && bytes4(messageData) == ISessionExecution.executeAsSession.selector;
    }

    function decodeSessionExecutions(bytes calldata messageData) private view returns (Execution[] memory) {
        (bool resolved, bytes memory returned) =
            address(this).staticcall(bytes.concat(this.resolveExecutions.selector, messageData[4:]));
        if (!resolved) bubble(returned);
        Execution[] memory executions = abi.decode(returned, (Execution[]));
        if (executions.length == 0) revert EmptyExecution();
        if (executions.length > MAXIMUM_EXECUTIONS) revert TooManyExecutions(executions.length);
        return executions;
    }

    function bubble(bytes memory reason) private pure {
        assembly ("memory-safe") {
            revert(add(reason, 0x20), mload(reason))
        }
    }

    function resolveSingle(bytes calldata executionCalldata) private pure returns (Execution[] memory executions) {
        (address target, uint256 value, bytes calldata callData) = ERC7579Utils.decodeSingle(executionCalldata);
        executions = new Execution[](1);
        executions[0] = Execution(target, value, callData);
    }

    function resolveBatch(bytes calldata executionCalldata) private pure returns (Execution[] memory) {
        return ERC7579Utils.decodeBatch(executionCalldata);
    }

    function measureSpending(bytes32 scope, Execution[] memory executions) private view returns (Spending memory) {
        address[] memory assets = assetLists[scope];
        Spending memory spending = Spending(assets, new uint256[](assets.length), new uint256[](assets.length));
        for (uint256 index = 0; index < executions.length; index++) {
            accountFor(scope, spending, executions[index]);
        }
        return spending;
    }

    function accountFor(bytes32 scope, Spending memory spending, Execution memory execution) private view {
        requireNotInfrastructure(execution.target);
        requireNotSanctioned(execution.target);
        if (caps[scope][execution.target] != 0) {
            accountForToken(spending, execution);
            return;
        }
        if (!allowedTargets[scope][execution.target]) revert TargetNotAllowed(execution.target);
        if (execution.value != 0) addMovement(spending, Movement(NATIVE_ASSET, execution.value, true));
    }

    function accountForToken(Spending memory spending, Execution memory execution) private view {
        if (execution.value != 0) revert AssetNotAllowed(execution.target);
        bytes memory callData = execution.callData;
        bytes4 selector = callData.length >= 4 ? bytes4(callData) : bytes4(0);
        if (callData.length != TOKEN_CALL_LENGTH || !isTransferOrApproval(selector)) {
            revert UnsupportedTokenCall(execution.target, selector);
        }
        requireNotSanctioned(tokenCounterparty(execution.target, callData));
        uint256 amount = tokenAmount(callData);
        bool leavesAccount = selector == IERC20.transfer.selector;
        addMovement(spending, Movement(execution.target, amount, leavesAccount));
    }

    function isTransferOrApproval(bytes4 selector) private pure returns (bool) {
        return selector == IERC20.transfer.selector || selector == IERC20.approve.selector;
    }

    function tokenCounterparty(address token, bytes memory callData) private pure returns (address counterparty) {
        uint256 word;
        assembly ("memory-safe") {
            word := mload(add(callData, 0x24))
        }
        if (word >> ADDRESS_BITS != 0) revert UnsupportedTokenCall(token, bytes4(callData));
        counterparty = address(uint160(word));
    }

    function requireNotSanctioned(address counterparty) private view {
        try oracle.isSanctioned{gas: ORACLE_GAS}(counterparty) returns (bool sanctioned) {
            if (sanctioned) revert SanctionedAddress(counterparty);
        } catch {
            revert OracleUnavailable();
        }
    }

    function tokenAmount(bytes memory callData) private pure returns (uint256 amount) {
        assembly ("memory-safe") {
            amount := mload(add(callData, 0x44))
        }
    }

    function addMovement(Spending memory spending, Movement memory movement) private pure {
        for (uint256 index = 0; index < spending.assets.length; index++) {
            if (spending.assets[index] != movement.asset) continue;
            spending.windowAmounts[index] += movement.amount;
            if (movement.leavesAccount) spending.outflows[index] += movement.amount;
            return;
        }
        revert AssetNotAllowed(movement.asset);
    }

    function chargeWindows(bytes32 scope, Spending memory spending) private {
        for (uint256 index = 0; index < spending.assets.length; index++) {
            if (spending.windowAmounts[index] == 0) continue;
            chargeWindow(scope, Movement(spending.assets[index], spending.windowAmounts[index], true));
        }
    }

    function chargeWindow(bytes32 scope, Movement memory charge) private {
        Window memory window = windows[scope][charge.asset];
        (uint64 liveHead, uint256 spent) = liveEvents(scope, charge.asset, window);
        window.head = liveHead;
        uint256 cap = caps[scope][charge.asset];
        if (spent + charge.amount > cap) {
            revert CapExceeded(charge.asset, charge.amount, cap > spent ? cap - spent : 0);
        }
        appendEvent(scope, charge, window);
    }

    function appendEvent(bytes32 scope, Movement memory charge, Window memory window) private {
        uint256 live = window.tail - window.head;
        if (live != 0) {
            uint256 latestPosition = (window.tail - 1) % MAXIMUM_WINDOW_EVENTS;
            uint256 latest = events[scope][charge.asset][latestPosition];
            if (latest >> AMOUNT_BITS == block.timestamp) {
                events[scope][charge.asset][latestPosition] = latest + charge.amount;
                windows[scope][charge.asset] = window;
                return;
            }
        }
        if (live >= MAXIMUM_WINDOW_EVENTS) revert WindowFull(charge.asset);
        events[scope][charge.asset][window.tail % MAXIMUM_WINDOW_EVENTS] =
            (block.timestamp << AMOUNT_BITS) | charge.amount;
        window.tail += 1;
        windows[scope][charge.asset] = window;
    }

    function liveEvents(bytes32 scope, address asset, Window memory window)
        private
        view
        returns (uint64 liveHead, uint256 spent)
    {
        liveHead = window.head;
        uint256 oldestLive = block.timestamp > WINDOW_SECONDS ? block.timestamp - WINDOW_SECONDS : 0;
        while (liveHead < window.tail && eventTime(scope, asset, liveHead) <= oldestLive) {
            liveHead += 1;
        }
        for (uint64 position = liveHead; position < window.tail; position++) {
            spent += eventAmount(scope, asset, position);
        }
    }

    function eventTime(bytes32 scope, address asset, uint64 position) private view returns (uint256) {
        return events[scope][asset][position % MAXIMUM_WINDOW_EVENTS] >> AMOUNT_BITS;
    }

    function eventAmount(bytes32 scope, address asset, uint64 position) private view returns (uint256) {
        return events[scope][asset][position % MAXIMUM_WINDOW_EVENTS] & ((1 << AMOUNT_BITS) - 1);
    }

    function balancesOf(address account, address[] memory assets) private view returns (uint256[] memory balances) {
        balances = new uint256[](assets.length);
        for (uint256 index = 0; index < assets.length; index++) {
            balances[index] = assets[index] == NATIVE_ASSET ? account.balance : IERC20(assets[index]).balanceOf(account);
        }
    }

    function requireDropWithinDeclaration(address asset, uint256 balanceBefore, uint256 balanceAfter, uint256 declared)
        private
        pure
    {
        if (balanceAfter >= balanceBefore) return;
        uint256 drop = balanceBefore - balanceAfter;
        if (drop > declared) revert BalanceDropExceedsDeclaration(asset, drop, declared);
    }

    function requireNotInfrastructure(address target) private view {
        if (
            target == NATIVE_ASSET || target == msg.sender || target == address(this)
                || target == address(ERC4337Utils.ENTRYPOINT_V08)
        ) {
            revert ForbiddenTarget(target);
        }
    }

    function requireConfigured(address account) private view returns (bytes32 scope) {
        scope = scopeOf(account);
        if (!configuredScopes[scope]) revert NotConfigured(account);
    }

    function addTarget(bytes32 scope, address target) private {
        if (target == address(0) || target == msg.sender || target == address(this)) revert InvalidConfiguration();
        if (caps[scope][target] != 0 || target == address(ERC4337Utils.ENTRYPOINT_V08)) revert InvalidConfiguration();
        allowedTargets[scope][target] = true;
    }

    function addRule(bytes32 scope, AssetRule memory rule) private {
        bool alreadyPresent = caps[scope][rule.asset] != 0;
        bool isInvalidAsset = rule.asset == msg.sender || rule.asset == address(this);
        if (rule.cap == 0 || rule.cap > type(uint208).max || isInvalidAsset || allowedTargets[scope][rule.asset]) {
            revert InvalidConfiguration();
        }
        if (!alreadyPresent) {
            if (assetLists[scope].length >= MAXIMUM_ASSETS) revert InvalidConfiguration();
            assetLists[scope].push(rule.asset);
        }
        caps[scope][rule.asset] = rule.cap;
    }

    function removeAsset(bytes32 scope, address asset) private {
        delete caps[scope][asset];
        address[] storage list = assetLists[scope];
        for (uint256 index = 0; index < list.length; index++) {
            if (list[index] != asset) continue;
            list[index] = list[list.length - 1];
            list.pop();
            return;
        }
    }

    function scopeOf(address account) private view returns (bytes32) {
        return keccak256(abi.encode(account, epochs[account]));
    }
}
