// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FintechModuleRegistry} from "../contracts/FintechModuleRegistry.sol";

interface RegistryCheats {
    function warp(uint256 timestamp) external;
    function prank(address caller) external;
    function etch(address target, bytes calldata code) external;
    function expectRevert(bytes calldata revertData) external;
    function expectRevert(bytes4 revertData) external;
}

contract InertModule {
    function marker() external pure returns (uint256) {
        return 1;
    }
}

contract FintechModuleRegistryTest {
    RegistryCheats private constant VM = RegistryCheats(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
    address private constant FIRST_ATTESTER = address(0x1111);
    address private constant SECOND_ATTESTER = address(0x2222);
    address private constant THIRD_ATTESTER = address(0x3333);
    address private constant ACCOUNT = address(0xACC0);
    address private constant OTHER_ACCOUNT = address(0xACC1);
    uint256 private constant VALIDATOR_TYPE = 1;
    uint256 private constant EXECUTOR_TYPE = 2;
    uint256 private constant HOOK_TYPE = 4;
    uint256 private constant VALIDATOR_BIT = 1 << 1;
    uint256 private constant EXECUTOR_BIT = 1 << 2;
    uint256 private constant START = 1_000_000;

    FintechModuleRegistry private registry;
    InertModule private module;

    function setUp() public {
        VM.warp(START);
        registry = new FintechModuleRegistry();
        module = new InertModule();
    }

    function attester(address who) private pure returns (address[] memory attesters) {
        attesters = new address[](1);
        attesters[0] = who;
    }

    function pair(address first, address second) private pure returns (address[] memory attesters) {
        attesters = new address[](2);
        attesters[0] = first;
        attesters[1] = second;
    }

    function attestAs(address who, address target, uint256 moduleTypes, uint48 expiresAt) private {
        VM.prank(who);
        registry.attest(target, moduleTypes, expiresAt);
    }

    function revokeAs(address who, address target) private {
        VM.prank(who);
        registry.revoke(target);
    }

    function trustAs(address who, uint8 threshold, address[] memory attesters) private {
        VM.prank(who);
        registry.trustAttesters(threshold, attesters);
    }

    function thresholdNotMet(uint256 valid, uint256 threshold) private pure returns (bytes memory) {
        return abi.encodeWithSelector(FintechModuleRegistry.AttestationThresholdNotMet.selector, valid, threshold);
    }

    function testAnAttestationRecordsTheCodeHashTheTypesAndTheTime() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT | EXECUTOR_BIT, 0);
        FintechModuleRegistry.Attestation memory record = registry.attestationOf(address(module), FIRST_ATTESTER);
        require(record.attestedAt == START, "time");
        require(record.moduleTypes == (VALIDATOR_BIT | EXECUTOR_BIT), "types");
        require(record.codeHash == address(module).codehash, "code hash");
        require(record.revokedAt == 0 && record.expiresAt == 0, "no revocation or expiry");
    }

    function testAttestingAnAddressWithoutCodeIsRefused() public {
        VM.expectRevert(abi.encodeWithSelector(FintechModuleRegistry.ModuleHasNoCode.selector, address(0xDEAD)));
        attestAs(FIRST_ATTESTER, address(0xDEAD), VALIDATOR_BIT, 0);
    }

    function testAttestingWithoutModuleTypesIsRefused() public {
        VM.expectRevert(FintechModuleRegistry.NoModuleTypes.selector);
        attestAs(FIRST_ATTESTER, address(module), 0, 0);
    }

    function testAttestingWithAnExpiryInThePastIsRefused() public {
        VM.expectRevert(abi.encodeWithSelector(FintechModuleRegistry.AlreadyExpired.selector, uint48(START)));
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, uint48(START));
    }

    function testAModuleWithoutAttestationsIsRefused() public {
        VM.expectRevert(thresholdNotMet(0, 1));
        registry.check(address(module), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
    }

    function testAnAttestedModuleOfTheRightTypePasses() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        registry.check(address(module), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
        registry.check(address(module), attester(FIRST_ATTESTER), 1);
    }

    function testAnAttestationDoesNotCoverAnotherModuleType() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        VM.expectRevert(
            abi.encodeWithSelector(
                FintechModuleRegistry.ModuleTypeMismatch.selector, address(module), FIRST_ATTESTER, 1 << HOOK_TYPE
            )
        );
        registry.check(address(module), HOOK_TYPE, attester(FIRST_ATTESTER), 1);
    }

    function testModuleTypesAtOrAboveTheLimitAreRefused() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        VM.expectRevert(abi.encodeWithSelector(FintechModuleRegistry.ModuleTypeOutOfRange.selector, 256));
        registry.check(address(module), 256, attester(FIRST_ATTESTER), 1);
    }

    function testARevokedAttestationIsRefused() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        revokeAs(FIRST_ATTESTER, address(module));
        VM.expectRevert(
            abi.encodeWithSelector(
                FintechModuleRegistry.AttestationWasRevoked.selector, address(module), FIRST_ATTESTER
            )
        );
        registry.check(address(module), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
    }

    function testRevocationCannotBeUndoneByAttestingAgain() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        revokeAs(FIRST_ATTESTER, address(module));
        VM.expectRevert(
            abi.encodeWithSelector(
                FintechModuleRegistry.AttestationWasRevoked.selector, address(module), FIRST_ATTESTER
            )
        );
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
    }

    function testOnlyTheAttesterCanRevokeItsOwnAttestation() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        VM.expectRevert(
            abi.encodeWithSelector(FintechModuleRegistry.NotAttested.selector, address(module), SECOND_ATTESTER)
        );
        revokeAs(SECOND_ATTESTER, address(module));
        registry.check(address(module), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
    }

    function testRevokingTwiceIsRefused() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        revokeAs(FIRST_ATTESTER, address(module));
        VM.expectRevert(
            abi.encodeWithSelector(
                FintechModuleRegistry.AttestationWasRevoked.selector, address(module), FIRST_ATTESTER
            )
        );
        revokeAs(FIRST_ATTESTER, address(module));
    }

    function testAnAttestationExpiresAfterItsExpiryTime() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, uint48(START + 100));
        VM.warp(START + 100);
        registry.check(address(module), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
        VM.warp(START + 101);
        VM.expectRevert(thresholdNotMet(0, 1));
        registry.check(address(module), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
    }

    function testChangedCodeAtTheAttestedAddressIsNotAttested() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        VM.etch(address(module), hex"60006000fd");
        VM.expectRevert(thresholdNotMet(0, 1));
        registry.check(address(module), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
    }

    function testTheSameBytecodeAtAnotherAddressIsNotAttested() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        InertModule twin = new InertModule();
        VM.expectRevert(thresholdNotMet(0, 1));
        registry.check(address(twin), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
    }

    function testAnAttestationByAnUntrustedAttesterDoesNotCount() public {
        attestAs(SECOND_ATTESTER, address(module), VALIDATOR_BIT, 0);
        VM.expectRevert(thresholdNotMet(0, 1));
        registry.check(address(module), VALIDATOR_TYPE, attester(FIRST_ATTESTER), 1);
    }

    function testTheThresholdCountsDistinctAttesters() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        VM.expectRevert(thresholdNotMet(1, 2));
        registry.check(address(module), VALIDATOR_TYPE, pair(FIRST_ATTESTER, SECOND_ATTESTER), 2);
        attestAs(SECOND_ATTESTER, address(module), VALIDATOR_BIT, 0);
        registry.check(address(module), VALIDATOR_TYPE, pair(FIRST_ATTESTER, SECOND_ATTESTER), 2);
    }

    function testOneRevocationVetoesEvenWhenTheThresholdIsOtherwiseMet() public {
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        attestAs(SECOND_ATTESTER, address(module), VALIDATOR_BIT, 0);
        revokeAs(SECOND_ATTESTER, address(module));
        VM.expectRevert(
            abi.encodeWithSelector(
                FintechModuleRegistry.AttestationWasRevoked.selector, address(module), SECOND_ATTESTER
            )
        );
        registry.check(address(module), VALIDATOR_TYPE, pair(FIRST_ATTESTER, SECOND_ATTESTER), 1);
    }

    function testUnsortedAttestersAreRefused() public {
        VM.expectRevert(FintechModuleRegistry.InvalidAttesterSet.selector);
        registry.check(address(module), pair(SECOND_ATTESTER, FIRST_ATTESTER), 1);
    }

    function testDuplicateAttestersAreRefused() public {
        VM.expectRevert(FintechModuleRegistry.InvalidAttesterSet.selector);
        registry.check(address(module), pair(FIRST_ATTESTER, FIRST_ATTESTER), 1);
    }

    function testTheZeroAddressIsNotAnAttester() public {
        VM.expectRevert(FintechModuleRegistry.InvalidAttesterSet.selector);
        registry.check(address(module), attester(address(0)), 1);
    }

    function testAnEmptyAttesterSetIsRefused() public {
        VM.expectRevert(FintechModuleRegistry.InvalidAttesterSet.selector);
        registry.check(address(module), new address[](0), 1);
    }

    function testMoreThanTheMaximumNumberOfAttestersIsRefused() public {
        address[] memory attesters = new address[](17);
        for (uint256 index; index < attesters.length; ++index) {
            attesters[index] = address(uint160(0x1000 + index));
        }
        VM.expectRevert(FintechModuleRegistry.InvalidAttesterSet.selector);
        registry.check(address(module), attesters, 1);
    }

    function testAZeroThresholdIsRefusedBecauseItWouldAcceptEverything() public {
        VM.expectRevert(abi.encodeWithSelector(FintechModuleRegistry.InvalidThreshold.selector, 0));
        registry.check(address(module), attester(FIRST_ATTESTER), 0);
    }

    function testAThresholdAboveTheNumberOfAttestersIsRefused() public {
        VM.expectRevert(abi.encodeWithSelector(FintechModuleRegistry.InvalidThreshold.selector, 2));
        registry.check(address(module), attester(FIRST_ATTESTER), 2);
    }

    function testChecksUseTheAttestersTheAccountTrusts() public {
        trustAs(ACCOUNT, 1, attester(FIRST_ATTESTER));
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        VM.prank(ACCOUNT);
        registry.check(address(module));
        VM.prank(ACCOUNT);
        registry.check(address(module), VALIDATOR_TYPE);
        registry.checkForAccount(ACCOUNT, address(module));
        registry.checkForAccount(ACCOUNT, address(module), VALIDATOR_TYPE);
    }

    function testTrustIsPerAccount() public {
        trustAs(ACCOUNT, 1, attester(FIRST_ATTESTER));
        attestAs(FIRST_ATTESTER, address(module), VALIDATOR_BIT, 0);
        VM.expectRevert(abi.encodeWithSelector(FintechModuleRegistry.NoTrustedAttesters.selector, OTHER_ACCOUNT));
        registry.checkForAccount(OTHER_ACCOUNT, address(module));
        trustAs(OTHER_ACCOUNT, 1, attester(SECOND_ATTESTER));
        VM.expectRevert(thresholdNotMet(0, 1));
        registry.checkForAccount(OTHER_ACCOUNT, address(module));
    }

    function testAnAccountWithoutTrustedAttestersFailsClosed() public {
        VM.expectRevert(abi.encodeWithSelector(FintechModuleRegistry.NoTrustedAttesters.selector, ACCOUNT));
        VM.prank(ACCOUNT);
        registry.check(address(module));
    }

    function testTrustedAttestersCanBeReplaced() public {
        trustAs(ACCOUNT, 1, attester(FIRST_ATTESTER));
        trustAs(ACCOUNT, 2, pair(FIRST_ATTESTER, SECOND_ATTESTER));
        (uint8 threshold, address[] memory attesters) = registry.trustedAttestersOf(ACCOUNT);
        require(threshold == 2 && attesters.length == 2, "the policy must be replaced");
    }

    function testTrustingAnInvalidSetIsRefused() public {
        VM.expectRevert(FintechModuleRegistry.InvalidAttesterSet.selector);
        trustAs(ACCOUNT, 1, pair(SECOND_ATTESTER, FIRST_ATTESTER));
        VM.expectRevert(abi.encodeWithSelector(FintechModuleRegistry.InvalidThreshold.selector, 0));
        trustAs(ACCOUNT, 0, attester(FIRST_ATTESTER));
    }
}
