// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {AccountERC7579} from "@openzeppelin/contracts/account/extensions/draft-AccountERC7579.sol";
import {AccountERC7579Hooked} from "@openzeppelin/contracts/account/extensions/draft-AccountERC7579Hooked.sol";
import {ERC4337Utils} from "@openzeppelin/contracts/account/utils/draft-ERC4337Utils.sol";
import {Mode} from "@openzeppelin/contracts/account/utils/draft-ERC7579Utils.sol";
import {IEntryPoint, PackedUserOperation} from "@openzeppelin/contracts/interfaces/draft-IERC4337.sol";
import {Calldata} from "@openzeppelin/contracts/utils/Calldata.sol";
import {MODULE_TYPE_EXECUTOR, MODULE_TYPE_VALIDATOR} from "@openzeppelin/contracts/interfaces/draft-IERC7579.sol";
import {Initializable} from "@openzeppelin/contracts/proxy/utils/Initializable.sol";
import {SignerECDSA} from "@openzeppelin/contracts/utils/cryptography/signers/SignerECDSA.sol";
import {IERC7484Registry} from "./interfaces/IERC7484Registry.sol";
import {ISessionExecution} from "./interfaces/ISessionExecution.sol";

contract FintechModularAccount is AccountERC7579Hooked, SignerECDSA, Initializable, ISessionExecution {
    IERC7484Registry public immutable registry;

    address[] private _trustedAttesters;
    uint8 private _attesterThreshold;

    event OwnerRotated(address indexed previousOwner, address indexed newOwner);
    event AttesterPolicyChanged(address[] attesters, uint8 threshold);

    error CallerIsNotAnInstalledExecutor(address caller);
    error InvalidOwner();
    error InvalidRegistry();
    error InvalidAttester();
    error PolicyHookNotInstalled();

    constructor(IERC7484Registry registry_) SignerECDSA(address(0)) {
        if (address(registry_) == address(0)) revert InvalidRegistry();
        registry = registry_;
        _disableInitializers();
    }

    function initializeAccount(address owner, address attester) external initializer {
        if (attester == address(0)) revert InvalidAttester();
        _setSigner(owner);
        _trustedAttesters.push(attester);
        _attesterThreshold = 1;
    }

    function setAttesterPolicy(address[] calldata attesters, uint8 threshold) external onlyEntryPointOrSelf {
        _trustedAttesters = attesters;
        _attesterThreshold = threshold;
        emit AttesterPolicyChanged(attesters, threshold);
    }

    function attesterPolicy() external view returns (address[] memory, uint8) {
        return (_trustedAttesters, _attesterThreshold);
    }

    function rotateOwner(address newOwner) external {
        if (!isModuleInstalled(MODULE_TYPE_EXECUTOR, msg.sender, Calldata.emptyBytes())) {
            revert CallerIsNotAnInstalledExecutor(msg.sender);
        }
        if (newOwner == address(0)) revert InvalidOwner();
        address previousOwner = signer();
        _setSigner(newOwner);
        emit OwnerRotated(previousOwner, newOwner);
    }

    function executeAsSession(bytes32 mode, bytes calldata executionCalldata) external onlyEntryPoint {
        if (hook() == address(0)) revert PolicyHookNotInstalled();
        _execute(Mode.wrap(mode), executionCalldata);
    }

    function entryPoint() public pure override returns (IEntryPoint) {
        return ERC4337Utils.ENTRYPOINT_V08;
    }

    function _validateUserOp(PackedUserOperation calldata userOp, bytes32 userOpHash, bytes calldata signature)
        internal
        override
        returns (uint256)
    {
        if (isSessionOperation(userOp) && !callsSessionEntryPoint(userOp)) {
            return ERC4337Utils.SIG_VALIDATION_FAILED;
        }
        return super._validateUserOp(userOp, userOpHash, signature);
    }

    function _installModule(uint256 moduleTypeId, address module, bytes memory initData) internal override {
        registry.check(module, moduleTypeId, _trustedAttesters, _attesterThreshold);
        super._installModule(moduleTypeId, module, initData);
    }

    function isSessionOperation(PackedUserOperation calldata userOp) private view returns (bool) {
        address validator = _extractUserOpValidator(userOp);
        return isModuleInstalled(MODULE_TYPE_VALIDATOR, validator, Calldata.emptyBytes());
    }

    function callsSessionEntryPoint(PackedUserOperation calldata userOp) private pure returns (bool) {
        return userOp.callData.length >= 4 && bytes4(userOp.callData) == ISessionExecution.executeAsSession.selector;
    }

    function _rawSignatureValidation(bytes32 hash, bytes calldata signature)
        internal
        view
        override(AccountERC7579, SignerECDSA)
        returns (bool)
    {
        return SignerECDSA._rawSignatureValidation(hash, signature);
    }
}
