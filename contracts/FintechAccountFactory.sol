// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {IERC7484Registry} from "./interfaces/IERC7484Registry.sol";
import {FintechModularAccount} from "./FintechModularAccount.sol";

interface IEntryPointSenderCreator {
    function senderCreator() external view returns (address);
}

contract FintechAccountFactory {
    address private constant ENTRY_POINT = 0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108;

    address public immutable implementation;
    address public immutable attester;

    error CallerIsNotTheSenderCreator(address caller);
    error InvalidAttester();

    constructor(IERC7484Registry registry, address attester_) {
        if (attester_ == address(0)) revert InvalidAttester();
        attester = attester_;
        implementation = address(new FintechModularAccount(registry));
    }

    function createAccount(address owner, bytes32 salt) external returns (address account) {
        address senderCreator = IEntryPointSenderCreator(ENTRY_POINT).senderCreator();
        if (msg.sender != senderCreator) revert CallerIsNotTheSenderCreator(msg.sender);
        account = getAddress(owner, salt);
        if (account.code.length != 0) return account;
        Clones.cloneDeterministic(implementation, _effectiveSalt(owner, salt));
        FintechModularAccount(payable(account)).initializeAccount(owner, attester);
    }

    function getAddress(address owner, bytes32 salt) public view returns (address) {
        return Clones.predictDeterministicAddress(implementation, _effectiveSalt(owner, salt));
    }

    function _effectiveSalt(address owner, bytes32 salt) private pure returns (bytes32) {
        return keccak256(abi.encode(owner, salt));
    }
}
