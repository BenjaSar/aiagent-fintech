// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

interface ISessionExecution {
    function executeAsSession(bytes32 mode, bytes calldata executionCalldata) external;
}
