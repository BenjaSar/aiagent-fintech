// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ISanctionsOracle} from "../interfaces/ISanctionsOracle.sol";

contract DevelopmentSanctionsOracle is ISanctionsOracle {
    mapping(address counterparty => bool listed) private listed;
    bool public unavailable;

    error OracleDown();

    function setSanctioned(address counterparty, bool value) external {
        listed[counterparty] = value;
    }

    function setUnavailable(bool value) external {
        unavailable = value;
    }

    function isSanctioned(address counterparty) external view returns (bool) {
        if (unavailable) revert OracleDown();
        return listed[counterparty];
    }
}
