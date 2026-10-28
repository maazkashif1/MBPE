"""Trade simulator, blackout and metrics (protocol in SCOPE.md).

Simulator: walks decision times in order. At most one open position across both sides; while a trade is
open, new signals are ignored; if long and short both signal in the same hour, neither is taken. A trade
opened at decision time t is open until its label's exit time; a later signal at t' is taken only if
t' >= that exit time. Outcomes come from the labels (net R per cost scenario).
"""

import numpy as np
import polars as pl

DAY = 86_400_000


def blackout(
    decision: np.ndarray, events: np.ndarray, before_min: int, horizon_min: int, latency_min: int
) -> np.ndarray:
    """True where an entry at decision + latency is blocked (a release in [entry - before, entry + horizon]).

    Uses only the scheduled horizon, not the trade's actual exit (unknown at entry).
    """
    entry = decision + latency_min * 60_000
    lo = np.searchsorted(events, entry - before_min * 60_000, "left")
    hi = np.searchsorted(events, entry + horizon_min * 60_000, "right")
    return hi > lo


def simulate(
    decision: np.ndarray,
    sig_long: np.ndarray,
    sig_short: np.ndarray,
    exit_long: np.ndarray,
    exit_short: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Indices (into the hourly arrays) and sides (+1 long / -1 short) of the trades taken, in time order.

    Arrays are aligned per decision time and sorted by it. Signals must already exclude blocked hours.
    """
    one = sig_long ^ sig_short  # both signalling -> neither
    idx = np.flatnonzero(one)
    t, side = decision[idx], np.where(sig_long[idx], 1, -1)
    ex = np.where(side == 1, exit_long[idx], exit_short[idx])
    taken, k = [], 0
    while k < len(idx):
        taken.append(k)
        k = int(np.searchsorted(t, ex[k], "left"))  # next signal at or after this trade's exit
        if k <= taken[-1]:  # defensive: an exit never precedes its own decision
            k = taken[-1] + 1
    taken = np.asarray(taken, dtype=np.int64)
    return idx[taken] if len(taken) else np.zeros(0, np.int64), side[taken] if len(taken) else np.zeros(
        0, np.int64
    )


def block_bootstrap_ci(x: np.ndarray, block: int, n: int, seed: int = 0) -> tuple[float, float]:
    """95% CI of the mean via a moving-block bootstrap over the trade sequence."""
    if len(x) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    b = min(block, len(x))
    k = int(np.ceil(len(x) / b))
    starts = rng.integers(0, len(x) - b + 1, size=(n, k))
    means = x[(starts[:, :, None] + np.arange(b)).reshape(n, -1)[:, : len(x)]].mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def metrics(
    trades: pl.DataFrame,
    period_start: int,
    period_end: int,
    risk: float,
    block: int,
    samples: int,
    ci: bool = True,
) -> dict:
    """trades: exit_time (epoch ms), net_R, bps_per_R. Period [start, end) in epoch ms."""
    n = trades.height
    years = (period_end - period_start) / (365.25 * DAY)
    if n == 0:
        return {
            "trades": 0,
            "mean_R": np.nan,
            "bps_per_trade": np.nan,
            "return_per_year": 0.0,
            "hit_rate": np.nan,
            "profit_factor": np.nan,
            "sharpe": np.nan,
            "max_drawdown": 0.0,
            "ci_lo": np.nan,
            "ci_hi": np.nan,
        }
    r = trades["net_R"].to_numpy()
    days = np.arange(period_start // DAY, (period_end - 1) // DAY + 1)
    daily = np.zeros(len(days))
    np.add.at(daily, np.clip(trades["exit_time"].to_numpy() // DAY - days[0], 0, len(days) - 1), risk * r)
    equity = np.cumprod(1 + risk * r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    lo, hi = block_bootstrap_ci(r, block, samples) if ci else (np.nan, np.nan)
    return {
        "trades": n,
        "mean_R": float(r.mean()),
        "bps_per_trade": float((r * trades["bps_per_R"].to_numpy()).mean()),
        "return_per_year": float(risk * r.sum() / years),
        "hit_rate": float((r > 0).mean()),
        "profit_factor": float(gains / losses) if losses > 0 else np.inf,
        "sharpe": float(daily.mean() / daily.std(ddof=1) * np.sqrt(365.25))
        if daily.std(ddof=1) > 0
        else np.nan,
        "max_drawdown": float((1 - equity / peak).max()),
        "ci_lo": lo,
        "ci_hi": hi,
    }
