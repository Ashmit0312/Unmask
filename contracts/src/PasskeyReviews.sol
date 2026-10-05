// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {WebAuthn} from "solady/utils/WebAuthn.sol";
import {IIdentityRegistry} from "./interfaces/IERC8004.sol";

/// @title PasskeyReviews
/// @notice Reviews of ERC-8004 agents signed with a passkey (Face ID, Touch ID, security key) and verified onchain
/// through Monad's P256 precompile. The reviewer needs no wallet and no gas: anyone may relay a signed review.
///
/// A reviewer is a passkey public key, pseudonymous and the same for every review it signs. Each passkey holds one
/// review per agent and can revise it; every signature covers the reviewer's current version for that agent, so an
/// old signature cannot be replayed. User verification (biometric or PIN) is required on every review.
///
/// This raises the cost of fake reviews from a funded wallet to a user-verified passkey gesture. It is not proof of
/// personhood: software authenticators exist, so the trust oracle still runs its sybil checks over these reviewers.
contract PasskeyReviews {
    struct Review {
        uint8 value; // 0..100
        bytes32 tag;
        uint32 version; // 1 for the first review, +1 per revision
        uint64 updatedAt;
    }

    uint8 public constant MAX_VALUE = 100;

    IIdentityRegistry public immutable identityRegistry;

    mapping(bytes32 reviewer => mapping(uint256 agentId => Review)) private _reviews;

    event PasskeyReview(
        uint256 indexed agentId, bytes32 indexed reviewer, uint8 value, bytes32 tag, uint32 version, bytes32 x, bytes32 y
    );

    error ValueOutOfRange();
    error InvalidPasskeySignature();

    constructor(address identityRegistry_) {
        identityRegistry = IIdentityRegistry(identityRegistry_);
    }

    /// @notice Pseudonymous reviewer id of a passkey public key.
    function reviewerId(bytes32 x, bytes32 y) public pure returns (bytes32) {
        return keccak256(abi.encode(x, y));
    }

    /// @notice The bytes a passkey must sign (as the WebAuthn challenge) to submit or revise this review.
    function challengeFor(uint256 agentId, uint8 value, bytes32 tag, bytes32 x, bytes32 y)
        public
        view
        returns (bytes memory)
    {
        bytes32 reviewer = reviewerId(x, y);
        uint32 version = _reviews[reviewer][agentId].version + 1;
        return abi.encodePacked(
            keccak256(abi.encode(block.chainid, address(this), agentId, value, tag, reviewer, version))
        );
    }

    /// @notice Submit or revise a review. Callable by anyone holding the passkey's signed assertion.
    function submit(
        uint256 agentId,
        uint8 value,
        bytes32 tag,
        bytes32 x,
        bytes32 y,
        WebAuthn.WebAuthnAuth calldata auth
    ) external {
        if (value > MAX_VALUE) revert ValueOutOfRange();
        identityRegistry.ownerOf(agentId); // reverts for an agent that does not exist

        bytes memory challenge = challengeFor(agentId, value, tag, x, y);
        if (!WebAuthn.verify(challenge, true, auth, x, y)) revert InvalidPasskeySignature();

        bytes32 reviewer = reviewerId(x, y);
        Review storage r = _reviews[reviewer][agentId];
        uint32 version = r.version + 1;
        _reviews[reviewer][agentId] = Review(value, tag, version, uint64(block.timestamp));
        emit PasskeyReview(agentId, reviewer, value, tag, version, x, y);
    }

    function getReview(bytes32 reviewer, uint256 agentId) external view returns (Review memory) {
        return _reviews[reviewer][agentId];
    }
}
