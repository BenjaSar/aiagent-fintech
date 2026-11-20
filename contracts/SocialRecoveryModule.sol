// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {MODULE_TYPE_EXECUTOR} from "@openzeppelin/contracts/interfaces/draft-IERC7579.sol";

interface IRotatableOwnerAccount {
    function rotateOwner(address newOwner) external;
}

contract SocialRecoveryModule {
    struct Configuration {
        uint256 threshold;
        uint256 delay;
        address[] guardians;
    }

    uint256 public constant MINIMUM_DELAY = 1 hours;

    mapping(address account => Configuration configuration) private configurations;
    mapping(address account => mapping(address guardian => bool isGuardian)) private guardianFlags;
    mapping(address account => uint256 round) private rounds;
    mapping(bytes32 proposal => mapping(address guardian => bool approved)) private approvals;
    mapping(bytes32 proposal => uint256 count) private approvalCounts;
    mapping(bytes32 proposal => uint256 readyAt) private readyTimes;

    event GuardiansConfigured(address indexed account, address[] guardians, uint256 threshold, uint256 delay);
    event GuardiansCleared(address indexed account);
    event RecoveryApproved(address indexed account, address indexed newOwner, address indexed guardian);
    event RecoveryScheduled(address indexed account, address indexed newOwner, uint256 readyAt);
    event RecoveryCancelled(address indexed account);
    event RecoveryExecuted(address indexed account, address indexed newOwner);

    error AlreadyConfigured();
    error InvalidConfiguration();
    error NotAGuardian();
    error AlreadyApproved();
    error InvalidNewOwner();
    error RecoveryNotReady();

    function onInstall(bytes calldata data) external {
        if (configurations[msg.sender].threshold != 0) revert AlreadyConfigured();
        (address[] memory guardians, uint256 threshold, uint256 delay) = abi.decode(data, (address[], uint256, uint256));
        if (threshold == 0 || threshold > guardians.length || delay < MINIMUM_DELAY) revert InvalidConfiguration();
        for (uint256 index = 0; index < guardians.length; index++) {
            address guardian = guardians[index];
            if (guardian == address(0) || guardian == msg.sender || guardianFlags[msg.sender][guardian]) {
                revert InvalidConfiguration();
            }
            guardianFlags[msg.sender][guardian] = true;
        }
        configurations[msg.sender] = Configuration(threshold, delay, guardians);
        emit GuardiansConfigured(msg.sender, guardians, threshold, delay);
    }

    function onUninstall(bytes calldata) external {
        address[] storage guardians = configurations[msg.sender].guardians;
        for (uint256 index = 0; index < guardians.length; index++) {
            delete guardianFlags[msg.sender][guardians[index]];
        }
        delete configurations[msg.sender];
        rounds[msg.sender] += 1;
        emit GuardiansCleared(msg.sender);
    }

    function isModuleType(uint256 moduleTypeId) external pure returns (bool) {
        return moduleTypeId == MODULE_TYPE_EXECUTOR;
    }

    function approveRecovery(address account, address newOwner) external {
        if (!guardianFlags[account][msg.sender]) revert NotAGuardian();
        if (newOwner == address(0)) revert InvalidNewOwner();
        bytes32 proposal = _proposalOf(account, newOwner);
        if (approvals[proposal][msg.sender]) revert AlreadyApproved();
        approvals[proposal][msg.sender] = true;
        uint256 count = approvalCounts[proposal] + 1;
        approvalCounts[proposal] = count;
        emit RecoveryApproved(account, newOwner, msg.sender);
        if (count == configurations[account].threshold) {
            uint256 readyAt = block.timestamp + configurations[account].delay;
            readyTimes[proposal] = readyAt;
            emit RecoveryScheduled(account, newOwner, readyAt);
        }
    }

    function cancelRecovery() external {
        rounds[msg.sender] += 1;
        emit RecoveryCancelled(msg.sender);
    }

    function executeRecovery(address account, address newOwner) external {
        bytes32 proposal = _proposalOf(account, newOwner);
        uint256 readyAt = readyTimes[proposal];
        if (readyAt == 0 || block.timestamp < readyAt) revert RecoveryNotReady();
        rounds[account] += 1;
        IRotatableOwnerAccount(account).rotateOwner(newOwner);
        emit RecoveryExecuted(account, newOwner);
    }

    function guardiansOf(address account) external view returns (address[] memory) {
        return configurations[account].guardians;
    }

    function thresholdOf(address account) external view returns (uint256) {
        return configurations[account].threshold;
    }

    function delayOf(address account) external view returns (uint256) {
        return configurations[account].delay;
    }

    function readyAtOf(address account, address newOwner) external view returns (uint256) {
        return readyTimes[_proposalOf(account, newOwner)];
    }

    function _proposalOf(address account, address newOwner) private view returns (bytes32) {
        return keccak256(abi.encode(account, newOwner, rounds[account]));
    }
}
