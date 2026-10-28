"""Overfitting estimates for Phase 3 (no holdout): deflated Sharpe ratio and PBO via CSCV.

Usage: uv run python -m mbpe.validation.overfitting
Outputs: reports/phase3_step3/{daily_returns.csv, dsr.json, pbo.json}

Inputs: the test-fold trades of every evaluated model configuration (Phase 3 step 2 and 2b; baselines and the
shuffled-label control excluded), turned into daily returns at 1% risk per trade (trades counted on their
exit day; days without trades = 0), 2022-01-01 to the holdout start.

- DSR (Bailey & Lopez de Prado 2014): PSR of the best configuration's daily Sharpe against SR*, the expected
  maximum Sharpe of N unskilled trials. N = all tuning trials + the untuned evaluated configurations. The
  variance of Sharpe across trials is estimated from the evaluated configurations' test-fold Sharpes (the
  tuning trials only have validation scores).
- PBO (Bailey et al. 2014) via CSCV across all evaluated configurations, adapted from LLM-Auto-Trader (MIT).
"""

# cscv_pbo() below is adapted from LLM-Auto-Trader, src/analysis/cscv.py (commit dfa93ea),
# Copyright (c) 2026 Erfan Alimohammadi, MIT License (licenses/LLM-Auto-Trader-LICENSE.txt).
# Changes: degenerate inputs raise instead of returning 0.5; also returns the in-sample winner per split.

import json
import math
from itertools import combinations
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import norm

from mbpe.validation.walk_forward import load_eval_config, make_folds, to_ms

OUT = Path("reports/phase3_step3")
TRADES = (
    Path("data/features/phase3_step2_trades.parquet"),
    Path("data/features/phase3_step2_cryptobert_trades.parquet"),
)
TUNING_FILES = (
    *(Path(f"reports/phase3_step2/tuning_r{r}.csv") for r in (1, 2, 3)),
    Path("reports/phase3_step2_cryptobert/tuning_r3.csv"),
)
MODEL_STRATEGIES = ("logreg", "lgbm", "lgbm_tuned")
EULER_GAMMA = 0.5772156649015329
DAY = 86_400_000


# ---------- daily returns ----------


def daily_returns(trades: pl.DataFrame, start_ms: int, end_ms: int, risk: float) -> pl.DataFrame:
    """Wide frame: day + one column per configuration (risk x sum of net R of trades exiting that day)."""
    days = pl.DataFrame({"day": np.arange(start_ms // DAY, (end_ms - 1) // DAY + 1)})
    t = trades.with_columns(day=pl.col("exit_time") // DAY, r=pl.col("net_R") * risk)
    agg = t.group_by("config", "day").agg(pl.col("r").sum())
    wide = agg.pivot("config", index="day", values="r")
    out = days.join(wide, on="day", how="left").fill_null(0.0)
    return out.select("day", *sorted(c for c in out.columns if c != "day"))


def configurations(paths=TRADES) -> pl.DataFrame:
    """Trades of every evaluated model configuration, with a `config` name."""
    frames = []
    for p in paths:
        t = pl.read_parquet(p).filter(pl.col("strategy").is_in(MODEL_STRATEGIES) & (pl.col("seed") == -1))
        scorer = "cryptobert" if "cryptobert" in p.name else "finbert"
        frames.append(
            t.with_columns(
                config=pl.format(
                    "{}/{}/{}/r{}/{}", "label_set", "scenario", "strategy", "rung", pl.lit(scorer)
                )
            ).select("config", "exit_time", "net_R")
        )
    return pl.concat(frames)


# ---------- deflated Sharpe ----------


def sharpe(x: np.ndarray) -> float:
    s = x.std(ddof=1)
    return float(x.mean() / s) if s > 0 else np.nan


def moments(x: np.ndarray) -> tuple[float, float]:
    """Skewness and non-excess kurtosis."""
    z = (x - x.mean()) / x.std(ddof=0)
    return float((z**3).mean()), float((z**4).mean())


def psr(sr: float, n: int, skew: float, kurt: float, sr_ref: float) -> float:
    """P(true SR > sr_ref) given the observed per-period SR over n periods (non-normal returns)."""
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    return float(norm.cdf((sr - sr_ref) * math.sqrt(n - 1) / math.sqrt(denom))) if denom > 0 else np.nan


def expected_max_sharpe(n_trials: int, variance: float) -> float:
    """E[max SR] of n_trials unskilled trials with SR variance `variance` (Bailey & Lopez de Prado 2014)."""
    return math.sqrt(variance) * (
        (1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials) + EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(returns: pl.DataFrame, n_tuning_trials: int, n_untuned: int) -> dict:
    cols = [c for c in returns.columns if c != "day"]
    srs = {c: sharpe(returns[c].to_numpy()) for c in cols}
    best = max(srs, key=lambda c: srs[c])
    x = returns[best].to_numpy()
    skew, kurt = moments(x)
    variance = float(np.var(list(srs.values()), ddof=1))
    n = n_tuning_trials + n_untuned
    sr_star = expected_max_sharpe(n, variance)
    sr = srs[best]
    return {
        "best_config": best,
        "n_days": len(x),
        "sr_daily": sr,
        "sr_annual": sr * math.sqrt(365.25),
        "skew": skew,
        "kurtosis": kurt,
        "n_trials": n,
        "n_tuning_trials": n_tuning_trials,
        "n_configs": n_untuned,
        "sr_variance": variance,
        "sr_star_daily": sr_star,
        "sr_star_annual": sr_star * math.sqrt(365.25),
        "psr_zero": psr(sr, len(x), skew, kurt, 0.0),
        "dsr": psr(sr, len(x), skew, kurt, sr_star),
        "sharpe_by_config": srs,
    }


# ---------- PBO via CSCV (adapted, see header) ----------


def _sharpe_cols(a: np.ndarray) -> np.ndarray:
    s = a.std(axis=0, ddof=1)
    return np.where(s > 0, a.mean(axis=0) / np.where(s > 0, s, 1), 0.0)


def cscv_pbo(returns_matrix: np.ndarray, n_groups: int = 16) -> dict:
    """CSCV estimate of the probability of backtest overfitting.

    returns_matrix: (n_periods, n_strategies). Rows are split into n_groups contiguous sub-groups (tail rows
    that don't fill a group are dropped); for every half/half split of the groups, the strategy with the best
    in-sample Sharpe is ranked out of sample; PBO = share of splits where that rank is in the bottom half.
    """
    arr = np.asarray(returns_matrix, dtype=float)
    if arr.ndim != 2:
        raise ValueError("returns_matrix must be 2-D (periods x strategies)")
    n_periods, n_strats = arr.shape
    if n_groups % 2 or n_groups < 2 or n_strats < 2 or n_periods < n_groups:
        raise ValueError(f"need an even n_groups >= 2, >= 2 strategies, >= n_groups periods; got {arr.shape}")
    rows = n_periods // n_groups
    groups = [arr[g * rows : (g + 1) * rows] for g in range(n_groups)]
    logits, winners, sr_is_w, sr_oos_w = [], [], [], []
    for is_groups in combinations(range(n_groups), n_groups // 2):
        is_rows = np.concatenate([groups[g] for g in is_groups])
        oos_rows = np.concatenate([groups[g] for g in range(n_groups) if g not in is_groups])
        sr_is, sr_oos = _sharpe_cols(is_rows), _sharpe_cols(oos_rows)
        w = int(np.argmax(sr_is))
        rank = int(sr_oos.argsort().argsort()[w])  # 0 = worst
        w_bar = (rank + 1) / (n_strats + 1)
        logits.append(math.log(w_bar / (1 - w_bar)))
        winners.append(w)
        sr_is_w.append(sr_is[w])
        sr_oos_w.append(sr_oos[w])
    logits = np.array(logits)
    return {
        "pbo": float((logits <= 0).mean()),
        "logits": logits,
        "winners": np.array(winners),
        "is_sr": np.array(sr_is_w),
        "oos_sr": np.array(sr_oos_w),
        "n_groups": n_groups,
        "rows_per_group": rows,
    }


def main() -> None:
    cfg = load_eval_config()
    start, end = make_folds(cfg)[0].test_start, to_ms(cfg.holdout_start)
    returns = daily_returns(configurations(), start, end, cfg.risk_per_trade)
    OUT.mkdir(parents=True, exist_ok=True)
    returns.write_csv(OUT / "daily_returns.csv")
    n_tuning = sum(pl.read_csv(p).height for p in TUNING_FILES)
    cols = [c for c in returns.columns if c != "day"]
    n_untuned = sum("/lgbm_tuned/" not in c for c in cols)
    dsr = deflated_sharpe(returns, n_tuning, n_untuned)
    (OUT / "dsr.json").write_text(json.dumps(dsr, indent=2))
    res = cscv_pbo(returns.select(cols).to_numpy())
    winner_counts = np.bincount(res["winners"], minlength=len(cols))
    top = int(winner_counts.argmax())
    pbo = {
        "pbo": res["pbo"],
        "n_strategies": len(cols),
        "n_days": returns.height,
        "n_groups": res["n_groups"],
        "rows_per_group": res["rows_per_group"],
        "n_splits": len(res["logits"]),
        "logit_median": float(np.median(res["logits"])),
        "is_sr_mean": float(res["is_sr"].mean()),
        "oos_sr_mean": float(res["oos_sr"].mean()),
        "p_oos_loss": float((res["oos_sr"] < 0).mean()),
        "top_winner": cols[top],
        "top_winner_share": float(winner_counts[top] / len(res["logits"])),
        "winner_shares": {cols[i]: float(c / len(res["logits"])) for i, c in enumerate(winner_counts) if c},
    }
    by_group = []  # same label set and cost scenario: configurations that are true alternatives
    for group in sorted({"/".join(c.split("/")[:2]) for c in cols}):
        gc = [c for c in cols if c.startswith(group + "/")]
        r = cscv_pbo(returns.select(gc).to_numpy())
        by_group.append(
            {
                "group": group,
                "n_strategies": len(gc),
                "pbo": r["pbo"],
                "p_oos_loss": float((r["oos_sr"] < 0).mean()),
            }
        )
    pbo["by_group"] = by_group
    (OUT / "pbo.json").write_text(json.dumps(pbo, indent=2))
    print(json.dumps({k: v for k, v in dsr.items() if k != "sharpe_by_config"}, indent=1))
    print(json.dumps({k: v for k, v in pbo.items() if k != "winner_shares"}, indent=1))


if __name__ == "__main__":
    main()
