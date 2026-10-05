// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {WebAuthn} from "solady/utils/WebAuthn.sol";
import {Base64} from "solady/utils/Base64.sol";
import {PasskeyReviews} from "../src/PasskeyReviews.sol";

/// Stand-in identity registry: agents 1..10 exist.
contract MockIdentity {
    function ownerOf(uint256 agentId) external pure returns (address) {
        require(agentId >= 1 && agentId <= 10, "ERC721NonexistentToken");
        return address(0xA11CE);
    }
}

contract PasskeyReviewsTest is Test {
    PasskeyReviews reviews;
    uint256 constant PK = 0xC0FFEE;
    uint256 constant N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551; // P256 order
    bytes32 x;
    bytes32 y;
    bytes32 constant TAG = "quality";

    function setUp() public virtual {
        reviews = new PasskeyReviews(address(new MockIdentity()));
        (uint256 px, uint256 py) = vm.publicKeyP256(PK);
        (x, y) = (bytes32(px), bytes32(py));
    }

    /// A WebAuthn assertion over `challenge`, as a browser would produce it. flags: 0x01 user present, 0x04 verified.
    function _assert(bytes memory challenge, uint8 flags, uint256 pk) internal pure returns (WebAuthn.WebAuthnAuth memory a) {
        a.authenticatorData = abi.encodePacked(sha256("unmask.app"), flags, uint32(1));
        a.clientDataJSON = string.concat(
            '{"type":"webauthn.get","challenge":"', Base64.encode(challenge, true, true),
            '","origin":"https://unmask.app","crossOrigin":false}'
        );
        a.challengeIndex = 23;
        a.typeIndex = 1;
        bytes32 digest = sha256(abi.encodePacked(a.authenticatorData, sha256(bytes(a.clientDataJSON))));
        (bytes32 r, bytes32 s) = vm.signP256(pk, digest);
        if (uint256(s) > N / 2) s = bytes32(N - uint256(s)); // low-s form, as verifiers require
        (a.r, a.s) = (r, s);
    }

    function _submit(uint256 agentId, uint8 value) internal {
        bytes memory c = reviews.challengeFor(agentId, value, TAG, x, y);
        reviews.submit(agentId, value, TAG, x, y, _assert(c, 0x05, PK));
    }

    function test_reviewIsVerifiedAndStored() public {
        bytes32 id = reviews.reviewerId(x, y);
        vm.expectEmit(true, true, false, true);
        emit PasskeyReviews.PasskeyReview(3, id, 92, TAG, 1, x, y);
        vm.prank(address(0xBEEF)); // a relayer, not the reviewer, pays the gas
        _submit(3, 92);

        PasskeyReviews.Review memory r = reviews.getReview(id, 3);
        assertEq(r.value, 92);
        assertEq(r.version, 1);
    }

    function test_revisionBumpsVersionAndOldSignatureCannotReplay() public {
        bytes memory c1 = reviews.challengeFor(3, 40, TAG, x, y);
        WebAuthn.WebAuthnAuth memory a1 = _assert(c1, 0x05, PK);
        reviews.submit(3, 40, TAG, x, y, a1);
        _submit(3, 85);
        assertEq(reviews.getReview(reviews.reviewerId(x, y), 3).version, 2);

        vm.expectRevert(PasskeyReviews.InvalidPasskeySignature.selector);
        reviews.submit(3, 40, TAG, x, y, a1); // replaying the first review would roll it back
    }

    function test_tamperedValueFails() public {
        bytes memory c = reviews.challengeFor(3, 10, TAG, x, y);
        WebAuthn.WebAuthnAuth memory a = _assert(c, 0x05, PK);
        vm.expectRevert(PasskeyReviews.InvalidPasskeySignature.selector);
        reviews.submit(3, 100, TAG, x, y, a); // relayer swaps 10 for 100
    }

    function test_userVerificationRequired() public {
        bytes memory c = reviews.challengeFor(3, 70, TAG, x, y);
        WebAuthn.WebAuthnAuth memory a = _assert(c, 0x01, PK); // present, but no biometric / PIN
        vm.expectRevert(PasskeyReviews.InvalidPasskeySignature.selector);
        reviews.submit(3, 70, TAG, x, y, a);
    }

    function test_wrongPasskeyFails() public {
        bytes memory c = reviews.challengeFor(3, 70, TAG, x, y);
        WebAuthn.WebAuthnAuth memory a = _assert(c, 0x05, 0xBAD);
        vm.expectRevert(PasskeyReviews.InvalidPasskeySignature.selector);
        reviews.submit(3, 70, TAG, x, y, a);
    }

    function test_unknownAgentAndRangeRejected() public virtual {
        bytes memory c = reviews.challengeFor(99, 70, TAG, x, y);
        WebAuthn.WebAuthnAuth memory a = _assert(c, 0x05, PK);
        vm.expectRevert(bytes("ERC721NonexistentToken"));
        reviews.submit(99, 70, TAG, x, y, a);

        vm.expectRevert(PasskeyReviews.ValueOutOfRange.selector);
        reviews.submit(3, 101, TAG, x, y, a);
    }

    function test_reviewsArePerAgent() public {
        _submit(3, 90);
        _submit(4, 20);
        bytes32 id = reviews.reviewerId(x, y);
        assertEq(reviews.getReview(id, 3).value, 90);
        assertEq(reviews.getReview(id, 4).value, 20);
        assertEq(reviews.getReview(id, 4).version, 1);
    }
}

/// Against the real chain: Monad's P256 precompile and the live ERC-8004 identity registry.
///   forge test --match-contract PasskeyReviewsFork
contract PasskeyReviewsForkTest is PasskeyReviewsTest {
    function setUp() public override {
        vm.createSelectFork("monad_testnet");
        reviews = new PasskeyReviews(0x8004A818BFB912233c491871b3d84c89A494BD9e);
        (uint256 px, uint256 py) = vm.publicKeyP256(PK);
        (x, y) = (bytes32(px), bytes32(py));
    }

    function test_precompileIsLiveOnMonad() public view {
        // RIP-7212 input: hash, r, s, x, y -> 32-byte 1 on success
        bytes32 h = keccak256("monad");
        (bytes32 r, bytes32 s) = vm.signP256(PK, h);
        if (uint256(s) > N / 2) s = bytes32(N - uint256(s));
        (bool ok, bytes memory out) = address(0x100).staticcall(abi.encode(h, r, s, x, y));
        assertTrue(ok);
        assertEq(abi.decode(out, (uint256)), 1);
    }

    // The mock-registry unknown-agent test does not apply to the real registry's agent set.
    function test_unknownAgentAndRangeRejected() public override {
        bytes memory c = reviews.challengeFor(10_000_000, 70, TAG, x, y);
        WebAuthn.WebAuthnAuth memory a = _assert(c, 0x05, PK);
        vm.expectRevert();
        reviews.submit(10_000_000, 70, TAG, x, y, a);
    }
}
