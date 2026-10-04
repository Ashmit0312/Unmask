"""Synthetic ERC-8004 world with ground truth: honest agents and clients, plus sybil operators running puppet raters.

Emits the same records the indexer reads from chain (registrations, feedback, fundings), so the model cannot
tell simulated from real. Labels live separately in GroundTruth.

    python -m trustscore.simulate --seed 0 --out ../data/sim0
"""

import argparse
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd
from eth_utils import to_checksum_address

from .schema import AgentRegistered, Feedback, Funding

BLOCKS_PER_DAY = 216_000  # Monad targets 0.4 s blocks


@dataclass(frozen=True)
class SimConfig:
    seed: int = 0
    start_block: int = 1_000_000
    duration_blocks: int = 7 * BLOCKS_PER_DAY

    # Honest side
    n_honest_operators: int = 60
    honest_agents_per_operator: tuple[int, int] = (1, 3)
    n_clients: int = 400
    ratings_per_client: float = 5.0  # Poisson mean
    rating_noise: float = 8.0  # std of honest ratings around true quality, 0-100 scale
    n_exchanges: int = 5  # shared hot wallets that fund many unrelated honest wallets
    p_personal_funding: float = 0.3  # honest wallet funded by its own unique wallet instead of an exchange

    # Sybil side
    n_sybil_operators: int = 4
    sybil_agents: tuple[int, int] = (2, 5)
    sybil_puppets: tuple[int, int] = (8, 20)
    sybil_quality: tuple[float, float] = (0.1, 0.4)
    p_puppet_rates_own: float = 0.8  # chance a puppet rates each of its operator's agents
    p_camouflage: float = 0.3  # chance a puppet also rates honest agents honestly, to blend in
    p_badmouth: float = 0.2  # chance a puppet down-rates a popular honest competitor
    p_hub_funding: float = 0.0  # chance a puppet is funded from an exchange rather than its treasury
    funding_hops: int = 1  # treasury -> (hops-1) layers of intermediate wallets -> puppet
    burst_blocks: int = 2_000  # puppets rate their own agents within this window after funding


@dataclass
class GroundTruth:
    operator_of_wallet: dict[str, str] = field(default_factory=dict)
    operator_of_agent: dict[int, str] = field(default_factory=dict)
    agent_quality: dict[int, float] = field(default_factory=dict)
    sybil_operators: set[str] = field(default_factory=set)
    hubs: set[str] = field(default_factory=set)

    def is_sybil_agent(self, agent_id: int) -> bool:
        return self.operator_of_agent[agent_id] in self.sybil_operators

    def is_sybil_wallet(self, wallet: str) -> bool:
        return self.operator_of_wallet.get(wallet) in self.sybil_operators


@dataclass
class World:
    config: SimConfig
    registrations: list[AgentRegistered]
    feedback: list[Feedback]
    fundings: list[Funding]
    truth: GroundTruth


class _Builder:
    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.truth = GroundTruth()
        self.registrations: list[AgentRegistered] = []
        self.feedback: list[Feedback] = []
        self.fundings: list[Funding] = []
        self._next_agent_id = 1
        self._agent_block: dict[int, int] = {}
        self._agent_owner: dict[int, str] = {}

    # primitives

    def _hex(self, n_bytes: int) -> str:
        return self.rng.bytes(n_bytes).hex()

    def wallet(self, operator: str) -> str:
        addr = to_checksum_address("0x" + self._hex(20))
        self.truth.operator_of_wallet[addr] = operator
        return addr

    def block_at(self, frac_lo: float, frac_hi: float) -> int:
        lo = self.cfg.start_block + int(frac_lo * self.cfg.duration_blocks)
        hi = self.cfg.start_block + int(frac_hi * self.cfg.duration_blocks)
        return int(self.rng.integers(lo, max(lo + 1, hi)))

    @property
    def end_block(self) -> int:
        return self.cfg.start_block + self.cfg.duration_blocks

    def fund(self, wallet: str, funder: str, block: int, amount: float | None = None) -> None:
        amount = amount if amount is not None else round(float(self.rng.uniform(0.5, 5.0)), 3)
        self.fundings.append(Funding(wallet, funder, amount, block, "0x" + self._hex(32)))

    def register(self, owner: str, operator: str, quality: float, block: int) -> int:
        agent_id = self._next_agent_id
        self._next_agent_id += 1
        self.registrations.append(
            AgentRegistered(agent_id, owner, f"ipfs://agent-{agent_id}.json", block, "0x" + self._hex(32))
        )
        self.truth.operator_of_agent[agent_id] = operator
        self.truth.agent_quality[agent_id] = quality
        self._agent_block[agent_id] = block
        self._agent_owner[agent_id] = owner
        return agent_id

    def rate(self, agent_id: int, client: str, value: float, block: int) -> None:
        assert client != self._agent_owner[agent_id], "registry forbids self-feedback"
        block = max(block, self._agent_block[agent_id] + 1)
        if block >= self.end_block:
            return
        value = float(np.clip(round(value), 0, 100))
        # index is assigned after sorting by block, as the registry numbers them in chain order
        self.feedback.append(Feedback(agent_id, client, 0, value, "quality", "", block, "0x" + self._hex(32)))

    def honest_value(self, agent_id: int) -> float:
        return self.truth.agent_quality[agent_id] * 100 + self.rng.normal(0, self.cfg.rating_noise)

    # world

    def build(self) -> World:
        cfg, rng = self.cfg, self.rng

        hubs = [self.wallet(f"hub{j}") for j in range(cfg.n_exchanges)]
        self.truth.hubs.update(hubs)

        def fund_like_a_person(wallet: str, operator: str, block: int) -> None:
            if rng.random() < cfg.p_personal_funding:
                self.fund(wallet, self.wallet(operator), block)
            else:
                self.fund(wallet, hubs[rng.integers(len(hubs))], block)

        # Honest operators and their agents.
        honest_agents: list[int] = []
        popularity: dict[int, float] = {}
        for i in range(cfg.n_honest_operators):
            op = f"honest{i}"
            owner = self.wallet(op)
            reg_block = self.block_at(0.0, 0.3)
            fund_like_a_person(owner, op, reg_block - int(rng.integers(100, 5_000)))
            lo, hi = cfg.honest_agents_per_operator
            for _ in range(rng.integers(lo, hi + 1)):
                quality = float(rng.beta(4, 2))
                aid = self.register(owner, op, quality, reg_block + int(rng.integers(0, 2_000)))
                honest_agents.append(aid)
                popularity[aid] = float(rng.lognormal(0, 1)) * (0.5 + quality)

        # Sybil operators: low-quality agents, owner wallets and puppet raters funded from one treasury.
        sybil_plans = []
        for i in range(cfg.n_sybil_operators):
            op = f"sybil{i}"
            self.truth.sybil_operators.add(op)
            treasury = self.wallet(op)
            attack_start = self.block_at(0.2, 0.7)
            self.fund(treasury, hubs[rng.integers(len(hubs))], attack_start - int(rng.integers(5_000, 50_000)),
                      amount=round(float(rng.uniform(50, 200)), 3))

            # Funding layers between treasury and the wallets it bankrolls.
            layer = [treasury]
            for h in range(cfg.funding_hops - 1):
                nxt = [self.wallet(op) for _ in range(max(1, 3 - h))]
                for w in nxt:
                    self.fund(w, layer[rng.integers(len(layer))], attack_start - int(rng.integers(500, 4_000)))
                layer = nxt

            def bankroll(wallet: str, block: int) -> None:
                if rng.random() < cfg.p_hub_funding:
                    self.fund(wallet, hubs[rng.integers(len(hubs))], block)
                else:
                    self.fund(wallet, layer[rng.integers(len(layer))], block, amount=round(float(rng.uniform(0.2, 1.0)), 3))

            agents = []
            for _ in range(rng.integers(cfg.sybil_agents[0], cfg.sybil_agents[1] + 1)):
                owner = self.wallet(op)
                b = attack_start + int(rng.integers(0, 500))
                bankroll(owner, b - int(rng.integers(10, 200)))
                aid = self.register(owner, op, float(rng.uniform(*cfg.sybil_quality)), b)
                agents.append(aid)
                popularity[aid] = float(rng.lognormal(0, 1)) * 0.3

            puppets = []
            for _ in range(rng.integers(cfg.sybil_puppets[0], cfg.sybil_puppets[1] + 1)):
                p = self.wallet(op)
                fb = attack_start + int(rng.integers(500, 3_000))
                bankroll(p, fb)
                puppets.append((p, fb))
            sybil_plans.append((agents, puppets))

        all_agents = honest_agents + [a for agents, _ in sybil_plans for a in agents]

        # Honest clients rate agents that exist at the time, weighted by popularity, honestly.
        for i in range(cfg.n_clients):
            op = f"client{i}"
            c = self.wallet(op)
            funded = self.block_at(0.0, 0.6)
            fund_like_a_person(c, op, funded)
            times = np.sort(rng.integers(funded + 1, self.end_block, size=rng.poisson(cfg.ratings_per_client)))
            rated: set[int] = set()
            for t in times:
                pool = [a for a in all_agents if self._agent_block[a] < t and a not in rated]
                if not pool:
                    continue
                w = np.array([popularity[a] for a in pool])
                aid = pool[rng.choice(len(pool), p=w / w.sum())]
                rated.add(aid)
                self.rate(aid, c, self.honest_value(aid), int(t))

        # Sybil puppets: burst of near-perfect ratings for their own agents, plus optional camouflage and badmouthing.
        top_honest = sorted(honest_agents, key=popularity.get, reverse=True)[:10]
        for agents, puppets in sybil_plans:
            for p, funded in puppets:
                for aid in agents:
                    if rng.random() < cfg.p_puppet_rates_own:
                        self.rate(aid, p, rng.uniform(90, 100), funded + int(rng.integers(1, cfg.burst_blocks)))
                if rng.random() < cfg.p_camouflage:
                    for aid in rng.choice(honest_agents, size=rng.integers(1, 4), replace=False):
                        self.rate(int(aid), p, self.honest_value(int(aid)), int(rng.integers(funded + 1, self.end_block)))
                if rng.random() < cfg.p_badmouth:
                    aid = int(rng.choice(top_honest))
                    self.rate(aid, p, rng.uniform(0, 15), funded + int(rng.integers(1, cfg.burst_blocks)))

        self.registrations.sort(key=lambda r: r.block)
        self.feedback.sort(key=lambda f: f.block)
        counts: dict[tuple[int, str], int] = {}
        for i, f in enumerate(self.feedback):
            key = (f.agent_id, f.client)
            counts[key] = counts.get(key, 0) + 1
            self.feedback[i] = replace(f, index=counts[key])
        self.fundings.sort(key=lambda f: f.block)
        return World(cfg, self.registrations, self.feedback, self.fundings, self.truth)


def simulate(cfg: SimConfig = SimConfig()) -> World:
    return _Builder(cfg).build()


def to_frames(world: World) -> dict[str, pd.DataFrame]:
    t = world.truth
    agents = pd.DataFrame(map(asdict, world.registrations))
    agents["operator"] = agents.agent_id.map(t.operator_of_agent)
    agents["quality"] = agents.agent_id.map(t.agent_quality)
    agents["is_sybil"] = agents.agent_id.map(t.is_sybil_agent)
    wallets = pd.DataFrame({"wallet": list(t.operator_of_wallet), "operator": list(t.operator_of_wallet.values())})
    wallets["is_sybil"] = wallets.operator.isin(t.sybil_operators)
    wallets["is_hub"] = wallets.wallet.isin(t.hubs)
    return {
        "registrations": pd.DataFrame(map(asdict, world.registrations)),
        "feedback": pd.DataFrame(map(asdict, world.feedback)),
        "fundings": pd.DataFrame(map(asdict, world.fundings)),
        "labels_agents": agents[["agent_id", "operator", "quality", "is_sybil"]],
        "labels_wallets": wallets,
    }


def naive_report(world: World) -> pd.DataFrame:
    """Plain mean rating per agent vs. true quality: what an aggregator without sybil defence would publish."""
    fb = pd.DataFrame(map(asdict, world.feedback))
    naive = fb.groupby("agent_id").value.mean()
    rows = []
    for aid, q in world.truth.agent_quality.items():
        rows.append({
            "agent_id": aid,
            "is_sybil": world.truth.is_sybil_agent(aid),
            "true": q * 100,
            "naive": naive.get(aid, np.nan),
            "n_ratings": int((fb.agent_id == aid).sum()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=Path)
    args = p.parse_args()

    world = simulate(SimConfig(seed=args.seed))
    frames = to_frames(world)
    print(
        f"{len(world.registrations)} agents, {len(world.feedback)} feedback, {len(world.fundings)} fundings, "
        f"{len(world.truth.sybil_operators)} sybil operators"
    )
    rep = naive_report(world)
    print("\nNaive mean rating vs true quality (0-100):")
    print(rep.groupby("is_sybil")[["true", "naive", "n_ratings"]].mean().round(1).to_string())

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, df in frames.items():
            df.to_csv(args.out / f"{name}.csv", index=False)
        print(f"\nwrote {', '.join(frames)} to {args.out}")


if __name__ == "__main__":
    main()
