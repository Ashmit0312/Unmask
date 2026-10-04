// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {AgentTrustOracle} from "../src/AgentTrustOracle.sol";

contract AgentTrustOracleTest is Test {
    AgentTrustOracle oracle;
    address updater = makeAddr("updater");
    address identity = makeAddr("identity");
    bytes32 constant MODEL = keccak256("model-v0");

    function setUp() public {
        oracle = new AgentTrustOracle(identity, updater);
    }

    function _batch(uint16 score) internal pure returns (uint256[] memory, uint16[] memory, uint16[] memory, uint32[] memory) {
        uint256[] memory ids = new uint256[](2);
        uint16[] memory scores = new uint16[](2);
        uint16[] memory confs = new uint16[](2);
        uint32[] memory clusters = new uint32[](2);
        (ids[0], ids[1]) = (1, 2);
        (scores[0], scores[1]) = (score, 1_200);
        (confs[0], confs[1]) = (8_000, 9_500);
        (clusters[0], clusters[1]) = (0, 7);
        return (ids, scores, confs, clusters);
    }

    function test_postAndRead() public {
        (uint256[] memory ids, uint16[] memory s, uint16[] memory c, uint32[] memory k) = _batch(9_000);
        vm.prank(updater);
        oracle.postScores(ids, s, c, k, MODEL);

        AgentTrustOracle.Score memory got = oracle.getScore(2);
        assertEq(got.score, 1_200);
        assertEq(got.confidence, 9_500);
        assertEq(got.clusterId, 7);
        assertEq(got.epoch, 1);
        assertEq(oracle.modelHash(), MODEL);
        assertTrue(oracle.isTrusted(1, 5_000, 5_000));
        assertFalse(oracle.isTrusted(2, 5_000, 5_000));
    }

    function test_unscoredAgentIsNotTrusted() public view {
        assertFalse(oracle.isTrusted(42, 0, 0));
    }

    function test_onlyUpdaterCanPost() public {
        (uint256[] memory ids, uint16[] memory s, uint16[] memory c, uint32[] memory k) = _batch(9_000);
        vm.expectRevert(AgentTrustOracle.NotUpdater.selector);
        oracle.postScores(ids, s, c, k, MODEL);
    }

    function test_rejectsOutOfRangeScore() public {
        (uint256[] memory ids, uint16[] memory s, uint16[] memory c, uint32[] memory k) = _batch(10_001);
        vm.prank(updater);
        vm.expectRevert(abi.encodeWithSelector(AgentTrustOracle.OutOfRange.selector, 1));
        oracle.postScores(ids, s, c, k, MODEL);
    }

    function test_rejectsLengthMismatch() public {
        (uint256[] memory ids, uint16[] memory s, uint16[] memory c,) = _batch(9_000);
        vm.prank(updater);
        vm.expectRevert(AgentTrustOracle.LengthMismatch.selector);
        oracle.postScores(ids, s, c, new uint32[](1), MODEL);
    }

    function test_ownerRotatesUpdater() public {
        address next = makeAddr("next");
        oracle.setUpdater(next);
        assertEq(oracle.updater(), next);

        vm.prank(next);
        vm.expectRevert(AgentTrustOracle.NotOwner.selector);
        oracle.setUpdater(updater);
    }
}
