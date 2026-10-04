"""Reference aggregators with no sybil defence. All return a score per agent on the 0-100 rating scale."""

import numpy as np
import pandas as pd


def naive_mean(fb: pd.DataFrame) -> pd.Series:
    return fb.groupby("agent_id").value.mean()


def bayesian_mean(fb: pd.DataFrame, prior_strength: float = 3.0) -> pd.Series:
    """Mean shrunk toward the global mean by `prior_strength` pseudo-ratings, so thinly rated agents stay central."""
    g = fb.groupby("agent_id").value
    return (g.sum() + prior_strength * fb.value.mean()) / (g.count() + prior_strength)


def iterative_filtering(fb: pd.DataFrame, iters: int = 100, eps: float = 1.0, tol: float = 1e-6) -> pd.Series:
    """Rater-credibility reputation (Laureti et al. 2006): weight each rater by 1 / (their mean squared
    deviation from the current consensus), recompute consensus, repeat to convergence."""
    a_codes, agents = pd.factorize(fb.agent_id)
    c_codes, _ = pd.factorize(fb.client)
    r = fb.value.to_numpy(float)
    n_c = c_codes.max() + 1
    rated = np.bincount(c_codes, minlength=n_c)

    w = np.ones(n_c)
    q = np.zeros(len(agents))
    for _ in range(iters):
        wr = w[c_codes]
        q_new = np.bincount(a_codes, wr * r) / np.bincount(a_codes, wr)
        dev = np.bincount(c_codes, (r - q_new[a_codes]) ** 2, minlength=n_c) / rated
        w = 1.0 / (dev + eps)
        if np.max(np.abs(q_new - q)) < tol:
            q = q_new
            break
        q = q_new
    return pd.Series(q, index=agents, name="value")
