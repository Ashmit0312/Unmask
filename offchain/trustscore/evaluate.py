"""Score every method on simulated worlds of increasing difficulty and compare against ground truth.

    python -m trustscore.evaluate --seeds 5
"""

import argparse
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from . import baselines, model
from .simulate import SimConfig, World, simulate

SCENARIOS: dict[str, SimConfig] = {
    "direct funding": SimConfig(),
    "3-hop funding": SimConfig(funding_hops=3),
    "exchange-funded": SimConfig(p_hub_funding=1.0),
    "camouflaged": SimConfig(p_hub_funding=1.0, p_camouflage=0.9, p_badmouth=0.5),
    "many small rings": SimConfig(p_hub_funding=1.0, n_sybil_operators=8, sybil_puppets=(3, 5)),
}


def frames(world: World) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (
        pd.DataFrame(map(asdict, world.registrations)),
        pd.DataFrame(map(asdict, world.feedback)),
        pd.DataFrame(map(asdict, world.fundings)),
    )


GATE_SCORE, GATE_CONFIDENCE = 60.0, 0.6  # the isTrusted() thresholds a consuming contract might use


def score_metrics(scores: pd.Series, confidence: pd.Series, world: World) -> dict[str, float]:
    t = world.truth
    df = pd.DataFrame({"agent_id": list(t.agent_quality)})
    df["true"] = df.agent_id.map(t.agent_quality) * 100
    df["sybil"] = df.agent_id.map(t.is_sybil_agent)
    df["score"] = df.agent_id.map(scores).fillna(scores.mean())
    df["confidence"] = df.agent_id.map(confidence).fillna(0.0)
    err = (df.score - df.true).abs()
    trusted = (df.score >= GATE_SCORE) & (df.confidence >= GATE_CONFIDENCE)
    good = ~df.sybil & (df.true >= GATE_SCORE)
    top10 = df.nlargest(10, "score")
    return {
        "mae_sybil": err[df.sybil].mean(),
        "mae_honest": err[~df.sybil].mean(),
        "spearman": df.score.corr(df.true, method="spearman"),
        "sybils_top10": int(top10.sybil.sum()),
        "sybils_trusted": trusted[df.sybil].mean(),
        "good_trusted": trusted[good].mean(),
    }


def detection_metrics(res: model.ScoreResult, world: World) -> dict[str, float]:
    t = world.truth
    a = res.agents.assign(sybil=lambda d: d.agent_id.map(t.is_sybil_agent))
    raters = {f.client for f in world.feedback}
    w = res.wallets[res.wallets.wallet.isin(raters)].assign(sybil=lambda d: d.wallet.map(t.is_sybil_wallet))
    honest = w[~w.sybil]
    return {
        "agent_auroc": roc_auc_score(a.sybil, a.risk),
        "wallet_auroc": roc_auc_score(w.sybil, w.suspicion),
        "honest_merged": float((honest.cluster_raters > 1).mean()),
    }


def evaluate(n_seeds: int = 5, mcfg: model.ModelConfig = model.ModelConfig()) -> pd.DataFrame:
    rows = []
    for name, cfg in SCENARIOS.items():
        for seed in range(n_seeds):
            world = simulate(replace(cfg, seed=seed))
            regs, fb, fund = frames(world)
            res = model.score(regs, fb, fund, mcfg)
            # Baselines have no notion of independence, so their confidence grows with the raw rating count.
            n = fb.groupby("agent_id").size()
            raw_conf = n / (n + mcfg.confidence_k)
            a = res.agents.set_index("agent_id")
            methods = {
                "naive mean": (baselines.naive_mean(fb), raw_conf),
                "bayesian mean": (baselines.bayesian_mean(fb), raw_conf),
                "iterative filtering": (baselines.iterative_filtering(fb), raw_conf),
                "model": (a.score, a.confidence),
            }
            for method, (s, c) in methods.items():
                row = {"scenario": name, "seed": seed, "method": method, **score_metrics(s, c, world)}
                if method == "model":
                    row |= detection_metrics(res, world)
                rows.append(row)
    return pd.DataFrame(rows)


def summary(results: pd.DataFrame) -> pd.DataFrame:
    order = {m: i for i, m in enumerate(["naive mean", "bayesian mean", "iterative filtering", "model"])}
    s = results.drop(columns="seed").groupby(["scenario", "method"], sort=False).mean()
    return s.sort_index(level="method", key=lambda i: i.map(order), sort_remaining=False).sort_index(
        level="scenario", key=lambda i: i.map({k: n for n, k in enumerate(SCENARIOS)}), sort_remaining=False
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=5)
    args = p.parse_args()
    pd.set_option("display.width", 200)
    print(summary(evaluate(args.seeds)).round(3).to_string(na_rep="-"))


if __name__ == "__main__":
    main()
