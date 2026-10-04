// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AgentTrustOracle
/// @notice Sybil-adjusted trust scores for ERC-8004 agents, computed offchain and posted in batches.
/// Scores and confidences are basis points (0..10_000). clusterId groups agents the model believes
/// share an operator; 0 means "no cluster assigned".
contract AgentTrustOracle {
    struct Score {
        uint16 score;
        uint16 confidence;
        uint32 clusterId;
        uint64 epoch;
        uint64 updatedAt;
    }

    uint16 public constant MAX_BPS = 10_000;

    address public immutable identityRegistry;
    address public owner;
    address public updater;

    /// Hash of the model weights/config that produced the latest batch, so a score can be traced to a model.
    bytes32 public modelHash;
    uint64 public epoch;

    mapping(uint256 agentId => Score) private _scores;

    event ScoresPosted(uint64 indexed epoch, bytes32 indexed modelHash, uint256 count);
    event ScoreUpdated(uint256 indexed agentId, uint64 indexed epoch, uint16 score, uint16 confidence, uint32 clusterId);
    event UpdaterChanged(address indexed updater);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error NotUpdater();
    error LengthMismatch();
    error OutOfRange(uint256 agentId);
    error ZeroAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address identityRegistry_, address updater_) {
        if (identityRegistry_ == address(0) || updater_ == address(0)) revert ZeroAddress();
        identityRegistry = identityRegistry_;
        owner = msg.sender;
        updater = updater_;
        emit OwnershipTransferred(address(0), msg.sender);
        emit UpdaterChanged(updater_);
    }

    function postScores(
        uint256[] calldata agentIds,
        uint16[] calldata scores,
        uint16[] calldata confidences,
        uint32[] calldata clusterIds,
        bytes32 modelHash_
    ) external {
        if (msg.sender != updater) revert NotUpdater();
        uint256 n = agentIds.length;
        if (scores.length != n || confidences.length != n || clusterIds.length != n) revert LengthMismatch();

        uint64 e = ++epoch;
        modelHash = modelHash_;
        for (uint256 i; i < n; ++i) {
            if (scores[i] > MAX_BPS || confidences[i] > MAX_BPS) revert OutOfRange(agentIds[i]);
            // forge-lint: disable-next-line(unsafe-typecast)
            _scores[agentIds[i]] = Score(scores[i], confidences[i], clusterIds[i], e, uint64(block.timestamp));
            emit ScoreUpdated(agentIds[i], e, scores[i], confidences[i], clusterIds[i]);
        }
        emit ScoresPosted(e, modelHash_, n);
    }

    function getScore(uint256 agentId) external view returns (Score memory) {
        return _scores[agentId];
    }

    /// True if the agent has been scored and meets both thresholds. Unscored agents are never trusted.
    function isTrusted(uint256 agentId, uint16 minScore, uint16 minConfidence) external view returns (bool) {
        Score memory s = _scores[agentId];
        return s.epoch != 0 && s.score >= minScore && s.confidence >= minConfidence;
    }

    function setUpdater(address updater_) external onlyOwner {
        if (updater_ == address(0)) revert ZeroAddress();
        updater = updater_;
        emit UpdaterChanged(updater_);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
