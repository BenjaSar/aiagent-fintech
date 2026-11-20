// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {PackedUserOperation} from "../contracts/interfaces/PackedUserOperation.sol";

interface CheatCodes {
    function etch(address target, bytes calldata code) external;
    function readFile(string calldata path) external view returns (string memory);
    function parseBytes(string calldata value) external pure returns (bytes memory);
    function chainId(uint256 newChainId) external;
    function deal(address target, uint256 amount) external;
}

interface IEntryPointV08 {
    function handleOps(PackedUserOperation[] calldata operations, address payable beneficiary) external;

    function getUserOpHash(PackedUserOperation calldata operation) external view returns (bytes32);

    function senderCreator() external view returns (address);

    function getNonce(address sender, uint192 key) external view returns (uint256);

    function depositTo(address account) external payable;

    function balanceOf(address account) external view returns (uint256);
}

contract SmokeAccount {
    uint256 public pings;

    function validateUserOp(PackedUserOperation calldata, bytes32, uint256 missingAccountFunds)
        external
        returns (uint256)
    {
        if (missingAccountFunds > 0) {
            (bool paid,) = msg.sender.call{value: missingAccountFunds}("");
            require(paid, "prefund failed");
        }
        return 0;
    }

    function ping() external {
        pings += 1;
    }

    receive() external payable {}
}

contract LocalEntryPointTest {
    CheatCodes private constant VM = CheatCodes(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
    address private constant ENTRY_POINT = 0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108;
    address private constant SENDER_CREATOR = 0x449ED7C3e6Fee6a97311d4b55475DF59C44AdD33;
    address payable private constant BENEFICIARY = payable(address(0xBEEF));

    SmokeAccount private account;

    function setUp() public {
        VM.etch(ENTRY_POINT, VM.parseBytes(VM.readFile("fixtures/base-sepolia/EntryPoint-v0.8.0.runtime.hex")));
        VM.etch(SENDER_CREATOR, VM.parseBytes(VM.readFile("fixtures/base-sepolia/SenderCreator-v0.8.0.runtime.hex")));
        account = new SmokeAccount();
        VM.deal(address(this), 10 ether);
        IEntryPointV08(ENTRY_POINT).depositTo{value: 1 ether}(address(account));
    }

    function smokeOperation(uint256 nonce) private view returns (PackedUserOperation memory) {
        return PackedUserOperation({
            sender: address(account),
            nonce: nonce,
            initCode: "",
            callData: abi.encodeCall(SmokeAccount.ping, ()),
            accountGasLimits: bytes32((uint256(500_000) << 128) | uint256(500_000)),
            preVerificationGas: 100_000,
            gasFees: bytes32((uint256(1 gwei) << 128) | uint256(1 gwei)),
            paymasterAndData: "",
            signature: ""
        });
    }

    function testEntryPointReportsTheDocumentedSenderCreator() public view {
        require(IEntryPointV08(ENTRY_POINT).senderCreator() == SENDER_CREATOR, "sender creator must match");
    }

    function testHandleOpsRunsAnOperationAndAdvancesTheNonce() public {
        PackedUserOperation[] memory operations = new PackedUserOperation[](1);
        operations[0] = smokeOperation(0);
        IEntryPointV08(ENTRY_POINT).handleOps(operations, BENEFICIARY);
        require(account.pings() == 1, "operation must have executed");
        require(IEntryPointV08(ENTRY_POINT).getNonce(address(account), 0) == 1, "nonce must advance");
    }

    function testReplayingTheSameNonceIsRejected() public {
        PackedUserOperation[] memory operations = new PackedUserOperation[](1);
        operations[0] = smokeOperation(0);
        IEntryPointV08(ENTRY_POINT).handleOps(operations, BENEFICIARY);
        try IEntryPointV08(ENTRY_POINT).handleOps(operations, BENEFICIARY) {
            revert("replay should have reverted");
        } catch {}
        require(account.pings() == 1, "replay must not execute");
    }

    function testUserOperationHashIsBoundToTheChain() public {
        bytes32 first = IEntryPointV08(ENTRY_POINT).getUserOpHash(smokeOperation(0));
        VM.chainId(84_532);
        bytes32 second = IEntryPointV08(ENTRY_POINT).getUserOpHash(smokeOperation(0));
        require(first != second, "hash must change with the chain id");
    }
}
