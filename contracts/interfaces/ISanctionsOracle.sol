// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

interface ISanctionsOracle {
    function isSanctioned(address counterparty) external view returns (bool);
}
