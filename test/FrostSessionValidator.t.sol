// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FrostSessionValidator} from "../contracts/FrostSessionValidator.sol";
import {PackedUserOperation} from "../contracts/interfaces/PackedUserOperation.sol";

interface StorageReader {
    function load(address target, bytes32 slot) external view returns (bytes32);
}

contract OtherAccount {
    function validate(FrostSessionValidator validator, PackedUserOperation memory userOp, bytes32 hash)
        external
        view
        returns (uint256)
    {
        return validator.validateUserOp(userOp, hash);
    }
}

contract FrostSessionValidatorTest {
    uint48 private constant VALID_AFTER = 1_000;
    uint48 private constant VALID_UNTIL = 2_000_000_000;
    uint256 private constant SUCCESS = (uint256(VALID_UNTIL) << 160) | (uint256(VALID_AFTER) << 208);
    uint256 private constant FAILED = 1;
    bytes4 private constant NO_MAGIC_VALUE = 0xffffffff;
    StorageReader private constant VM = StorageReader(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
    bytes32 private constant GROUP_PUBLIC_KEY_X = 0xf37c34b66ced1fb51c34a90bdae006901f10625cc06c4f64663b0eae87d87b4f;
    bytes private constant GROUP_PUBLIC_KEY = hex"02f37c34b66ced1fb51c34a90bdae006901f10625cc06c4f64663b0eae87d87b4f";

    struct Signed {
        bytes32 hash;
        bytes commitment;
        uint256 response;
    }

    FrostSessionValidator private validator = new FrostSessionValidator();

    function quorumOneTwo() private pure returns (Signed memory) {
        return Signed({
            hash: 0xd86547dd5271cbe6f4ffece3eb66bba24e21e2bf117680bdbbbc81a8ec062447,
            commitment: hex"02b7a8fd68a4bb1793b58305f45f27ad7d88e82f23802ffa9236bec017b0c499db",
            response: 0x917a88749254931a5c7f7862a98c28613ce430a12512a4068e9dd2792604be29
        });
    }

    function quorumOneThree() private pure returns (Signed memory) {
        return Signed({
            hash: 0x7407b22ab5377d2ae2b1325e7368d74106e05873d0e09ad1d05581e984ed8c09,
            commitment: hex"0391c81717cdfe0bc45fea0e1c3530dddaefe9826dbf08f277f4d4b603aedbc3bb",
            response: 0x9a524a23ce9791f1a25fd5b744b6d3f3f76843208afbe2243ac8124d63735912
        });
    }

    function quorumTwoThree() private pure returns (Signed memory) {
        return Signed({
            hash: 0x6e2828a3952b98a03982085a9c7b705b096e9007dbbf4f515afc25de615c6529,
            commitment: hex"036162c6df1837a56337285dddceea28965ea6a6674e50761fdf032154fc0c9303",
            response: 0xdf20d6164d6d1b2a074f959e2150e9b7ba6f4966b95428b92afe981f1514f646
        });
    }

    function belowThresholdSingleShare() private pure returns (Signed memory) {
        return Signed({
            hash: 0xe01682ae48868f1caf467ea71faa8194a7ae0949252f873030a55e98d64997e8,
            commitment: hex"0320a22f5b97e04987448ddc06676c02adff2a52e49d7db991696384ee33e15e4b",
            response: 0x506550f4d29cf79cdaadf76db802961c19f73ef71543df7099fd20a3da09f492
        });
    }

    function soloEnclave() private pure returns (Signed memory) {
        return Signed({
            hash: 0x077944e45d16a1fe21ad4edd6ca5c6e18da9ce0bae4688ebc00961fbe5557916,
            commitment: hex"035e7cf4e852c425a6cc63fe20ce71cbd7c5be44317769f62561a35a7dd1d60281",
            response: 0xbcc9e9357842a2bdb211a97392a4e8ab9a166cb92095f0cf9344b58ba9d5d0b1
        });
    }

    function userOperationWith(bytes memory signature) private view returns (PackedUserOperation memory) {
        return PackedUserOperation({
            sender: address(this),
            nonce: 0,
            initCode: "",
            callData: "",
            accountGasLimits: bytes32(0),
            preVerificationGas: 0,
            gasFees: bytes32(0),
            paymasterAndData: "",
            signature: signature
        });
    }

    function validationResult(Signed memory signed) private view returns (uint256) {
        bytes memory signature = abi.encodePacked(signed.commitment, signed.response);
        return validator.validateUserOp(userOperationWith(signature), signed.hash);
    }

    function installData(bytes memory publicKey, uint48 validAfter, uint48 validUntil)
        private
        pure
        returns (bytes memory)
    {
        return abi.encodePacked(publicKey, validAfter, validUntil);
    }

    function defaultInstallData() private pure returns (bytes memory) {
        return installData(GROUP_PUBLIC_KEY, VALID_AFTER, VALID_UNTIL);
    }

    function installGroupKey() private {
        validator.onInstall(defaultInstallData());
    }

    function expectInstallRevert(bytes memory data, bytes4 expectedError) private {
        try validator.onInstall(data) {
            revert("installation should have reverted");
        } catch (bytes memory reason) {
            require(bytes4(reason) == expectedError, "unexpected revert reason");
        }
    }

    function testModuleTypeIsValidatorOnly() public view {
        require(validator.isModuleType(1), "must be a validator");
        require(!validator.isModuleType(2), "must not be an executor");
        require(!validator.isModuleType(3), "must not be a fallback handler");
        require(!validator.isModuleType(4), "must not be a hook");
    }

    function testInstallStoresTheSessionKeyForTheCallingAccount() public {
        installGroupKey();
        require(
            keccak256(validator.sessionPublicKeyOf(address(this))) == keccak256(GROUP_PUBLIC_KEY), "key must be stored"
        );
    }

    function testInstallRejectsInvalidKeys() public {
        bytes4 invalidKey = FrostSessionValidator.InvalidSessionPublicKey.selector;
        bytes memory unknownPrefix = hex"04f37c34b66ced1fb51c34a90bdae006901f10625cc06c4f64663b0eae87d87b4f";
        bytes memory offCurve = abi.encodePacked(bytes1(0x02), bytes32(uint256(5)));
        bytes memory zeroCoordinate = abi.encodePacked(bytes1(0x02), bytes32(0));
        bytes memory atGroupOrder =
            abi.encodePacked(bytes1(0x02), bytes32(0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141));
        expectInstallRevert(installData(unknownPrefix, VALID_AFTER, VALID_UNTIL), invalidKey);
        expectInstallRevert(installData(offCurve, VALID_AFTER, VALID_UNTIL), invalidKey);
        expectInstallRevert(installData(zeroCoordinate, VALID_AFTER, VALID_UNTIL), invalidKey);
        expectInstallRevert(installData(atGroupOrder, VALID_AFTER, VALID_UNTIL), invalidKey);
    }

    function testInstallRejectsDataOfTheWrongLength() public {
        bytes4 invalidData = FrostSessionValidator.InvalidInstallData.selector;
        expectInstallRevert(hex"", invalidData);
        expectInstallRevert(GROUP_PUBLIC_KEY, invalidData);
        expectInstallRevert(abi.encodePacked(defaultInstallData(), bytes1(0x00)), invalidData);
    }

    function testInstallRejectsAnInvalidValidityWindow() public {
        bytes4 invalidWindow = FrostSessionValidator.InvalidSessionWindow.selector;
        expectInstallRevert(installData(GROUP_PUBLIC_KEY, VALID_AFTER, 0), invalidWindow);
        expectInstallRevert(installData(GROUP_PUBLIC_KEY, VALID_AFTER, VALID_AFTER), invalidWindow);
        expectInstallRevert(installData(GROUP_PUBLIC_KEY, VALID_UNTIL, VALID_AFTER), invalidWindow);
        expectInstallRevert(installData(GROUP_PUBLIC_KEY, 0, uint48(block.timestamp)), invalidWindow);
    }

    function testInstallStoresTheValidityWindow() public {
        installGroupKey();
        (uint48 validAfter, uint48 validUntil) = validator.sessionWindowOf(address(this));
        require(validAfter == VALID_AFTER && validUntil == VALID_UNTIL, "window must be stored");
    }

    function testValidationReturnsTheValidityWindowForTheEntryPointToEnforce() public {
        installGroupKey();
        uint256 result = validationResult(quorumOneTwo());
        require(uint48(result >> 160) == VALID_UNTIL, "validUntil must be packed after the authorizer");
        require(uint48(result >> 208) == VALID_AFTER, "validAfter must be packed above validUntil");
        require(address(uint160(result)) == address(0), "authorizer must be zero for a valid signature");
    }

    function testInstallRevertsWhenAlreadyInstalled() public {
        installGroupKey();
        expectInstallRevert(defaultInstallData(), FrostSessionValidator.SessionAlreadyInstalled.selector);
    }

    function testEveryTwoOfThreeQuorumIsAccepted() public {
        installGroupKey();
        require(validationResult(quorumOneTwo()) == SUCCESS, "quorum 1,2 must be accepted");
        require(validationResult(quorumOneThree()) == SUCCESS, "quorum 1,3 must be accepted");
        require(validationResult(quorumTwoThree()) == SUCCESS, "quorum 2,3 must be accepted");
    }

    function testBelowThresholdSignatureIsRejected() public {
        installGroupKey();
        require(validationResult(belowThresholdSingleShare()) == FAILED, "one share must be rejected");
    }

    function testSingleEnclaveSigningAloneIsRejected() public {
        installGroupKey();
        require(validationResult(soloEnclave()) == FAILED, "a lone enclave must be rejected");
    }

    function testSignatureOverADifferentHashIsRejected() public {
        installGroupKey();
        Signed memory signed = quorumOneTwo();
        signed.hash = quorumOneThree().hash;
        require(validationResult(signed) == FAILED, "a different hash must be rejected");
    }

    function testTamperedResponseIsRejected() public {
        installGroupKey();
        Signed memory signed = quorumOneTwo();
        signed.response = signed.response ^ 1;
        require(validationResult(signed) == FAILED, "a tampered response must be rejected");
    }

    function testWrongSignatureLengthFailsWithoutReverting() public {
        installGroupKey();
        Signed memory signed = quorumOneTwo();
        bytes memory valid = abi.encodePacked(signed.commitment, signed.response);
        bytes memory tooShort = abi.encodePacked(signed.commitment);
        bytes memory tooLong = abi.encodePacked(valid, bytes1(0x00));
        require(validator.validateUserOp(userOperationWith(hex""), signed.hash) == FAILED, "empty");
        require(validator.validateUserOp(userOperationWith(tooShort), signed.hash) == FAILED, "short");
        require(validator.validateUserOp(userOperationWith(tooLong), signed.hash) == FAILED, "long");
    }

    function testNothingValidatesWithoutAnInstalledKey() public view {
        require(validationResult(quorumOneTwo()) == FAILED, "must fail closed without a key");
    }

    function testAnotherAccountCannotUseThisAccountsKey() public {
        installGroupKey();
        Signed memory signed = quorumOneTwo();
        bytes memory signature = abi.encodePacked(signed.commitment, signed.response);
        OtherAccount other = new OtherAccount();
        uint256 result = other.validate(validator, userOperationWith(signature), signed.hash);
        require(result == FAILED, "keys must be isolated per account");
    }

    function testUninstallRemovesTheKeyAndStopsValidation() public {
        installGroupKey();
        validator.onUninstall(hex"");
        require(validator.sessionPublicKeyOf(address(this)).length == 0, "key must be removed");
        require(validationResult(quorumOneTwo()) == FAILED, "validation must stop after uninstall");
    }

    function testUninstallNeverRevertsEvenWhenNothingIsInstalled() public {
        validator.onUninstall(hex"");
        validator.onUninstall(hex"");
    }

    function testReinstallIsPossibleAfterUninstall() public {
        installGroupKey();
        validator.onUninstall(hex"");
        installGroupKey();
        require(validationResult(quorumTwoThree()) == SUCCESS, "reinstalled key must validate");
    }

    function testErc1271ForwardingNeverAuthorizesTheSessionKey() public {
        installGroupKey();
        Signed memory signed = quorumOneTwo();
        bytes memory signature = abi.encodePacked(signed.commitment, signed.response);
        bytes4 result = validator.isValidSignatureWithSender(address(this), signed.hash, signature);
        require(result == NO_MAGIC_VALUE, "session key must not sign ERC-1271 messages");
    }

    function accountMappingSlot(address account) private pure returns (uint256) {
        return uint256(keccak256(abi.encode(account, uint256(0))));
    }

    function testKeyLivesInSlotsAssociatedWithTheAccount() public {
        installGroupKey();
        uint256 slot = accountMappingSlot(address(this));
        bytes32 packedFirstSlot = bytes32(uint256(2) | (uint256(VALID_AFTER) << 8) | (uint256(VALID_UNTIL) << 56));
        require(
            VM.load(address(validator), bytes32(slot)) == packedFirstSlot, "prefix and window must share the first slot"
        );
        require(
            VM.load(address(validator), bytes32(slot + 1)) == GROUP_PUBLIC_KEY_X,
            "x coordinate must sit in the second slot"
        );
    }

    function testNothingIsStoredAtTheHashedDataSlot() public {
        installGroupKey();
        bytes32 hashedSlot = keccak256(abi.encode(accountMappingSlot(address(this))));
        require(VM.load(address(validator), hashedSlot) == bytes32(0), "no key data outside the associated range");
        require(VM.load(address(validator), bytes32(uint256(hashedSlot) + 1)) == bytes32(0), "no key data after it");
    }

    function testUninstallClearsBothSlots() public {
        installGroupKey();
        validator.onUninstall(hex"");
        uint256 slot = accountMappingSlot(address(this));
        require(VM.load(address(validator), bytes32(slot)) == bytes32(0), "first slot must be cleared");
        require(VM.load(address(validator), bytes32(slot + 1)) == bytes32(0), "second slot must be cleared");
    }
}
