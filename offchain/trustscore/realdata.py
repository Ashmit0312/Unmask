"""Score the real ERC-8004 agents on Monad from full chain history and report what the model finds.

    python -m trustscore.realdata --net monad --nansen-budget 2000
    python -m trustscore.realdata --net monad-testnet

Stages 1, 2 and the transfer counts in 4 are cached as CSV under data/<net>/ (delete a file to redo it).
Stages 3 and 4's API answers live in data/cache/ instead, so those stages re-run for free and pick up where a
budget limit or an outage stopped them.
  1. events      HyperSync: every Registered / NewFeedback / FeedbackRevoked
  2. fundings    HyperSync: first value transfer into every rater and owner, 3 hops back
  3. gaps        wallets funded through contract calls: Nansen First Funder (mainnet) or BlockVision (testnet)
  4. hubs        funders shared by several of our wallets: lifetime transfer count (HyperSync), then Nansen labels
                 for the most shared ones (mainnet)
  5. scores      model v1 (+ v2 if a trained linker exists); scores, wallets and rings as CSV plus a printed report
"""

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from web3 import Web3

from . import model
from .config import MONAD_MAINNET, MONAD_TESTNET, REPO_ROOT
from .evaluate import GATE_CONFIDENCE, GATE_SCORE
from .schema import Funding

NETS = {"monad": MONAD_MAINNET, "monad-testnet": MONAD_TESTNET}
FEEDBACK_COLS = ["agent_id", "client", "index", "value", "tag1", "tag2", "block", "tx_hash", "revoked"]
FUNDING_COLS = ["wallet", "funder", "amount", "block", "tx_hash"]
HUB_TRANSFER_CAP = 200  # a funder that has sent this many value transfers is infrastructure, not an operator
SHARED_FUNDER_MIN = 3  # only funders behind at least this many of our wallets can merge anything worth checking


def normalize_values(fb: pd.DataFrame) -> pd.DataFrame:
    """Map every app's rating scale onto 0-100, per tag1 (ERC-8004 leaves the scale to the app).

    Tags with at least 90% of values in 0..100 (and reaching above 5) are percentages: in-range values stay,
    out-of-range ones are dropped as spam. Other tags are rescaled
    from their own 1st..99th percentile range. A tag where every rating is the same value carries no comparison;
    it is kept only if that value is already on the 0..100 scale. Returns rows with `value` normalised and the
    original kept in `value_raw`; dropped rows are left out.
    """
    out = []
    for _, g in fb.assign(tag1=fb.tag1.fillna("")).groupby("tag1"):
        v = g.value.astype(float)
        in_range = v.between(0, 100)
        if in_range.mean() >= 0.9 and v[in_range].max() > 5:
            # A percentage tag with a few out-of-range values (spam such as the int128 maximum): drop those.
            g, v = g[in_range], v[in_range]
            out.append(g.assign(value_raw=v, value=v))
            continue
        lo, hi = v.quantile(0.01), v.quantile(0.99)
        if hi > lo:
            norm = ((v - lo) / (hi - lo)).clip(0, 1) * 100
        elif 0 <= lo <= 100:
            norm = v
        else:
            continue
        out.append(g.assign(value_raw=v, value=norm))
    return pd.concat(out, ignore_index=True) if out else fb.assign(value_raw=fb.value)


REG_COLS = ["agent_id", "owner", "agent_uri", "block", "tx_hash"]


@dataclass
class Inputs:
    registrations: pd.DataFrame
    feedback: pd.DataFrame  # normalised to 0-100 per tag
    fundings: pd.DataFrame
    known_hubs: frozenset[str]
    head: int


def _read(path: Path, cols: list[str]) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame(columns=cols)


def update_events(src, out: Path, refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Cached registrations and feedback, topped up with blocks after the last cached one when refresh is set."""
    regs_p, fb_p, head_p = out / "registrations.csv", out / "feedback.csv", out / "head.json"
    regs, fb = _read(regs_p, REG_COLS), _read(fb_p, FEEDBACK_COLS)
    head = json.loads(head_p.read_text())["head"] if head_p.exists() else -1
    if head < 0 and (len(regs) or len(fb)):  # caches written before head tracking: resume after their last event
        head = int(max(regs.block.max() if len(regs) else -1, fb.block.max() if len(fb) else -1))
    if refresh or head < 0:
        new_head = src.height() - 1
        if new_head > head:
            start = head + 1
            revoked = src.revocations(start, new_head)
            new_regs = pd.DataFrame(map(asdict, src.registrations(start, new_head)), columns=REG_COLS)
            new_fb = pd.DataFrame(map(asdict, src.feedback(start, new_head, revoked)), columns=FEEDBACK_COLS)
            regs = pd.concat([regs, new_regs], ignore_index=True)
            fb = pd.concat([fb, new_fb], ignore_index=True)
            if revoked and len(fb):  # a revocation can land long after the rating it cancels
                keys = list(zip(fb.agent_id, fb.client, fb["index"]))
                fb["revoked"] = fb.revoked.astype(bool) | pd.Series([k in revoked for k in keys], index=fb.index)
            regs.to_csv(regs_p, index=False)
            fb.to_csv(fb_p, index=False)
            head = new_head
            head_p.write_text(json.dumps({"head": head}))
    return regs, fb, head


def update_fundings(src, out: Path, wallets: set[str]) -> pd.DataFrame:
    """First fundings via HyperSync, queried once per wallet (unresolved wallets are remembered too)."""
    fund_p, checked_p = out / "fundings_hypersync.csv", out / "fundings_checked.csv"
    fund = _read(fund_p, FUNDING_COLS)
    checked = set(_read(checked_p, ["wallet"]).wallet.str.lower())
    if fund_p.exists() and not checked_p.exists():  # caches written before tracking: every wallet was queried
        checked = {w.lower() for w in wallets}
    todo = {w for w in wallets if w.lower() not in checked}
    if todo:
        new = pd.DataFrame(map(asdict, src.first_fundings(todo)), columns=FUNDING_COLS)
        fund = pd.concat([fund, new], ignore_index=True).drop_duplicates("wallet", keep="first")
        fund.to_csv(fund_p, index=False)
        pd.DataFrame({"wallet": sorted(checked | {w.lower() for w in todo})}).to_csv(checked_p, index=False)
    return fund


def collect(net_name: str, nansen_budget: int = 0, labels_top: int = 0, out: Path | None = None,
            refresh: bool = False, quiet: bool = False) -> Inputs:
    from .hypersync_source import HyperSyncSource

    log = (lambda *a: None) if quiet else print
    net = NETS[net_name]
    out = out or REPO_ROOT / "data" / net_name
    src = HyperSyncSource(net)
    out.mkdir(parents=True, exist_ok=True)

    # 1. events
    regs, fb, head = update_events(src, out, refresh)
    if (passkey := os.environ.get("PASSKEY_REVIEWS_ADDRESS")) and net.chain_id == 10143:
        pk = pd.DataFrame(map(asdict, src.passkey_reviews(passkey)), columns=FEEDBACK_COLS)
        log(f"    + {len(pk)} passkey reviews from {pk.client.nunique()} passkeys")
        fb = pd.concat([fb, pk], ignore_index=True)
    fb = fb.astype({"revoked": bool, "agent_id": int, "value": float, "block": int})
    regs = regs.astype({"agent_id": int, "block": int})
    raters = set(fb.client)
    wallets = raters | set(regs.owner)
    log(f"[1] {len(regs)} agents, {len(fb)} ratings ({int(fb.revoked.sum())} revoked) from {len(raters)} raters, "
        f"to block {head}")

    # 2. fundings
    fund = update_fundings(src, out, wallets)
    resolved = set(fund.wallet.str.lower())
    missing = sorted(w for w in raters if w.lower() not in resolved)
    log(f"[2] {len(fund)} funding records; {len(raters) - len(missing)}/{len(raters)} raters have a direct funder")

    # 3. gaps
    def fill_gaps() -> pd.DataFrame:
        rows = []
        if net.chain_id == 143:
            from .enrich import BudgetExceeded, Nansen

            nansen = Nansen(budget_credits=nansen_budget)
            w3 = Web3(Web3.HTTPProvider(net.rpc_url))
            try:
                for w in missing:
                    ff = nansen.first_funder(w)
                    if not ff or not str(ff.get("first_funder_address", "")).startswith("0x"):
                        continue
                    block = -1
                    if ff.get("chain") == "monad":
                        try:
                            block = w3.eth.get_transaction(ff["transaction_hash"]).blockNumber
                        except Exception:
                            pass
                    rows.append(Funding(Web3.to_checksum_address(w), Web3.to_checksum_address(ff["first_funder_address"]),
                                        0.0, block, ff.get("transaction_hash", "")))
            except BudgetExceeded as e:
                log(f"    {e}; {len(rows)} gaps filled")
            finally:
                nansen.save()
            log(f"    Nansen credits spent this run: {nansen.spent}")
        else:
            from .enrich import BlockVision

            bv = BlockVision()
            try:
                for w in missing:
                    if bv.disabled:
                        break
                    if (f := bv.first_internal_funding(w)) is not None:
                        rows.append(f)
            finally:
                bv.save()
            if bv.disabled:
                log(f"    BlockVision unavailable: {bv.disabled}")
            elif bv.failures:
                log(f"    BlockVision did not answer for {bv.failures} wallets; rerun to retry them")
        return pd.DataFrame(map(asdict, rows))

    gaps = fill_gaps()
    gaps = gaps if len(gaps) else pd.DataFrame(columns=FUNDING_COLS)
    gaps.to_csv(out / "fundings_gaps.csv", index=False)
    fund_all = pd.concat([fund, gaps], ignore_index=True).drop_duplicates("wallet", keep="first")
    log(f"[3] filled {len(gaps)} of {len(missing)} gaps")

    # 4. hubs
    def find_hubs() -> pd.DataFrame:
        shared = fund_all.funder.value_counts()
        shared = shared[shared >= SHARED_FUNDER_MIN]
        counts_path = out / "funders_sent.csv"
        counts = pd.read_csv(counts_path) if counts_path.exists() else pd.DataFrame(columns=["funder", "sent"])
        todo = [f for f in shared.index if f not in set(counts.funder)]
        if todo:
            sent = src.value_transfers_sent(todo, cap=HUB_TRANSFER_CAP)
            new = pd.DataFrame({"funder": todo, "sent": [sent.get(f.lower(), 0) for f in todo]})
            counts = pd.concat([counts, new], ignore_index=True)
            counts.to_csv(counts_path, index=False)
        df = pd.DataFrame({"funder": shared.index, "our_wallets": shared.values})
        df["transfers_sent"] = df.funder.map(dict(zip(counts.funder, counts.sent))).fillna(0).astype(int)
        df["labels"] = ""
        df["hub_by_volume"] = df.transfers_sent >= HUB_TRANSFER_CAP
        df["hub_by_label"] = False
        if net.chain_id == 143 and labels_top > 0:
            from .enrich import BudgetExceeded, Nansen, is_hub_label

            nansen = Nansen(budget_credits=nansen_budget)
            try:
                for i in df.sort_values("our_wallets", ascending=False).head(labels_top).index:
                    labels = nansen.labels(df.at[i, "funder"])
                    df.at[i, "labels"] = "; ".join(l.get("label", "") for l in labels)
                    df.at[i, "hub_by_label"] = is_hub_label(labels)
            except BudgetExceeded as e:
                log(f"    {e}")
            finally:
                nansen.save()
        return df

    hubs = find_hubs()
    hubs.to_csv(out / "funders.csv", index=False)
    known_hubs = frozenset(hubs.funder[hubs.hub_by_volume | hubs.hub_by_label]) if len(hubs) else frozenset()
    log(f"[4] {len(hubs)} shared funders checked; {len(known_hubs)} confirmed as hubs")

    fbn = normalize_values(fb) if len(fb) else fb
    log(f"[5] {len(fbn)} of {len(fb)} ratings kept after per-tag normalisation to 0-100")
    return Inputs(regs, fbn, fund_all, known_hubs, head)


def run(net_name: str, nansen_budget: int, labels_top: int, out: Path, refresh: bool = False) -> dict:
    inp = collect(net_name, nansen_budget, labels_top, out, refresh)
    res = model.score(inp.registrations, inp.feedback, inp.fundings, model.ModelConfig(), known_hubs=inp.known_hubs)
    return report(inp.registrations, inp.feedback, res, out, net_name)


def report(regs: pd.DataFrame, fb: pd.DataFrame, res: model.ScoreResult, out: Path, net_name: str) -> dict:
    live = fb[~fb.revoked]
    n = live.groupby("agent_id").size()
    naive = live.groupby("agent_id").value.mean()
    a = res.agents.set_index("agent_id")
    a["naive"] = naive
    a["naive_trusted"] = (a.naive >= GATE_SCORE) & ((n / (n + 5)).reindex(a.index).fillna(0) >= GATE_CONFIDENCE)
    a["trusted"] = (a.score >= GATE_SCORE) & (a.confidence >= GATE_CONFIDENCE)
    a.reset_index().to_csv(out / "scores.csv", index=False)

    w = res.wallets
    w.to_csv(out / "wallets.csv", index=False)
    rated = live.assign(cluster=live.client.map(dict(zip(w.wallet, w.cluster))))
    rings = (
        w[(w.cluster_raters >= 2) & (w.suspicion >= 0.5)]
        .groupby("cluster")
        .agg(raters=("cluster_raters", "first"), suspicion=("suspicion", "first"), wallets=("wallet", "size"))
        .join(rated.groupby("cluster").agg(ratings=("value", "size"), agents=("agent_id", "nunique"),
                                           mean_rating=("value", "mean")))
        .sort_values("ratings", ascending=False)
    )
    rings.to_csv(out / "rings.csv")

    rated_agents = a[a.n_ratings > 0]
    summary = {
        "network": net_name,
        "agents": int(len(regs)),
        "rated_agents": int(len(rated_agents)),
        "ratings": int(len(live)),
        "raters": int(live.client.nunique()),
        "rings": int(len(rings)),
        "ring_wallets": int(rings.raters.sum()) if len(rings) else 0,
        "ratings_from_rings": int(rings.ratings.sum()) if len(rings) else 0,
        "agents_with_ring_risk>=0.5": int((rated_agents.risk >= 0.5).sum()),
        "trusted_naive": int(rated_agents.naive_trusted.sum()),
        "trusted_model": int(rated_agents.trusted.sum()),
        "lost_trust": int((rated_agents.naive_trusted & ~rated_agents.trusted).sum()),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if len(rings):
        print("\nLargest suspected rings:\n" + rings.head(10).round(2).to_string())
    moved = rated_agents.assign(drop=rated_agents.naive - rated_agents.score).sort_values("drop", ascending=False)
    print("\nAgents whose naive average overstates them most:\n" + moved.head(10)[
        ["n_ratings", "n_votes", "naive", "score", "confidence", "risk"]].round(2).to_string())
    return summary


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--net", choices=list(NETS), default="monad")
    p.add_argument("--nansen-budget", type=int, default=2_000, help="max Nansen credits this run (cached calls are free)")
    p.add_argument("--labels-top", type=int, default=10, help="Nansen-label the N most shared funders (100 credits each)")
    p.add_argument("--refresh", action="store_true", help="fetch blocks newer than the cached ones")
    args = p.parse_args()
    run(args.net, args.nansen_budget, args.labels_top, REPO_ROOT / "data" / args.net, args.refresh)


if __name__ == "__main__":
    main()
