// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {FrostSecp256k1} from "../contracts/FrostSecp256k1.sol";

contract FrostSecp256k1Harness {
    function verify(FrostSecp256k1.Statement memory statement, FrostSecp256k1.Signature memory signature)
        external
        view
        returns (bool)
    {
        return FrostSecp256k1.verify(statement, signature);
    }
}

contract FrostSecp256k1Test {
    uint256 private constant GROUP_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
    uint256 private constant FIELD_PRIME = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F;

    struct Vector {
        bytes publicKey;
        bytes message;
        bytes commitment;
        uint256 response;
    }

    FrostSecp256k1Harness private harness = new FrostSecp256k1Harness();

    function rfc9591Vector() private pure returns (Vector memory) {
        return Vector({
            publicKey: hex"02f37c34b66ced1fb51c34a90bdae006901f10625cc06c4f64663b0eae87d87b4f",
            message: hex"74657374",
            commitment: hex"0205b6d04d3774c8929413e3c76024d54149c372d57aae62574ed74319b5ea14d0",
            response: 0xc65dde8492a7471437e6c2fe3da49b90d23f642b5c6dbe7e36089f096dd97324
        });
    }

    function generatedVectors() private pure returns (Vector[6] memory vectors) {
        vectors[0] = Vector({
            publicKey: hex"02658f6a30f108fcbf614b41fd84531637725f4e14cde55e555ed8767c95655649",
            message: hex"6c1a5dbabe96356415b37fe811209c17006108445dd42dbd5e517adf5a8de348",
            commitment: hex"0205fd2be6dee001af928b6b4864c1d776bba8dfd4a564326b58d103fb13dfb3ce",
            response: 0x59561645319818c7f7b299c98b635464426e35706b35543275f0ea1184f21649
        });
        vectors[1] = Vector({
            publicKey: hex"036318970891881b199bb96a9261927fcf99d596d33f0e9f0f61241c96f395bed9",
            message: hex"c97e17ce9b5743185c96edcf99278a1aefd775e797f458e97e968244641d083a",
            commitment: hex"0287154deeaa1765234715ef9fe0e53deebc3fb8bea5c0898d0f3ce79df829daef",
            response: 0x394a3c30c44e957fce568b1b76bee23a611545ac602033710f1d6d071d9c21d4
        });
        vectors[2] = Vector({
            publicKey: hex"0376f96afbd426d45b1e6e95f60844e590ba8034e8020486630567078691b30511",
            message: hex"30bff80ad842deb8e0096a5cee26749d21c10927b1ca73bdf423d32b5abd822d",
            commitment: hex"0372da086e294d20dfa1444378045399043d8387e88059123c52b36046708625d1",
            response: 0x5de8be7392cd3d51d68b3d8ff940eb2a7ec7a64c2e9562d5aa5d1e3e8baac454
        });
        vectors[3] = Vector({
            publicKey: hex"02a5fc8adf23851fc8702ef177d468844f5d753495cec9e874cb855d740b38dd8b",
            message: hex"c9dbbc0a11a935fc0ce58539c44ba1b8f8f31ef49002ae207812bc72f18df9fc",
            commitment: hex"0355026fbbe718078f17c78351f79de6670d30adb7105a2dc6e3db27b3d45072a5",
            response: 0x4fe75c2a4330302adb06895e1bcea5a7ca74861e0c8fb7033023ce5484fad7d0
        });
        vectors[4] = Vector({
            publicKey: hex"032ec2a2a9614f99cd81f35c9a51af2be8fb1d25d6ac36e4df8fdfa0638feddb79",
            message: hex"8c2d206df532ae2f0639fd7e187b5ac62dac6a516a5f4581694fc61bfb63a345",
            commitment: hex"029835c8dcaec1818b0d1a337bb8b42e03b2882bf0a5c16fe862aa5dc7fafd2e98",
            response: 0xf4ddb2c7598a0205b76b300d3afb8ecd853ff4f3c61cd1ec93a017367907210d
        });
        vectors[5] = Vector({
            publicKey: hex"0202ac8b8379cb1e285f9c36e5be6613cd558cae50a08682fd2fe49a3757cd4abe",
            message: hex"14c8b8e72a3f4c28edfe623fab1418ce69c9b0b72f35fd878b9730917a83daf0",
            commitment: hex"033176c76e9e50af00ef18bea48df7f510776c5590a348917f2b3f74fbb4017da3",
            response: 0xda0a8f82322227cc8435d3383d4d5ffb0e76d7263f24ee780e665809c72d20b8
        });
    }

    function accepts(Vector memory vector) private view returns (bool) {
        return harness.verify(
            FrostSecp256k1.Statement(vector.publicKey, vector.message),
            FrostSecp256k1.Signature(vector.commitment, vector.response)
        );
    }

    function testRfc9591VectorVerifies() public view {
        require(accepts(rfc9591Vector()), "RFC 9591 E.5 signature must verify");
    }

    function testGeneratedVectorsVerifyForEveryParityCombination() public view {
        Vector[6] memory vectors = generatedVectors();
        for (uint256 index = 0; index < vectors.length; index++) {
            require(accepts(vectors[index]), "generated vector must verify");
        }
    }

    function testTamperedResponseIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.response = addmod(vector.response, 1, GROUP_ORDER);
        require(!accepts(vector), "tampered response must fail");
    }

    function testDifferentMessageIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.message = hex"74657375";
        require(!accepts(vector), "different message must fail");
    }

    function testDifferentPublicKeyIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.publicKey = generatedVectors()[0].publicKey;
        require(!accepts(vector), "different public key must fail");
    }

    function testFlippedCommitmentParityIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.commitment[0] = 0x03;
        require(!accepts(vector), "flipped commitment parity must fail");
    }

    function testFlippedPublicKeyParityIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.publicKey[0] = 0x03;
        require(!accepts(vector), "flipped public key parity must fail");
    }

    function testAlteredCommitmentCoordinatesAreRejected() public view {
        for (uint8 delta = 1; delta <= 4; delta++) {
            Vector memory vector = rfc9591Vector();
            vector.commitment[32] = bytes1(uint8(vector.commitment[32]) + delta);
            require(!accepts(vector), "altered commitment must fail");
        }
    }

    function testZeroResponseIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.response = 0;
        require(!accepts(vector), "zero response must fail");
    }

    function testResponseAtGroupOrderIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.response = GROUP_ORDER;
        require(!accepts(vector), "response at the group order must fail");
    }

    function testResponseAboveGroupOrderIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.response = type(uint256).max;
        require(!accepts(vector), "response above the group order must fail");
    }

    function testMalformedEncodingsAreRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.commitment = hex"0405b6d04d3774c8929413e3c76024d54149c372d57aae62574ed74319b5ea14d0";
        require(!accepts(vector), "unknown commitment prefix must fail");

        vector = rfc9591Vector();
        vector.commitment = hex"0205b6d04d3774c8929413e3c76024d54149c372d57aae62574ed74319b5ea14";
        require(!accepts(vector), "short commitment must fail");

        vector = rfc9591Vector();
        vector.publicKey = hex"04f37c34b66ced1fb51c34a90bdae006901f10625cc06c4f64663b0eae87d87b4f";
        require(!accepts(vector), "unknown public key prefix must fail");

        vector = rfc9591Vector();
        vector.publicKey = hex"02f37c34b66ced1fb51c34a90bdae006901f10625cc06c4f64663b0eae87d87b";
        require(!accepts(vector), "short public key must fail");
    }

    function testCoordinatesAtOrAboveTheFieldPrimeAreRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.commitment = abi.encodePacked(bytes1(0x02), bytes32(FIELD_PRIME));
        require(!accepts(vector), "commitment x at the field prime must fail");

        vector = rfc9591Vector();
        vector.publicKey = abi.encodePacked(bytes1(0x02), bytes32(FIELD_PRIME));
        require(!accepts(vector), "public key x at the field prime must fail");
    }

    function testPublicKeyCoordinateAtGroupOrderIsRejected() public view {
        Vector memory vector = rfc9591Vector();
        vector.publicKey = abi.encodePacked(bytes1(0x02), bytes32(GROUP_ORDER));
        require(!accepts(vector), "public key x at the group order must fail");
    }

    function testVerificationGasForTheRfcVector() public view {
        uint256 gasBefore = gasleft();
        accepts(rfc9591Vector());
        uint256 gasUsed = gasBefore - gasleft();
        require(gasUsed > 0, "gas must be measurable");
    }
}
