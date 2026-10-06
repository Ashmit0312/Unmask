"""Export the evidence behind each score as JSON for the web app (web/public/data/<net>.json).

Live values (score, confidence, isTrusted, ERC-8004 summary) are read onchain by the app; this file adds what only
the pipeline knows: independent votes, self-dealing and ring shares, the ring each agent's ratings came from, and
the network summary.

    python -m trustscore.export_web --net monad-testnet
"""

import argparse
import json

import pandas as pd

from . import model
from .config import REPO_ROOT
from .realdata import collect

OUT = REPO_ROOT / "web" / "public" / "data"


def export(net_name: str) -> dict:
    inp = collect(net_name, quiet=True)
    res = model.score(inp.registrations, inp.feedback, inp.fundings, model.ModelConfig(), known_hubs=inp.known_hubs)
    fb = inp.feedback[~inp.feedback.revoked.astype(bool)]
    w = res.wallets.set_index("wallet")
    fb = fb.assign(cluster=fb.client.map(w.cluster), passkey=fb.tag2.eq("passkey"))
    owner = inp.registrations.drop_duplicates("agent_id").set_index("agent_id").owner
    a = res.agents.set_index("agent_id")
    a = a[a.n_ratings > 0]

    agents = {}
    for aid, r in a.iterrows():
        rows = fb[fb.agent_id == aid]
        by_cluster = rows.groupby("cluster").agg(ratings=("value", "size"), wallets=("client", "nunique"),
                                                 mean=("value", "mean")).sort_values("ratings", ascending=False)
        owner_cluster = w.cluster.get(owner.get(aid))
        top = []
        for c, g in by_cluster.head(5).iterrows():
            top.append({
                "wallets": int(g.wallets), "ratings": int(g.ratings), "mean": round(float(g["mean"]), 1),
                "suspicion": round(float(w.suspicion[w.cluster == c].max()), 2),
                "owner": bool(c == owner_cluster),
            })
        agents[str(aid)] = {
            "owner": owner.get(aid),
            "ratings": int(r.n_ratings), "raters": int(rows.client.nunique()),
            "independent": round(float(r.effective_votes), 2),
            "naive": round(float(rows.value.mean()), 1),
            "score": round(float(r.score), 1), "confidence": round(float(r.confidence), 3),
            "selfDealing": round(float(r.self_dealing_share), 3), "ringShare": round(float(r.ring_share), 3),
            "risk": round(float(r.risk), 3),
            "passkeyReviews": int(rows.passkey.sum()),
            "sources": top,
        }

    summary_path = REPO_ROOT / "data" / net_name / "summary.json"
    data = {
        "network": net_name,
        "head": int(inp.head),
        "summary": json.loads(summary_path.read_text()) if summary_path.exists() else {},
        "agents": agents,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{net_name}.json"
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {len(agents)} agents to {path} ({path.stat().st_size / 1024:.0f} KB)")
    return data


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--net", choices=["monad", "monad-testnet"], default="monad-testnet")
    args = p.parse_args()
    export(args.net)


if __name__ == "__main__":
    main()
