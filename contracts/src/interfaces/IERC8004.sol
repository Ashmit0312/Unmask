// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// Minimal interfaces for the canonical ERC-8004 registries.
/// Monad testnet (10143): Identity 0x8004A818BFB912233c491871b3d84c89A494BD9e,
///                        Reputation 0x8004B663056A597Dffe9eCcC1965A193B7388713
/// Monad mainnet (143):   Identity 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432,
///                        Reputation 0x8004BAa17C55a88189AE136b182e5fdA19dE9b63

interface IIdentityRegistry {
    event Registered(uint256 indexed agentId, string agentURI, address indexed owner);

    function register(string memory agentURI) external returns (uint256 agentId);
    function ownerOf(uint256 agentId) external view returns (address);
    function tokenURI(uint256 agentId) external view returns (string memory);
    function getAgentWallet(uint256 agentId) external view returns (address);
    function isAuthorizedOrOwner(address spender, uint256 agentId) external view returns (bool);
}

interface IReputationRegistry {
    event NewFeedback(
        uint256 indexed agentId,
        address indexed clientAddress,
        uint64 feedbackIndex,
        int128 value,
        uint8 valueDecimals,
        string indexed indexedTag1,
        string tag1,
        string tag2,
        string endpoint,
        string feedbackURI,
        bytes32 feedbackHash
    );
    event FeedbackRevoked(uint256 indexed agentId, address indexed clientAddress, uint64 indexed feedbackIndex);

    function giveFeedback(
        uint256 agentId,
        int128 value,
        uint8 valueDecimals,
        string calldata tag1,
        string calldata tag2,
        string calldata endpoint,
        string calldata feedbackURI,
        bytes32 feedbackHash
    ) external;

    function revokeFeedback(uint256 agentId, uint64 feedbackIndex) external;
    function getIdentityRegistry() external view returns (address);
    function getClients(uint256 agentId) external view returns (address[] memory);
    function getLastIndex(uint256 agentId, address clientAddress) external view returns (uint64);

    /// Reverts unless clientAddresses is non-empty: the caller decides whose ratings count.
    function getSummary(uint256 agentId, address[] calldata clientAddresses, string calldata tag1, string calldata tag2)
        external
        view
        returns (uint64 count, int128 summaryValue, uint8 summaryValueDecimals);
}
