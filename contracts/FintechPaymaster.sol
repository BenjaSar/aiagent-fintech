// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IPaymaster, PackedUserOperation} from "@openzeppelin/contracts/interfaces/draft-IERC4337.sol";
import {ERC4337Utils} from "@openzeppelin/contracts/account/utils/draft-ERC4337Utils.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

interface IEntryPointDeposits {
    function depositTo(address account) external payable;

    function withdrawTo(address payable withdrawAddress, uint256 amount) external;

    function balanceOf(address account) external view returns (uint256);
}

contract FintechPaymaster is IPaymaster {
    address private constant ENTRY_POINT = 0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108;
    uint256 private constant PAYMASTER_DATA_OFFSET = 52;
    uint256 private constant GAS_LIMITS_START = 20;
    uint256 private constant TIME_RANGE_LENGTH = 12;
    uint256 private constant SIGNATURE_LENGTH = 65;

    address public immutable owner;
    address public immutable sponsorSigner;

    error CallerIsNotTheEntryPoint(address caller);
    error CallerIsNotTheOwner(address caller);
    error InvalidPaymasterData();
    error PostOperationIsNotSupported();

    constructor(address owner_, address sponsorSigner_) {
        owner = owner_;
        sponsorSigner = sponsorSigner_;
    }

    function validatePaymasterUserOp(PackedUserOperation calldata userOp, bytes32, uint256)
        external
        view
        returns (bytes memory context, uint256 validationData)
    {
        if (msg.sender != ENTRY_POINT) revert CallerIsNotTheEntryPoint(msg.sender);
        bytes calldata data = userOp.paymasterAndData[PAYMASTER_DATA_OFFSET:];
        if (data.length != TIME_RANGE_LENGTH + SIGNATURE_LENGTH) revert InvalidPaymasterData();
        uint48 validUntil = uint48(bytes6(data[:6]));
        uint48 validAfter = uint48(bytes6(data[6:TIME_RANGE_LENGTH]));
        (address recovered, ECDSA.RecoverError problem,) =
            ECDSA.tryRecover(getHash(userOp, validUntil, validAfter), data[TIME_RANGE_LENGTH:]);
        bool approved = problem == ECDSA.RecoverError.NoError && recovered == sponsorSigner;
        return ("", ERC4337Utils.packValidationData(approved, validAfter, validUntil));
    }

    function postOp(PostOpMode, bytes calldata, uint256, uint256) external pure {
        revert PostOperationIsNotSupported();
    }

    function getHash(PackedUserOperation calldata userOp, uint48 validUntil, uint48 validAfter)
        public
        view
        returns (bytes32)
    {
        return keccak256(
            abi.encode(
                userOp.sender,
                userOp.nonce,
                keccak256(userOp.initCode),
                keccak256(userOp.callData),
                userOp.accountGasLimits,
                bytes32(userOp.paymasterAndData[GAS_LIMITS_START:PAYMASTER_DATA_OFFSET]),
                userOp.preVerificationGas,
                userOp.gasFees,
                block.chainid,
                address(this),
                validUntil,
                validAfter
            )
        );
    }

    function deposit() external payable {
        IEntryPointDeposits(ENTRY_POINT).depositTo{value: msg.value}(address(this));
    }

    function withdrawTo(address payable destination, uint256 amount) external {
        if (msg.sender != owner) revert CallerIsNotTheOwner(msg.sender);
        IEntryPointDeposits(ENTRY_POINT).withdrawTo(destination, amount);
    }

    function depositBalance() external view returns (uint256) {
        return IEntryPointDeposits(ENTRY_POINT).balanceOf(address(this));
    }
}
