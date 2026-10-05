"""Fill what HyperSync cannot see, and confirm which funders are exchanges.

- Nansen (Monad mainnet): First Funder (1 credit) for wallets funded through contract calls; Labels (100 credits)
  only for the few funders shared by many of our wallets, to confirm exchanges.
- BlockVision (the network its API key is bound to): internal transactions, the first value transfer that reached
  a wallet through a contract call (faucets, batch senders).

Every response is cached under data/cache/, so re-runs never pay twice; Nansen spend is capped per run.
"""

import json
import os
import time
from pathlib import Path

import requests

from .config import REPO_ROOT
from .schema import Funding

CACHE_DIR = REPO_ROOT / "data" / "cache"
NANSEN_URL = "https://api.nansen.ai/api/v1/profiler/address"
BLOCKVISION_URL = "https://api.blockvision.org/v2/monad/account/internal/transactions"

# Nansen label categories / words that mark a wallet as exchange-like infrastructure rather than an identity.
HUB_LABEL_WORDS = ("exchange", "cex", "hot wallet", "deposit", "withdrawal", "bridge", "faucet", "binance", "coinbase",
                   "okx", "bybit", "kraken", "kucoin", "gate", "bitget", "mexc", "htx", "upbit")


class BudgetExceeded(RuntimeError):
    pass


def _request(method: str, url: str, tries: int = 5, **kw) -> requests.Response | None:
    """Retry rate limits, server errors and timeouts with backoff; None if the service never answered."""
    for attempt in range(tries):
        try:
            r = requests.request(method, url, timeout=60, **kw)
        except (requests.Timeout, requests.ConnectionError):
            r = None
        if r is not None and r.status_code < 500 and r.status_code != 429:
            r.raise_for_status()
            return r
        time.sleep(min(2 ** attempt, 20))
    return None


class _Cache:
    def __init__(self, name: str):
        self.path = CACHE_DIR / f"{name}.json"
        self.data: dict = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))


class Nansen:
    def __init__(self, api_key: str | None = None, budget_credits: int = 2_000):
        self.key = api_key or os.environ.get("NANSEN_API_KEY")
        if not self.key:
            raise RuntimeError("NANSEN_API_KEY is not set")
        self.cache = _Cache("nansen")
        self.budget, self.spent = budget_credits, 0
        self.remaining: int | None = None  # account balance, from the x-nansen-credits-remaining header

    def _post(self, endpoint: str, body: dict, cost: int):
        key = f"{endpoint}:{json.dumps(body, sort_keys=True)}"
        if (hit := self.cache.get(key)) is not None:
            return hit
        if self.spent + cost > self.budget:
            raise BudgetExceeded(f"Nansen budget of {self.budget} credits reached")
        if self.remaining is not None and self.remaining < cost:
            raise BudgetExceeded(f"Nansen account has {self.remaining} credits left, this call costs {cost}")
        r = _request("POST", f"{NANSEN_URL}/{endpoint}", json=body,
                     headers={"apikey": self.key, "Content-Type": "application/json"})
        if r is None:
            return []  # not cached: a later run retries
        self.spent += cost
        if "x-nansen-credits-remaining" in r.headers:
            self.remaining = int(r.headers["x-nansen-credits-remaining"])
        data = r.json().get("data", [])
        self.cache.put(key, data)
        return data

    def first_funder(self, wallet: str) -> dict | None:
        """{first_funder_address, chain, transaction_hash, block_timestamp, first_funder_name?} across all chains."""
        rows = self._post("first-funder", {"address": wallet.lower(), "chain": "all"}, cost=1)
        return rows[0] if rows else None

    def labels(self, address: str, chain: str = "monad") -> list[dict]:
        return self._post("labels", {"address": address.lower(), "chain": chain,
                                     "pagination": {"page": 1, "per_page": 100}}, cost=100)

    def save(self) -> None:
        self.cache.save()


class BlockVision:
    def __init__(self, api_key: str | None = None):
        self.key = api_key or os.environ.get("BLOCKVISION_API_KEY")
        if not self.key:
            raise RuntimeError("BLOCKVISION_API_KEY is not set")
        self.cache = _Cache("blockvision")
        self.failures = 0
        self.disabled: str | None = None  # set when the key has no access (e.g. trial used up)

    def first_internal_funding(self, wallet: str) -> Funding | None:
        key = wallet.lower()
        hit = self.cache.get(key)
        if hit is None:
            try:
                r = _request("GET", BLOCKVISION_URL, headers={"x-api-key": self.key}, params={
                    "address": wallet, "filter": "to", "limit": 50, "ascendingOrder": "true"})
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    self.disabled = e.response.json().get("message", str(e))
                    return None
                raise
            if r is None:
                self.failures += 1
                return None  # not cached: a later run retries
            rows = (r.json().get("result") or {}).get("data") or []
            first = next((t for t in rows if int(t.get("value") or 0) > 0), None)
            hit = first or {}
            self.cache.put(key, hit)
        if not hit:
            return None
        return Funding(hit["to"], hit["from"], int(hit["value"]) / 1e18, int(hit["blockNumber"]), hit["hash"])

    def save(self) -> None:
        self.cache.save()


def is_hub_label(labels: list[dict]) -> bool:
    text = " ".join(f"{l.get('label', '')} {l.get('category') or ''} {' '.join(l.get('kind') or [])}"
                    for l in labels).lower()
    return any(w in text for w in HUB_LABEL_WORDS)


def cached_paths() -> list[Path]:
    return sorted(CACHE_DIR.glob("*.json"))
