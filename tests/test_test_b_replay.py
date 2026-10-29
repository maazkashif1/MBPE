from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.research import phase6_models as p6
from mbpe.research import test_b_replay as tb
from mbpe.research.walk_forward import Fold

HOUR = 3_600_000
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T0_MS = int(T0.timestamp() * 1000)


def _inputs():
    n = 72 * 60
    candles = pl.DataFrame(
        {
            "open_time": [T0 + timedelta(minutes=m) for m in range(n)],
            "open": [100.0] * n,
            "high": [100.5] * n,
            "low": [99.5] * n,
            "close": [100.0] * n,
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))
    times = [T0 + timedelta(hours=h) for h in range(0, 96, 8)]
    funding = pl.DataFrame(
        {
            "calc_time": times,
            "last_funding_rate": [0.0] * len(times),
            "funding_interval_hours": [8] * len(times),
        }
    ).with_columns(pl.col("calc_time").cast(pl.Datetime("ms", "UTC")))
    hours = [20, 21, 30, 31, 40]
    cand = pl.DataFrame(
        {
            "fold": [0, 0, 0, 1, 1],
            "decision_time": [T0_MS + h * HOUR for h in hours],
            "side": [1, -1, 1, 1, -1],
            "meta_p": [0.9] * 5,
            "val_thr": [0.5] * 5,
            "net_R_taker": [-0.1, -0.2, -0.3, -0.4, -0.5],
            "kind": ["lgbm"] * 5,
        }
    )
    overall = pl.DataFrame(
        {
            "model": ["lgbm"],
            "strategy": ["primary_metaval"],
            "scenario": ["taker"],
            "seed": [-1],
            "period": ["folds 0-1"],
            "trades": [7],
            "mean_R": [-0.1],
            "ci_lo": [-0.2],
            "ci_hi": [0.0],
        }
    )
    decision = np.array([T0_MS + h * HOUR for h in range(15, 60)])
    return {
        "cfg": p6.cfg_p6().model_copy(update={"bootstrap_samples": 50}),
        "candles": candles,
        "funding": funding,
        "cand": cand,
        "overall": overall,
        "decision": decision,
        "exit": decision + 4 * HOUR,
        "eligible": np.ones(len(decision), bool),
    }


def test_replay_on_synthetic_inputs(monkeypatch, tmp_path: Path):
    folds = [Fold(0, T0_MS + 15 * HOUR, T0_MS + 35 * HOUR), Fold(1, T0_MS + 35 * HOUR, T0_MS + 60 * HOUR)]
    monkeypatch.setattr(tb, "_load", _inputs)
    monkeypatch.setattr(tb, "make_folds", lambda cfg: folds)
    monkeypatch.setattr(tb, "FIRST_FOLD", 0)
    r = tb.replay(tmp_path)
    assert r["decisions"] == 5
    assert r["busy_skips"] > 0
    assert r["attempted"] == r["decisions"] - r["busy_skips"] - r["missing_candles"] - r["excluded_atr"]
    assert r["fill_rate"] == r["filled"] / r["attempted"]
    assert r["filled"] == 3
    assert r["taker_same_orders"]["n"] == 3
    assert r["taker_same_orders"]["mean_R"] == np.mean([-0.1, -0.3, -0.5])
    assert r["always_long"]["decisions"] == 45
    assert (tmp_path / "replay.json").exists()
