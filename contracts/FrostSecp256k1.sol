// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

library FrostSecp256k1 {
    struct Statement {
        bytes publicKey;
        bytes message;
    }

    struct Signature {
        bytes groupCommitment;
        uint256 response;
    }

    struct Equation {
        uint256 publicKeyX;
        uint8 recoveryId;
        uint256 challenge;
        uint256 response;
    }

    uint256 private constant GROUP_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
    uint256 private constant FIELD_PRIME = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F;
    uint256 private constant CURVE_COEFFICIENT = 7;
    uint256 private constant SQUARE_ROOT_EXPONENT = (FIELD_PRIME + 1) / 4;
    uint256 private constant COMPRESSED_POINT_LENGTH = 33;
    uint256 private constant WORD_LENGTH = 32;
    uint16 private constant SCALAR_EXPANSION_LENGTH = 48;
    uint8 private constant EVEN_PREFIX = 2;
    uint8 private constant ODD_PREFIX = 3;
    uint8 private constant ECRECOVER_EVEN_V = 27;
    address private constant MODEXP_PRECOMPILE = address(5);
    bytes private constant CHALLENGE_DOMAIN = "FROST-secp256k1-SHA256-v1chal";

    function verify(Statement memory statement, Signature memory signature) internal view returns (bool) {
        if (signature.response == 0 || signature.response >= GROUP_ORDER) return false;
        (bool keyWellFormed, uint8 keyPrefix, uint256 keyX) = _parseCompressed(statement.publicKey);
        if (!keyWellFormed || keyX == 0 || keyX >= GROUP_ORDER) return false;
        (bool commitmentValid, uint256 commitmentX, uint256 commitmentY) = _decompress(signature.groupCommitment);
        if (!commitmentValid) return false;
        uint256 challenge = _challenge(statement, signature);
        if (challenge == 0) return false;
        address recovered =
            _recoverEquationAddress(Equation(keyX, keyPrefix - EVEN_PREFIX, challenge, signature.response));
        return recovered != address(0) && recovered == _addressOf(commitmentX, commitmentY);
    }

    function isValidPublicKey(bytes memory encoded) internal view returns (bool) {
        (bool wellFormed,, uint256 keyX) = _parseCompressed(encoded);
        if (!wellFormed || keyX == 0 || keyX >= GROUP_ORDER) return false;
        (bool onCurve,,) = _decompress(encoded);
        return onCurve;
    }

    function _parseCompressed(bytes memory encoded)
        private
        pure
        returns (bool wellFormed, uint8 prefix, uint256 xCoordinate)
    {
        if (encoded.length != COMPRESSED_POINT_LENGTH) return (false, 0, 0);
        prefix = uint8(encoded[0]);
        if (prefix != EVEN_PREFIX && prefix != ODD_PREFIX) return (false, 0, 0);
        assembly ("memory-safe") {
            xCoordinate := mload(add(encoded, 0x21))
        }
        return (true, prefix, xCoordinate);
    }

    function _decompress(bytes memory encoded)
        private
        view
        returns (bool valid, uint256 xCoordinate, uint256 yCoordinate)
    {
        (bool wellFormed, uint8 prefix, uint256 parsedX) = _parseCompressed(encoded);
        if (!wellFormed || parsedX >= FIELD_PRIME) return (false, 0, 0);
        uint256 curveValue =
            addmod(mulmod(mulmod(parsedX, parsedX, FIELD_PRIME), parsedX, FIELD_PRIME), CURVE_COEFFICIENT, FIELD_PRIME);
        uint256 root = _squareRoot(curveValue);
        if (mulmod(root, root, FIELD_PRIME) != curveValue) return (false, 0, 0);
        if (root % 2 != prefix % 2) root = FIELD_PRIME - root;
        return (true, parsedX, root);
    }

    function _squareRoot(uint256 value) private view returns (uint256) {
        bytes memory input = abi.encode(WORD_LENGTH, WORD_LENGTH, WORD_LENGTH, value, SQUARE_ROOT_EXPONENT, FIELD_PRIME);
        (bool success, bytes memory output) = MODEXP_PRECOMPILE.staticcall(input);
        require(success && output.length == WORD_LENGTH, "modexp precompile failed");
        return abi.decode(output, (uint256));
    }

    function _challenge(Statement memory statement, Signature memory signature) private pure returns (uint256) {
        return _hashToScalar(abi.encodePacked(signature.groupCommitment, statement.publicKey, statement.message));
    }

    function _hashToScalar(bytes memory input) private pure returns (uint256) {
        bytes memory tagSuffix = abi.encodePacked(CHALLENGE_DOMAIN, uint8(CHALLENGE_DOMAIN.length));
        bytes32 firstBlock =
            sha256(abi.encodePacked(bytes32(0), bytes32(0), input, SCALAR_EXPANSION_LENGTH, uint8(0), tagSuffix));
        bytes32 secondBlock = sha256(abi.encodePacked(firstBlock, uint8(1), tagSuffix));
        bytes32 thirdBlock = sha256(abi.encodePacked(firstBlock ^ secondBlock, uint8(2), tagSuffix));
        uint256 high = uint256(secondBlock);
        uint256 low = uint128(bytes16(thirdBlock));
        return addmod(mulmod(high, 2 ** 128, GROUP_ORDER), low, GROUP_ORDER);
    }

    function _recoverEquationAddress(Equation memory equation) private pure returns (address) {
        bytes32 messageHash =
            bytes32((GROUP_ORDER - mulmod(equation.response, equation.publicKeyX, GROUP_ORDER)) % GROUP_ORDER);
        bytes32 recoverySignature = bytes32(GROUP_ORDER - mulmod(equation.challenge, equation.publicKeyX, GROUP_ORDER));
        return
            ecrecover(
                messageHash, ECRECOVER_EVEN_V + equation.recoveryId, bytes32(equation.publicKeyX), recoverySignature
            );
    }

    function _addressOf(uint256 xCoordinate, uint256 yCoordinate) private pure returns (address) {
        return address(uint160(uint256(keccak256(abi.encodePacked(xCoordinate, yCoordinate)))));
    }
}
