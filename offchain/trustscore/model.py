"""Sybil-aware trust scores: resolve wallets into likely operators, then give each operator one vote per agent.

1. Hubs: funders that bankroll wallets in many separate bursts (exchanges, faucets) are not identity evidence.
2. Operator clusters (union-find) over three kinds of evidence:
   - funding from a non-hub wallet (treasury -> puppets, treasury -> agent owners)
   - two raters repeatedly hitting the same agents within a short window with near-identical values
   - two raters funded by the same hub in the same burst that also co-rated an agent
3. Ratings from the agent owner's own cluster are self-dealing and dropped.
4. Each remaining cluster's ratings of an agent collapse to one vote, weighted by 1 - the cluster's ring suspicion
   (the share of its ratings that are self-dealing or bloc votes).
5. Votes are shrunk toward the global mean; agents caught self-dealing shrink toward a floor instead.
"""

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from .features import pair_features


@dataclass(frozen=True)
class ModelConfig:
    # Observed block numbers are multiplied by this before the windows below apply. 1 for live data; a replay
    # that compresses simulated time onto fewer chain blocks records its ratio in the manifest.
    block_scale: float = 1.0
    hub_min_degree: int = 8
    # A hub funds in many separate bursts (exchange withdrawals over time); a treasury funds in one or two waves.
    # Counting bursts, not the share in the busiest one, keeps a hub a hub when an attacker routes one big burst
    # of puppet funding through it.
    hub_min_bursts: int = 5
    burst_window: int = 5_000  # blocks; fundings with gaps under this belong to one burst
    corate_window: int = 2_000  # blocks; two ratings of one agent this close together are a co-rating
    corate_value_tol: float = 10.0  # co-ratings must agree this closely on the 0-100 scale
    min_shared_corates: int = 2  # distinct agents two raters must co-rate before they are linked
    prior_strength: float = 3.0
    self_dealing_prior: float = 0.0  # where a self-dealing agent's prior moves to, 0-100
    confidence_k: float = 5.0


@dataclass
class ScoreResult:
    agents: pd.DataFrame  # agent_id, score, confidence, n_ratings, n_votes, effective_votes, risk, ...
    wallets: pd.DataFrame  # wallet, cluster, cluster_raters, suspicion


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def detect_hubs(fund: pd.DataFrame, cfg: ModelConfig) -> set[str]:
    hubs = set()
    for funder, g in fund.groupby("funder"):
        if len(g) < cfg.hub_min_degree:
            continue
        b = np.sort(g.block.to_numpy())
        bursts = 1 + int(np.sum(np.diff(b) > cfg.burst_window))
        if bursts >= cfg.hub_min_bursts:
            hubs.add(funder)
    return hubs


def corate_pairs(fb: pd.DataFrame, cfg: ModelConfig) -> dict[tuple[str, str], set[int]]:
    """For each pair of raters, the agents they rated within corate_window of each other with similar values."""
    pairs: dict[tuple[str, str], set[int]] = defaultdict(set)
    for agent_id, g in fb.groupby("agent_id"):
        g = g.sort_values("block")
        blocks, clients, values = g.block.to_numpy(), g.client.to_numpy(), g.value.to_numpy()
        hi = np.searchsorted(blocks, blocks + cfg.corate_window, side="right")
        for i in range(len(blocks)):
            for j in range(i + 1, hi[i]):
                if clients[i] != clients[j] and abs(values[i] - values[j]) <= cfg.corate_value_tol:
                    key = (clients[i], clients[j]) if clients[i] < clients[j] else (clients[j], clients[i])
                    pairs[key].add(int(agent_id))
    return pairs


def cluster_wallets(
    fb: pd.DataFrame, fund: pd.DataFrame, regs: pd.DataFrame, cfg: ModelConfig, linker=None,
    known_hubs: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Union-find over operator evidence. Non-hub funding always links. Rater-rater links come from the
    hand-written co-rating rules (v1), or from a learned pairwise linker when one is given (v2)."""
    uf = _UnionFind()
    for w in pd.concat([fb.client, regs.owner, fund.wallet, fund.funder]).unique():
        uf.find(w)

    # Fundings found on another chain (Nansen first funder) carry block -1: identity evidence, but no timing.
    hubs = detect_hubs(fund[fund.block >= 0], cfg) | set(known_hubs)
    # A wallet that owns an agent is an identity, never shared infrastructure, however many wallets it funds:
    # on Monad mainnet one agent owner funded 7,665 wallets that each rated its agent.
    hubs -= set(regs.owner)
    for f in fund.itertuples():
        if f.funder not in hubs:
            uf.union(f.wallet, f.funder)

    if linker is not None:
        feats = pair_features(fb, fund, hubs)
        for a, b in feats.index[(linker.predict(feats) >= linker.threshold).to_numpy()]:
            uf.union(a, b)
        return {w: uf.find(w) for w in uf.parent}

    hub_funding = {f.wallet: (f.funder, f.block) for f in fund.itertuples() if f.funder in hubs}
    for (a, b), agents in corate_pairs(fb, cfg).items():
        if len(agents) >= cfg.min_shared_corates:
            uf.union(a, b)
        elif a in hub_funding and b in hub_funding:
            (ha, ba), (hb, bb) = hub_funding[a], hub_funding[b]
            if ha == hb and abs(ba - bb) <= cfg.burst_window:
                uf.union(a, b)

    return {w: uf.find(w) for w in uf.parent}


def score(
    registrations: pd.DataFrame,
    feedback: pd.DataFrame,
    fundings: pd.DataFrame,
    cfg: ModelConfig = ModelConfig(),
    linker=None,
    known_hubs: frozenset[str] = frozenset(),
) -> ScoreResult:
    """known_hubs: funders confirmed as exchanges or other shared infrastructure from outside the ratings data
    (Nansen labels, lifetime transfer counts); they never link the wallets they fund."""
    if cfg.block_scale != 1.0:
        registrations, feedback, fundings = (
            f.assign(block=f.block * cfg.block_scale) for f in (registrations, feedback, fundings)
        )
    fb = feedback[~feedback.revoked.astype(bool)] if "revoked" in feedback else feedback
    cluster = cluster_wallets(fb, fundings, registrations, cfg, linker, known_hubs)

    fb = fb.assign(cluster=fb.client.map(cluster))
    owner_cluster = registrations.set_index("agent_id").owner.map(cluster)
    fb["self_dealing"] = fb.cluster.to_numpy() == fb.agent_id.map(owner_cluster).to_numpy()

    # Rings: clusters with two or more rating wallets. A rating is coordinated when it is self-dealing or part of a
    # bloc (two or more wallets of one cluster rating the same agent: one operator, several voices). A ring's
    # suspicion is the share of its ratings that are coordinated. Suspicion only ever discounts the ring's own
    # votes, so pointing a ring at someone else's agent cannot move that agent, up or down.
    raters_per_cluster = fb.groupby("cluster").client.nunique()
    fb["ring"] = fb.cluster.map(raters_per_cluster).to_numpy() >= 2
    share = fb[fb.ring].groupby(["agent_id", "cluster"]).size() / fb.groupby("agent_id").size()
    bloc_size = fb.groupby(["agent_id", "cluster"]).client.transform("nunique")
    fb["coordinated"] = fb.self_dealing | (bloc_size >= 2)
    cluster_susp = fb[fb.ring].groupby("cluster").coordinated.mean()

    # One vote per (agent, cluster), weighted by 1 - suspicion; self-dealing ratings excluded.
    votes = fb[~fb.self_dealing].groupby(["agent_id", "cluster"]).value.mean().reset_index()
    votes["w"] = 1.0 - votes.cluster.map(cluster_susp).fillna(0.0)
    global_prior = np.average(votes.value, weights=votes.w) if votes.w.sum() > 0 else 50.0
    v = votes.assign(wv=votes.w * votes.value).groupby("agent_id")[["wv", "w"]].sum()

    agents = registrations[["agent_id"]].drop_duplicates().set_index("agent_id")
    agents["n_ratings"] = fb.groupby("agent_id").size()
    agents["n_votes"] = votes.groupby("agent_id").size()
    agents = agents.fillna({"n_ratings": 0, "n_votes": 0}).astype({"n_ratings": int, "n_votes": int})
    agents["effective_votes"] = v.w.reindex(agents.index).fillna(0.0)
    agents["self_dealing_share"] = fb.groupby("agent_id").self_dealing.mean()
    agents["ring_share"] = share.groupby(level="agent_id").max()
    agents = agents.fillna({"self_dealing_share": 0.0, "ring_share": 0.0})
    agents["risk"] = agents[["self_dealing_share", "ring_share"]].max(axis=1)

    # An operator caught rating its own agent is evidence against the agent: its prior moves toward the floor.
    # Only self-dealing does this; it needs a funding link to the owner, which an outsider cannot plant.
    prior = global_prior * (1 - agents.self_dealing_share) + cfg.self_dealing_prior * agents.self_dealing_share
    wv = v.wv.reindex(agents.index).fillna(0.0)
    agents["score"] = (wv + cfg.prior_strength * prior) / (agents.effective_votes + cfg.prior_strength)
    agents["confidence"] = agents.effective_votes / (agents.effective_votes + cfg.confidence_k)

    wallets = pd.DataFrame({"wallet": list(cluster), "cluster": list(cluster.values())})
    wallets["cluster_raters"] = wallets.cluster.map(raters_per_cluster).fillna(0).astype(int)
    wallets["suspicion"] = wallets.cluster.map(cluster_susp).fillna(0.0)

    return ScoreResult(agents.reset_index(), wallets)
