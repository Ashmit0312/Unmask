"""Score every method on simulated worlds of increasing difficulty and compare against ground truth.

    python -m trustscore.evaluate --seeds 5            # v2 uses the saved linker (trained on SCENARIOS)
    python -m trustscore.evaluate --seeds 5 --loso     # v2 retrained without the scenario it is scored on
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

# Never used for training: attacks whose shape the learned linker has not seen.
HELD_OUT: dict[str, SimConfig] = {
    # Puppets spread their ratings over most of a day instead of a burst.
    "held-out: slow drip": SimConfig(p_hub_funding=1.0, burst_blocks=150_000),
    # Puppets give believable 70-100 ratings and rate honest agents honestly half the time.
    "held-out: mimic": SimConfig(p_hub_funding=1.0, puppet_rating=(70, 100), p_camouflage=0.5),
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
    return frame_metrics(df)


def frame_metrics(df: pd.DataFrame) -> dict[str, float]:
    """Metrics from one row per agent with columns true (0-100), sybil, score (0-100), confidence (0-1)."""
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


METHODS = ["naive mean", "bayesian mean", "iterative filtering", "model v1 (rules)", "model v2 (learned)"]


def evaluate_world(world: World, mcfg: model.ModelConfig, linker=None) -> list[dict]:
    regs, fb, fund = frames(world)
    # Baselines have no notion of independence, so their confidence grows with the raw rating count.
    n = fb.groupby("agent_id").size()
    raw_conf = n / (n + mcfg.confidence_k)
    results = {"model v1 (rules)": model.score(regs, fb, fund, mcfg)}
    if linker is not None:
        results["model v2 (learned)"] = model.score(regs, fb, fund, mcfg, linker)
    methods = {
        "naive mean": (baselines.naive_mean(fb), raw_conf),
        "bayesian mean": (baselines.bayesian_mean(fb), raw_conf),
        "iterative filtering": (baselines.iterative_filtering(fb), raw_conf),
    } | {m: (r.agents.set_index("agent_id").score, r.agents.set_index("agent_id").confidence)
         for m, r in results.items()}
    rows = []
    for method, (s, c) in methods.items():
        row = {"method": method, **score_metrics(s, c, world)}
        if method in results:
            row |= detection_metrics(results[method], world)
        rows.append(row)
    return rows


def evaluate(
    n_seeds: int = 5, mcfg: model.ModelConfig = model.ModelConfig(), linker=None, loso: bool = False
) -> pd.DataFrame:
    """Every method on SCENARIOS and HELD_OUT. With loso, v2 on each training scenario uses a linker
    retrained without that scenario; held-out scenarios always use `linker`."""
    from . import linker as linker_mod

    rows = []
    for name, cfg in (SCENARIOS | HELD_OUT).items():
        lk = linker
        if loso and name in SCENARIOS:
            lk = linker_mod.train({k: v for k, v in SCENARIOS.items() if k != name})
        for seed in range(n_seeds):
            for row in evaluate_world(simulate(replace(cfg, seed=seed)), mcfg, lk):
                rows.append({"scenario": name, "seed": seed, **row})
    return pd.DataFrame(rows)


def summary(results: pd.DataFrame) -> pd.DataFrame:
    order = {m: i for i, m in enumerate(METHODS)}
    scen = {k: n for n, k in enumerate(SCENARIOS | HELD_OUT)}
    s = results.drop(columns="seed").groupby(["scenario", "method"], sort=False).mean()
    return s.sort_index(level="method", key=lambda i: i.map(order), sort_remaining=False).sort_index(
        level="scenario", key=lambda i: i.map(scen), sort_remaining=False
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--loso", action="store_true", help="leave-one-scenario-out training for v2")
    p.add_argument("--no-v2", action="store_true")
    args = p.parse_args()
    from . import linker as linker_mod

    lk = None if args.no_v2 else linker_mod.load()
    pd.set_option("display.width", 220)
    print(summary(evaluate(args.seeds, linker=lk, loso=args.loso)).round(3).to_string(na_rep="-"))


if __name__ == "__main__":
    main()
