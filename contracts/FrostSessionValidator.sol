// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FrostSecp256k1} from "./FrostSecp256k1.sol";
import {IERC7579Validator} from "./interfaces/IERC7579Validator.sol";
import {PackedUserOperation} from "./interfaces/PackedUserOperation.sol";

contract FrostSessionValidator is IERC7579Validator {
    struct SessionKey {
        uint8 prefix;
        uint48 validAfter;
        uint48 validUntil;
        uint256 xCoordinate;
    }

    struct SessionClaim {
        SessionKey sessionKey;
        bytes32 hash;
        FrostSecp256k1.Signature signature;
    }

    uint256 private constant VALIDATOR_MODULE_TYPE = 1;
    uint256 private constant SIGNATURE_VALIDATION_FAILED = 1;
    uint256 private constant COMPRESSED_POINT_LENGTH = 33;
    uint256 private constant SIGNATURE_LENGTH = 65;
    uint256 private constant INSTALL_DATA_LENGTH = 45;
    uint256 private constant VALID_UNTIL_OFFSET = 160;
    uint256 private constant VALID_AFTER_OFFSET = 208;
    bytes4 private constant ERC1271_FAILURE = 0xffffffff;

    mapping(address account => SessionKey sessionKey) private sessionKeys;

    event SessionKeyInstalled(address indexed account, bytes publicKey, uint48 validAfter, uint48 validUntil);
    event SessionKeyRemoved(address indexed account);

    error InvalidInstallData();
    error InvalidSessionPublicKey();
    error InvalidSessionWindow();
    error SessionAlreadyInstalled();

    function onInstall(bytes calldata data) external {
        if (sessionKeys[msg.sender].prefix != 0) revert SessionAlreadyInstalled();
        if (data.length != INSTALL_DATA_LENGTH) revert InvalidInstallData();
        if (!FrostSecp256k1.isValidPublicKey(data[:COMPRESSED_POINT_LENGTH])) revert InvalidSessionPublicKey();
        uint48 validAfter = uint48(bytes6(data[33:39]));
        uint48 validUntil = uint48(bytes6(data[39:45]));
        if (validUntil <= validAfter || validUntil <= block.timestamp) revert InvalidSessionWindow();
        sessionKeys[msg.sender] = SessionKey(uint8(data[0]), validAfter, validUntil, uint256(bytes32(data[1:33])));
        emit SessionKeyInstalled(msg.sender, data[:COMPRESSED_POINT_LENGTH], validAfter, validUntil);
    }

    function onUninstall(bytes calldata) external {
        delete sessionKeys[msg.sender];
        emit SessionKeyRemoved(msg.sender);
    }

    function isModuleType(uint256 moduleTypeId) external pure returns (bool) {
        return moduleTypeId == VALIDATOR_MODULE_TYPE;
    }

    function validateUserOp(PackedUserOperation calldata userOp, bytes32 userOpHash) external view returns (uint256) {
        (bool wellFormed, FrostSecp256k1.Signature memory signature) = _parseSignature(userOp.signature);
        SessionKey memory sessionKey = sessionKeys[msg.sender];
        bool authorized = wellFormed && _isAuthorized(SessionClaim(sessionKey, userOpHash, signature));
        return authorized ? _packValidityWindow(sessionKey) : SIGNATURE_VALIDATION_FAILED;
    }

    function isValidSignatureWithSender(address, bytes32, bytes calldata) external pure returns (bytes4) {
        return ERC1271_FAILURE;
    }

    function sessionPublicKeyOf(address account) external view returns (bytes memory) {
        return _encodeKey(sessionKeys[account]);
    }

    function sessionWindowOf(address account) external view returns (uint48 validAfter, uint48 validUntil) {
        SessionKey memory sessionKey = sessionKeys[account];
        return (sessionKey.validAfter, sessionKey.validUntil);
    }

    function _encodeKey(SessionKey memory sessionKey) private pure returns (bytes memory) {
        if (sessionKey.prefix == 0) return "";
        return abi.encodePacked(bytes1(sessionKey.prefix), bytes32(sessionKey.xCoordinate));
    }

    function _packValidityWindow(SessionKey memory sessionKey) private pure returns (uint256) {
        return
            (uint256(sessionKey.validUntil) << VALID_UNTIL_OFFSET)
                | (uint256(sessionKey.validAfter) << VALID_AFTER_OFFSET);
    }

    function _isAuthorized(SessionClaim memory claim) private view returns (bool) {
        bytes memory publicKey = _encodeKey(claim.sessionKey);
        if (publicKey.length == 0) return false;
        return FrostSecp256k1.verify(FrostSecp256k1.Statement(publicKey, abi.encodePacked(claim.hash)), claim.signature);
    }

    function _parseSignature(bytes calldata encoded)
        private
        pure
        returns (bool wellFormed, FrostSecp256k1.Signature memory signature)
    {
        if (encoded.length != SIGNATURE_LENGTH) return (false, signature);
        signature.groupCommitment = encoded[:COMPRESSED_POINT_LENGTH];
        signature.response = uint256(bytes32(encoded[COMPRESSED_POINT_LENGTH:]));
        return (true, signature);
    }
}
