// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

contract DevelopmentPingTarget {
    uint256 public pings;

    receive() external payable {}

    function ping() external {
        pings += 1;
    }
}
