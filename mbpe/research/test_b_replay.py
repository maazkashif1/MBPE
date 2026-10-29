import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.labels.limit_orders import run_book
from mbpe.labels.triple_barrier import decision_atr, load_config
from mbpe.research import phase6_models as p6
from mbpe.research.backtest import block_bootstrap_ci
from mbpe.research.experiment import load_dataset, prepare_arrays
from mbpe.research.walk_forward import make_folds, split

OUT = Path("reports/test_b")
CANDIDATES = Path("data/features/phase6_candidates.parquet")
KLINES = Path("data/clean/binance/futures_klines_1m.parquet")
FUNDING = Path("data/clean/binance/funding.parquet")
OVERALL = Path("reports/phase6/eval/overall_metrics.csv")
FIRST_FOLD = p6.META_FIRST_FOLD


def _load() -> dict:
    cfg = p6.cfg_p6()
    df = load_dataset("A", cfg)
    decision, arr, _, eligible = prepare_arrays(df, cfg)
    return {
        "cfg": cfg,
        "candles": pl.read_parquet(KLINES),
        "funding": pl.read_parquet(FUNDING),
        "cand": pl.read_parquet(CANDIDATES),
        "overall": pl.read_csv(OVERALL),
        "decision": decision,
        "exit": arr["long"]["exit"],
        "eligible": eligible,
    }


def _stats(r: np.ndarray, cfg) -> dict:
    lo, hi = block_bootstrap_ci(r, cfg.bootstrap_block, cfg.bootstrap_samples)
    return {
        "n": int(len(r)),
        "mean_R": float(r.mean()) if len(r) else float("nan"),
        "ci_lo": lo,
        "ci_hi": hi,
    }


def _book(decisions: list[tuple[int, int]], atr: dict, candles, funding, lcfg) -> tuple[list[dict], int]:
    ok = [(t, s, atr[t]) for t, s in decisions if np.isfinite(atr.get(t, np.nan)) and atr[t] > 0]
    return run_book(ok, candles, funding, lcfg), len(decisions) - len(ok)


def replay(out: Path = OUT) -> dict:
    d = _load()
    cfg, cand = d["cfg"], d["cand"]
    lcfg = load_config()
    candles, funding = d["candles"], d["funding"]
    a = decision_atr(candles, lcfg)
    atr = {
        t: np.nan if v is None else v
        for t, v in zip(a["decision_time"].dt.epoch("ms").to_list(), a["atr"].to_list(), strict=True)
    }
    folds = [f for f in make_folds(cfg) if f.index >= FIRST_FOLD]

    c = (
        cand.filter(
            (pl.col("kind") == "lgbm")
            & (pl.col("fold") >= FIRST_FOLD)
            & (pl.col("meta_p").fill_null(-1) >= pl.col("val_thr").fill_null(np.inf))
        )
        .sort("decision_time")
        .select("decision_time", "side", "net_R_taker")
    )
    res, excluded = _book(
        list(zip(c["decision_time"].to_list(), c["side"].to_list(), strict=True)), atr, candles, funding, lcfg
    )
    closed = [r for r in res if r["status"] == "closed"]
    unfilled = [r for r in res if r["status"] == "unfilled"]
    busy = sum(r["status"] == "skip" for r in res)
    missing = sum(r["status"] == "missing_candles" for r in res)
    attempted = len(closed) + len(unfilled)
    taker = c.filter(pl.col("decision_time").is_in([r["decision_ms"] for r in closed]))["net_R_taker"]

    test = np.zeros(len(d["decision"]), bool)
    for f in folds:
        test |= split(d["decision"], d["exit"], f, cfg)["test"]
    al_dec = [(int(t), 1) for t in d["decision"][test & d["eligible"]]]
    al_res, al_excluded = _book(al_dec, atr, candles, funding, lcfg)
    al_closed = [r for r in al_res if r["status"] == "closed"]
    al_stats = _stats(np.array([r["net_R"] for r in al_closed]), cfg)

    ov = (
        d["overall"]
        .filter(
            (pl.col("model") == "lgbm")
            & (pl.col("strategy") == "primary_metaval")
            & (pl.col("scenario") == "taker")
            & (pl.col("seed") == -1)
            & (pl.col("period") == f"folds {FIRST_FOLD}-{folds[-1].index}")
        )
        .row(0, named=True)
    )

    result = {
        "decisions": c.height,
        "attempted": attempted,
        "filled": len(closed),
        "fill_rate": len(closed) / attempted if attempted else float("nan"),
        "busy_skips": busy,
        "missing_candles": missing,
        "excluded_atr": excluded,
        "test_b": _stats(np.array([r["net_R"] for r in closed]), cfg),
        "taker_same_orders": _stats(taker.to_numpy(), cfg),
        "test_a_walk_forward": {
            "n": int(ov["trades"]),
            "mean_R": ov["mean_R"],
            "ci_lo": ov["ci_lo"],
            "ci_hi": ov["ci_hi"],
        },
        "always_long": {
            "decisions": len(al_dec),
            "attempted": len(al_closed) + sum(r["status"] == "unfilled" for r in al_res),
            "filled": len(al_closed),
            "missing_candles": sum(r["status"] == "missing_candles" for r in al_res),
            "excluded_atr": al_excluded,
            "mean_R": al_stats["mean_R"],
            "ci_lo": al_stats["ci_lo"],
            "ci_hi": al_stats["ci_hi"],
        },
        "run_at": datetime.now(UTC).isoformat(),
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip(),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "replay.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(replay(), indent=2))
