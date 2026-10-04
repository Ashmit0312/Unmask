// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {AgentTrustOracle} from "../src/AgentTrustOracle.sol";

/// forge script script/DeployOracle.s.sol --rpc-url monad_testnet --account monad-deployer --broadcast
/// ORACLE_UPDATER (optional) = address the offchain poster signs with; defaults to the deployer.
contract DeployOracle is Script {
    address constant IDENTITY_TESTNET = 0x8004A818BFB912233c491871b3d84c89A494BD9e;

    function run() external returns (AgentTrustOracle oracle) {
        vm.startBroadcast();
        address updater = vm.envOr("ORACLE_UPDATER", msg.sender);
        oracle = new AgentTrustOracle(IDENTITY_TESTNET, updater);
        vm.stopBroadcast();
        console.log("AgentTrustOracle:", address(oracle));
        console.log("updater:", updater);
    }
}
