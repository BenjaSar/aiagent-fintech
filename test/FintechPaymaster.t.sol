// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IPaymaster, PackedUserOperation} from "@openzeppelin/contracts/interfaces/draft-IERC4337.sol";
import {FintechAccountFactory} from "../contracts/FintechAccountFactory.sol";
import {FintechModuleRegistry} from "../contracts/FintechModuleRegistry.sol";
import {FintechModularAccount} from "../contracts/FintechModularAccount.sol";
import {FintechPaymaster} from "../contracts/FintechPaymaster.sol";
import {TestAttester} from "./support/TestAttester.sol";

interface PaymasterCheats {
    function etch(address target, bytes calldata code) external;
    function readFile(string calldata path) external view returns (string memory);
    function parseBytes(string calldata value) external pure returns (bytes memory);
    function sign(uint256 privateKey, bytes32 digest) external pure returns (uint8 v, bytes32 r, bytes32 s);
    function addr(uint256 privateKey) external pure returns (address);
    function deal(address target, uint256 amount) external;
    function prank(address caller) external;
    function warp(uint256 timestamp) external;
    function expectRevert(bytes calldata reason) external;
}

interface IEntryPointForPaymaster {
    function handleOps(PackedUserOperation[] calldata operations, address payable beneficiary) external;

    function getUserOpHash(PackedUserOperation calldata operation) external view returns (bytes32);

    function balanceOf(address account) external view returns (uint256);
}

contract PingCounter {
    uint256 public pings;

    function ping() external {
        pings += 1;
    }
}

contract FintechPaymasterTest {
    PaymasterCheats private constant VM = PaymasterCheats(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
    address private constant ENTRY_POINT = 0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108;
    address private constant SENDER_CREATOR = 0x449ED7C3e6Fee6a97311d4b55475DF59C44AdD33;
    address payable private constant BENEFICIARY = payable(address(0xBEEF));
    uint256 private constant OWNER_KEY = 0xA11CE;
    uint256 private constant SPONSOR_KEY = 0x5B0A;
    uint256 private constant STRANGER_KEY = 0xBAD;
    bytes32 private constant SALT = bytes32(uint256(1));
    bytes32 private constant SINGLE_CALL_MODE = bytes32(0);
    uint48 private constant NO_LIMIT = type(uint48).max;

    FintechAccountFactory private factory;
    FintechPaymaster private paymaster;
    PingCounter private target;
    address private owner;
    address private account;

    function setUp() public {
        VM.warp(1_000_000);
        VM.etch(ENTRY_POINT, VM.parseBytes(VM.readFile("fixtures/base-sepolia/EntryPoint-v0.8.0.runtime.hex")));
        VM.etch(SENDER_CREATOR, VM.parseBytes(VM.readFile("fixtures/base-sepolia/SenderCreator-v0.8.0.runtime.hex")));
        factory = new FintechAccountFactory(new FintechModuleRegistry(), address(new TestAttester()));
        owner = VM.addr(OWNER_KEY);
        account = factory.getAddress(owner, SALT);
        paymaster = new FintechPaymaster(address(this), VM.addr(SPONSOR_KEY));
        target = new PingCounter();
        VM.deal(address(this), 10 ether);
        paymaster.deposit{value: 1 ether}();
    }

    function creationCode() private view returns (bytes memory) {
        return abi.encodePacked(address(factory), abi.encodeCall(FintechAccountFactory.createAccount, (owner, SALT)));
    }

    function pingCallData() private view returns (bytes memory) {
        return abi.encodeWithSignature(
            "execute(bytes32,bytes)",
            SINGLE_CALL_MODE,
            abi.encodePacked(address(target), uint256(0), abi.encodeCall(PingCounter.ping, ()))
        );
    }

    function unsponsoredOperation() private view returns (PackedUserOperation memory) {
        return PackedUserOperation({
            sender: account,
            nonce: 0,
            initCode: creationCode(),
            callData: pingCallData(),
            accountGasLimits: bytes32((uint256(1_500_000) << 128) | uint256(500_000)),
            preVerificationGas: 100_000,
            gasFees: bytes32((uint256(1 gwei) << 128) | uint256(1 gwei)),
            paymasterAndData: "",
            signature: ""
        });
    }

    function placeholderPaymasterData() private view returns (bytes memory) {
        return abi.encodePacked(address(paymaster), uint128(200_000), uint128(1), new bytes(77));
    }

    function sponsored(uint256 sponsorKey, uint256 accountKey, uint48 validUntil, uint48 validAfter)
        private
        view
        returns (PackedUserOperation memory operation)
    {
        operation = unsponsoredOperation();
        operation.paymasterAndData = placeholderPaymasterData();
        bytes32 sponsorHash = paymaster.getHash(operation, validUntil, validAfter);
        (uint8 sponsorV, bytes32 sponsorR, bytes32 sponsorS) = VM.sign(sponsorKey, sponsorHash);
        operation.paymasterAndData = abi.encodePacked(
            address(paymaster),
            uint128(200_000),
            uint128(1),
            validUntil,
            validAfter,
            abi.encodePacked(sponsorR, sponsorS, sponsorV)
        );
        bytes32 operationHash = IEntryPointForPaymaster(ENTRY_POINT).getUserOpHash(operation);
        (uint8 v, bytes32 r, bytes32 s) = VM.sign(accountKey, operationHash);
        operation.signature = abi.encodePacked(r, s, v);
    }

    function submit(PackedUserOperation memory operation) public {
        PackedUserOperation[] memory operations = new PackedUserOperation[](1);
        operations[0] = operation;
        IEntryPointForPaymaster(ENTRY_POINT).handleOps(operations, BENEFICIARY);
    }

    function expectRejection(PackedUserOperation memory operation, bytes4 reasonCode) private {
        try this.submit(operation) {
            revert("operation should have been rejected");
        } catch (bytes memory reason) {
            bytes4 selector = bytes4(reason);
            require(
                selector == bytes4(keccak256("FailedOp(uint256,string)"))
                    || selector == bytes4(keccak256("FailedOpWithRevert(uint256,string,bytes)")),
                "expected a FailedOp revert"
            );
            bytes memory encodedReason = new bytes(reason.length - 4);
            for (uint256 index = 4; index < reason.length; index++) {
                encodedReason[index - 4] = reason[index];
            }
            (, string memory message) = abi.decode(encodedReason, (uint256, string));
            require(bytes4(bytes(message)) == reasonCode, string.concat("unexpected rejection: ", message));
        }
    }

    function testASponsoredOperationRunsAndTheGasIsChargedToThePaymasterDeposit() public {
        uint256 paymasterBefore = paymaster.depositBalance();
        submit(sponsored(SPONSOR_KEY, OWNER_KEY, NO_LIMIT, 0));
        require(target.pings() == 1, "the call must have executed");
        require(account.code.length != 0, "the account must have been created");
        require(IEntryPointForPaymaster(ENTRY_POINT).balanceOf(account) == 0, "the account must pay nothing");
        require(paymaster.depositBalance() < paymasterBefore, "the paymaster must pay the gas");
    }

    function testASponsorApprovalFromTheWrongSignerIsRejected() public {
        expectRejection(sponsored(STRANGER_KEY, OWNER_KEY, NO_LIMIT, 0), "AA34");
    }

    function testSponsorshipDoesNotReplaceTheAccountSignature() public {
        expectRejection(sponsored(SPONSOR_KEY, STRANGER_KEY, NO_LIMIT, 0), "AA24");
    }

    function testAnExpiredSponsorApprovalIsRejected() public {
        expectRejection(sponsored(SPONSOR_KEY, OWNER_KEY, uint48(block.timestamp - 1), 0), "AA32");
    }

    function testASponsorApprovalNotYetValidIsRejected() public {
        expectRejection(sponsored(SPONSOR_KEY, OWNER_KEY, NO_LIMIT, uint48(block.timestamp + 1 days)), "AA32");
    }

    function testMalformedPaymasterDataIsRejected() public {
        PackedUserOperation memory operation = sponsored(SPONSOR_KEY, OWNER_KEY, NO_LIMIT, 0);
        operation.paymasterAndData = abi.encodePacked(address(paymaster), uint128(200_000), uint128(1), hex"1234");
        bytes32 operationHash = IEntryPointForPaymaster(ENTRY_POINT).getUserOpHash(operation);
        (uint8 v, bytes32 r, bytes32 s) = VM.sign(OWNER_KEY, operationHash);
        operation.signature = abi.encodePacked(r, s, v);
        expectRejection(operation, "AA33");
    }

    function testASponsorApprovalCannotBeReusedForADifferentOperation() public {
        PackedUserOperation memory operation = sponsored(SPONSOR_KEY, OWNER_KEY, NO_LIMIT, 0);
        operation.callData = abi.encodeWithSignature(
            "execute(bytes32,bytes)", SINGLE_CALL_MODE, abi.encodePacked(BENEFICIARY, uint256(0))
        );
        bytes32 operationHash = IEntryPointForPaymaster(ENTRY_POINT).getUserOpHash(operation);
        (uint8 v, bytes32 r, bytes32 s) = VM.sign(OWNER_KEY, operationHash);
        operation.signature = abi.encodePacked(r, s, v);
        expectRejection(operation, "AA34");
    }

    function testThePaymasterHasNoAuthorityOverTheAccount() public {
        submit(sponsored(SPONSOR_KEY, OWNER_KEY, NO_LIMIT, 0));
        VM.deal(account, 1 ether);
        VM.prank(address(paymaster));
        try FintechModularAccount(payable(account))
            .execute(SINGLE_CALL_MODE, abi.encodePacked(BENEFICIARY, uint256(1 ether))) {
            revert("the paymaster must not be able to move funds");
        } catch {}
        require(account.balance == 1 ether, "the account balance must be untouched");
    }

    function testThePaymasterCannotRotateTheAccountOwner() public {
        submit(sponsored(SPONSOR_KEY, OWNER_KEY, NO_LIMIT, 0));
        VM.prank(address(paymaster));
        try FintechModularAccount(payable(account)).rotateOwner(address(paymaster)) {
            revert("the paymaster must not be able to rotate the owner");
        } catch {}
        require(FintechModularAccount(payable(account)).signer() == owner, "owner must be unchanged");
    }

    function testOnlyTheEntryPointCanCallValidation() public {
        PackedUserOperation memory operation = sponsored(SPONSOR_KEY, OWNER_KEY, NO_LIMIT, 0);
        VM.expectRevert(abi.encodeWithSelector(FintechPaymaster.CallerIsNotTheEntryPoint.selector, address(this)));
        paymaster.validatePaymasterUserOp(operation, bytes32(0), 0);
    }

    function testOnlyTheOwnerCanWithdrawTheDeposit() public {
        VM.prank(address(0xBAD));
        VM.expectRevert(abi.encodeWithSelector(FintechPaymaster.CallerIsNotTheOwner.selector, address(0xBAD)));
        paymaster.withdrawTo(BENEFICIARY, 1);
        paymaster.withdrawTo(BENEFICIARY, 0.5 ether);
        require(BENEFICIARY.balance == 0.5 ether, "the owner can withdraw");
    }

    function testPostOperationIsNeverSupported() public {
        VM.expectRevert(abi.encodeWithSelector(FintechPaymaster.PostOperationIsNotSupported.selector));
        paymaster.postOp(IPaymaster.PostOpMode.opSucceeded, "", 0, 0);
    }
}
