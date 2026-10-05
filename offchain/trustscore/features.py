"""Pair features for learned entity resolution: is one operator behind both of these rating wallets?

Candidate pairs are wallets that rated at least one common agent (blocking on the agent), which is the only case
where a hidden shared operator changes a score. Features avoid simulator artefacts that real chains would not
reproduce (funding amounts, exact rating ranges) and lean on timing, agreement, overlap and funding structure.
"""

import numpy as np
import pandas as pd

WINDOWS = (2_000, 10_000, 50_000)  # blocks: co-ratings this close together

FEATURES = [
    "n_common", "jaccard", "min_agents", "max_agents",
    *(f"corate_{w}" for w in WINDOWS), *(f"corate_{w}_share" for w in WINDOWS),
    "log_min_dt", "log_median_dt", "mean_dv", "max_dv", "both_high", "both_low",
    "same_funder", "same_hub", "log_fund_gap", "log_delay_min", "log_delay_max", "log_delay_gap",
    "log_first_gap", "extreme_min", "concentration_min",
]


def rater_profiles(fb: pd.DataFrame, fund: pd.DataFrame) -> pd.DataFrame:
    g = fb.groupby("client")
    prof = pd.DataFrame({
        "n_agents": g.agent_id.nunique(),
        "first_block": g.block.min(),
        "extreme": g.value.apply(lambda v: ((v >= 85) | (v <= 15)).mean()),
        # Share of a wallet's ratings that go to its single most-rated agent.
        "concentration": fb.groupby(["client", "agent_id"]).size().groupby(level="client").max() / g.size(),
    })
    first_fund = fund.sort_values("block").drop_duplicates("wallet").set_index("wallet")
    prof["funder"] = first_fund.funder.reindex(prof.index)
    # Cross-chain first funders carry block -1: the funder still counts, the timing does not.
    prof["funded_block"] = first_fund.block.where(first_fund.block >= 0).reindex(prof.index)
    prof["delay"] = prof.first_block - prof.funded_block
    return prof


def pair_features(fb: pd.DataFrame, fund: pd.DataFrame, hubs: set[str]) -> pd.DataFrame:
    """One row per candidate pair (a < b) with FEATURES; index (a, b)."""
    r = fb[["agent_id", "client", "block", "value"]]
    m = r.merge(r, on="agent_id", suffixes=("_a", "_b"))
    m = m[m.client_a < m.client_b]
    if m.empty:
        return pd.DataFrame(columns=FEATURES, index=pd.MultiIndex.from_tuples([], names=["a", "b"]))
    m = m.assign(
        dt=(m.block_a - m.block_b).abs(),
        dv=(m.value_a - m.value_b).abs(),
        both_high=(m.value_a >= 85) & (m.value_b >= 85),
        both_low=(m.value_a <= 15) & (m.value_b <= 15),
    )
    key = ["client_a", "client_b"]
    g = m.groupby(key)
    f = pd.DataFrame({
        "n_common": g.agent_id.nunique(),
        "log_min_dt": np.log1p(g.dt.min()),
        "log_median_dt": np.log1p(g.dt.median()),
        "mean_dv": g.dv.mean(),
        "max_dv": g.dv.max(),
        "both_high": g.both_high.mean(),
        "both_low": g.both_low.mean(),
    })
    for w in WINDOWS:
        f[f"corate_{w}"] = m[m.dt <= w].groupby(key).agent_id.nunique().reindex(f.index).fillna(0)
        f[f"corate_{w}_share"] = f[f"corate_{w}"] / f.n_common
    f.index.names = ["a", "b"]

    prof = rater_profiles(fb, fund)
    a = prof.reindex(f.index.get_level_values("a"))
    b = prof.reindex(f.index.get_level_values("b"))
    av = lambda col: a[col].to_numpy(dtype=float)  # noqa: E731
    bv = lambda col: b[col].to_numpy(dtype=float)  # noqa: E731

    f["min_agents"] = np.minimum(av("n_agents"), bv("n_agents"))
    f["max_agents"] = np.maximum(av("n_agents"), bv("n_agents"))
    f["jaccard"] = f.n_common / (av("n_agents") + bv("n_agents") - f.n_common)
    same = (a.funder.to_numpy() == b.funder.to_numpy()) & a.funder.notna().to_numpy()
    f["same_funder"] = same.astype(float)
    f["same_hub"] = (same & a.funder.isin(hubs).to_numpy()).astype(float)
    f["log_fund_gap"] = np.log1p(np.abs(av("funded_block") - bv("funded_block")))
    da, db = av("delay"), bv("delay")
    f["log_delay_min"] = np.log1p(np.clip(np.fmin(da, db), 0, None))
    f["log_delay_max"] = np.log1p(np.clip(np.fmax(da, db), 0, None))
    f["log_delay_gap"] = np.log1p(np.abs(da - db))
    f["log_first_gap"] = np.log1p(np.abs(av("first_block") - bv("first_block")))
    f["extreme_min"] = np.minimum(av("extreme"), bv("extreme"))
    f["concentration_min"] = np.minimum(av("concentration"), bv("concentration"))
    return f[FEATURES]
