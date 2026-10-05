// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {PasskeyReviews} from "../src/PasskeyReviews.sol";

/// forge script script/DeployPasskeyReviews.s.sol --rpc-url monad_testnet --account monad-deployer --broadcast
contract DeployPasskeyReviews is Script {
    address constant IDENTITY_TESTNET = 0x8004A818BFB912233c491871b3d84c89A494BD9e;

    function run() external returns (PasskeyReviews reviews) {
        vm.startBroadcast();
        reviews = new PasskeyReviews(IDENTITY_TESTNET);
        vm.stopBroadcast();
        console.log("PasskeyReviews:", address(reviews));
    }
}
