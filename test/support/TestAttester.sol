// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FintechModuleRegistry} from "../../contracts/FintechModuleRegistry.sol";

uint256 constant VALIDATOR_TYPES = 1 << 1;
uint256 constant EXECUTOR_TYPES = 1 << 2;
uint256 constant HOOK_TYPES = 1 << 4;

contract TestAttester {
    function attest(FintechModuleRegistry registry, address module, uint256 moduleTypes) external {
        registry.attest(module, moduleTypes, 0);
    }

    function revoke(FintechModuleRegistry registry, address module) external {
        registry.revoke(module);
    }
}
