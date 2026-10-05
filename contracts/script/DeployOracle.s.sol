// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {AgentTrustOracle} from "../src/AgentTrustOracle.sol";

/// forge script script/DeployOracle.s.sol --rpc-url monad_testnet --account monad-deployer --broadcast
/// ORACLE_UPDATER (optional) = address the offchain poster signs with; defaults to the deployer.
contract DeployOracle is Script {
    address constant IDENTITY_TESTNET = 0x8004A818BFB912233c491871b3d84c89A494BD9e;

    function run() external returns (AgentTrustOracle oracle) {
        address updater = vm.envOr("ORACLE_UPDATER", address(0));
        vm.startBroadcast();
        // The broadcasting account (--account / --private-key), not the script contract's msg.sender.
        (, address deployer,) = vm.readCallers();
        if (updater == address(0)) updater = deployer;
        oracle = new AgentTrustOracle(IDENTITY_TESTNET, updater);
        vm.stopBroadcast();
        console.log("AgentTrustOracle:", address(oracle));
        console.log("owner:", deployer);
        console.log("updater:", updater);
    }
}
