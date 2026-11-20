// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC7484Registry} from "./interfaces/IERC7484Registry.sol";

contract FintechModuleRegistry is IERC7484Registry {
    struct Attestation {
        uint48 attestedAt;
        uint48 expiresAt;
        uint48 revokedAt;
        uint256 moduleTypes;
        bytes32 codeHash;
    }

    struct TrustedAttesters {
        uint8 threshold;
        address[] attesters;
    }

    struct Query {
        address module;
        uint256 requiredTypes;
        uint256 threshold;
    }

    uint256 public constant MAXIMUM_ATTESTERS = 16;
    uint256 public constant MODULE_TYPE_LIMIT = 256;

    mapping(address module => mapping(address attester => Attestation)) private _attestations;
    mapping(address account => TrustedAttesters) private _trusted;

    event Attested(
        address indexed module, address indexed attester, uint256 moduleTypes, bytes32 codeHash, uint48 expiresAt
    );
    event AttestationRevoked(address indexed module, address indexed attester);
    event AttestersTrusted(address indexed account, uint8 threshold, address[] attesters);

    error ModuleHasNoCode(address module);
    error NoModuleTypes();
    error ModuleTypeOutOfRange(uint256 moduleType);
    error AlreadyExpired(uint48 expiresAt);
    error NotAttested(address module, address attester);
    error AttestationWasRevoked(address module, address attester);
    error ModuleTypeMismatch(address module, address attester, uint256 requiredTypes);
    error AttestationThresholdNotMet(uint256 validAttestations, uint256 threshold);
    error InvalidAttesterSet();
    error InvalidThreshold(uint256 threshold);
    error NoTrustedAttesters(address account);

    function attest(address module, uint256 moduleTypes, uint48 expiresAt) external {
        if (module.code.length == 0) revert ModuleHasNoCode(module);
        if (moduleTypes == 0) revert NoModuleTypes();
        if (expiresAt != 0 && expiresAt <= block.timestamp) revert AlreadyExpired(expiresAt);
        Attestation storage existing = _attestations[module][msg.sender];
        if (existing.revokedAt != 0) revert AttestationWasRevoked(module, msg.sender);
        bytes32 codeHash = module.codehash;
        _attestations[module][msg.sender] = Attestation({
            attestedAt: uint48(block.timestamp),
            expiresAt: expiresAt,
            revokedAt: 0,
            moduleTypes: moduleTypes,
            codeHash: codeHash
        });
        emit Attested(module, msg.sender, moduleTypes, codeHash, expiresAt);
    }

    function revoke(address module) external {
        Attestation storage existing = _attestations[module][msg.sender];
        if (existing.attestedAt == 0) revert NotAttested(module, msg.sender);
        if (existing.revokedAt != 0) revert AttestationWasRevoked(module, msg.sender);
        existing.revokedAt = uint48(block.timestamp);
        emit AttestationRevoked(module, msg.sender);
    }

    function attestationOf(address module, address attester) external view returns (Attestation memory) {
        return _attestations[module][attester];
    }

    function trustedAttestersOf(address account) external view returns (uint8, address[] memory) {
        TrustedAttesters storage trusted = _trusted[account];
        return (trusted.threshold, trusted.attesters);
    }

    function trustAttesters(uint8 threshold, address[] calldata attesters) external {
        _requireValidSet(attesters, threshold);
        _trusted[msg.sender] = TrustedAttesters({threshold: threshold, attesters: attesters});
        emit AttestersTrusted(msg.sender, threshold, attesters);
    }

    function check(address module) external view {
        _verifyForAccount(msg.sender, Query(module, 0, 0));
    }

    function checkForAccount(address smartAccount, address module) external view {
        _verifyForAccount(smartAccount, Query(module, 0, 0));
    }

    function check(address module, uint256 moduleType) external view {
        _verifyForAccount(msg.sender, Query(module, _typeBit(moduleType), 0));
    }

    function checkForAccount(address smartAccount, address module, uint256 moduleType) external view {
        _verifyForAccount(smartAccount, Query(module, _typeBit(moduleType), 0));
    }

    function check(address module, address[] calldata attesters, uint256 threshold) external view {
        _verify(Query(module, 0, threshold), attesters);
    }

    function check(address module, uint256 moduleType, address[] calldata attesters, uint256 threshold) external view {
        _verify(Query(module, _typeBit(moduleType), threshold), attesters);
    }

    function _verifyForAccount(address account, Query memory query) private view {
        TrustedAttesters storage trusted = _trusted[account];
        if (trusted.attesters.length == 0) revert NoTrustedAttesters(account);
        query.threshold = trusted.threshold;
        _verify(query, trusted.attesters);
    }

    function _verify(Query memory query, address[] memory attesters) private view {
        _requireValidSet(attesters, query.threshold);
        bytes32 codeHash = query.module.codehash;
        uint256 validAttestations;
        for (uint256 index; index < attesters.length; ++index) {
            if (_countsAsValid(query, attesters[index], codeHash)) ++validAttestations;
        }
        if (validAttestations < query.threshold) {
            revert AttestationThresholdNotMet(validAttestations, query.threshold);
        }
    }

    function _countsAsValid(Query memory query, address attester, bytes32 codeHash) private view returns (bool) {
        Attestation memory record = _attestations[query.module][attester];
        if (record.attestedAt == 0) return false;
        if (record.revokedAt != 0) revert AttestationWasRevoked(query.module, attester);
        if (record.expiresAt != 0 && record.expiresAt < block.timestamp) return false;
        if (record.codeHash != codeHash) return false;
        if (record.moduleTypes & query.requiredTypes != query.requiredTypes) {
            revert ModuleTypeMismatch(query.module, attester, query.requiredTypes);
        }
        return true;
    }

    function _typeBit(uint256 moduleType) private pure returns (uint256) {
        if (moduleType >= MODULE_TYPE_LIMIT) revert ModuleTypeOutOfRange(moduleType);
        return 1 << moduleType;
    }

    function _requireValidSet(address[] memory attesters, uint256 threshold) private pure {
        uint256 count = attesters.length;
        if (count == 0 || count > MAXIMUM_ATTESTERS) revert InvalidAttesterSet();
        if (threshold == 0 || threshold > count) revert InvalidThreshold(threshold);
        address previous;
        for (uint256 index; index < count; ++index) {
            if (attesters[index] <= previous) revert InvalidAttesterSet();
            previous = attesters[index];
        }
    }
}
