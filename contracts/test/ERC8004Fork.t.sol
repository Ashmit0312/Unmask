// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IIdentityRegistry, IReputationRegistry} from "../src/interfaces/IERC8004.sol";

/// Runs against the live ERC-8004 registries on a Monad testnet fork (needs network):
///   forge test --match-contract ERC8004Fork
contract ERC8004ForkTest is Test {
    IIdentityRegistry constant IDENTITY = IIdentityRegistry(0x8004A818BFB912233c491871b3d84c89A494BD9e);
    IReputationRegistry constant REPUTATION = IReputationRegistry(0x8004B663056A597Dffe9eCcC1965A193B7388713);

    address operator = makeAddr("operator");
    address client = makeAddr("client");

    function setUp() public {
        vm.createSelectFork("monad_testnet");
    }

    function test_registriesAreWired() public view {
        assertEq(REPUTATION.getIdentityRegistry(), address(IDENTITY));
    }

    function test_registerAndRate() public {
        vm.prank(operator);
        uint256 agentId = IDENTITY.register("ipfs://agent-card.json");
        assertEq(IDENTITY.ownerOf(agentId), operator);

        vm.prank(client);
        REPUTATION.giveFeedback(agentId, 87, 0, "quality", "", "", "", bytes32(0));

        address[] memory clients = new address[](1);
        clients[0] = client;
        (uint64 count, int128 value,) = REPUTATION.getSummary(agentId, clients, "", "");
        assertEq(count, 1);
        assertEq(value, 87);
    }

    function test_ownerCannotRateOwnAgent() public {
        vm.prank(operator);
        uint256 agentId = IDENTITY.register("ipfs://agent-card.json");

        vm.prank(operator);
        vm.expectRevert(bytes("Self-feedback not allowed"));
        REPUTATION.giveFeedback(agentId, 100, 0, "", "", "", "", bytes32(0));
    }

    /// The loophole our model targets: one operator with a second wallet rates its own agent freely.
    function test_sockPuppetCanRateOwnAgent() public {
        vm.prank(operator);
        uint256 agentId = IDENTITY.register("ipfs://agent-card.json");

        address sock = makeAddr("operator-sock-puppet");
        vm.prank(sock);
        REPUTATION.giveFeedback(agentId, 100, 0, "", "", "", "", bytes32(0));
        assertEq(REPUTATION.getLastIndex(agentId, sock), 1);
    }

    function test_summaryNeedsClientList() public {
        vm.prank(operator);
        uint256 agentId = IDENTITY.register("ipfs://agent-card.json");

        vm.expectRevert(bytes("clientAddresses required"));
        REPUTATION.getSummary(agentId, new address[](0), "", "");
    }
}
