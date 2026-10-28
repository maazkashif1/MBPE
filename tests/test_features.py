"""Leakage tests for every registered market/derivatives feature.

For >= 200 random decision times t: features computed on data truncated at t (only timestamps before t)
must equal features computed on the full data, at t, for every feature. Failures name the feature.
"""

from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from mbpe.features.market import (
    CLEAN,
    DATASETS,
    HOUR,
    MIN,
    REGISTRY,
    UTC_MS,
    build_features,
    load_data,
    load_feature_config,
    truncate,
)

CFG = load_feature_config()
N_TIMES = 200


def synthetic(days: int = 70, seed: int = 1) -> dict[str, pl.DataFrame]:
    """1m futures + spot (with gaps), 5-min metrics (gaps, zero-OI placeholders), 8 h funding (ms jitter)."""
    rng = np.random.default_rng(seed)
    start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    n = days * 1440
    t = start + np.arange(n, dtype=np.int64) * MIN
    close = 40_000 * np.exp(np.cumsum(rng.normal(0, 8e-4, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 6e-4, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 6e-4, n))
    vol = rng.lognormal(2, 0.8, n)

    def klines(scale, keep):
        return pl.DataFrame(
            {
                "open_time": t[keep],
                "open": (open_ * scale)[keep],
                "high": (high * scale)[keep],
                "low": (low * scale)[keep],
                "close": (close * scale)[keep],
                "volume": (vol * (0.2 if scale != 1 else 1))[keep],
                "taker_buy_volume": (vol * rng.uniform(0.3, 0.7, n) * (0.2 if scale != 1 else 1))[keep],
            }
        ).with_columns(pl.col("open_time").cast(UTC_MS))

    fut_keep = np.ones(n, bool)
    fut_keep[rng.choice(n, 40, replace=False)] = False  # a few missing futures minutes
    spot_keep = rng.uniform(size=n) > 0.005
    spot_keep[35 * 1440 : 35 * 1440 + 180] = False  # a 3 h spot outage
    mt = start + np.arange(0, n * MIN, 5 * MIN, dtype=np.int64)
    oi = 80_000 * np.exp(np.cumsum(rng.normal(0, 2e-3, len(mt))))
    oi[rng.choice(len(mt), 15, replace=False)] = 0.0  # placeholder zeros
    m_keep = np.ones(len(mt), bool)
    m_keep[40 * 288 : 40 * 288 + 30] = False  # a 2.5 h metrics gap
    ft = (
        start + np.arange(0, n * MIN, 8 * HOUR, dtype=np.int64) + rng.integers(0, 6, n // 480 + 1)[: n // 480]
    )
    mark_keep = np.ones(n, bool)
    mark_keep[38 * 1440 + 300 : 38 * 1440 + 420] = False
    prem_keep = rng.uniform(size=n) > 0.02
    prem_keep[41 * 1440 + 600 : 41 * 1440 + 780] = False
    return {
        "futures_klines_1m": klines(1.0, fut_keep),
        "spot_klines_1m": klines(0.9995, spot_keep),
        "metrics": pl.DataFrame({"create_time": mt[m_keep], "sum_open_interest": oi[m_keep]}).with_columns(
            pl.col("create_time").cast(UTC_MS)
        ),
        "funding": pl.DataFrame(
            {
                "calc_time": ft,
                "funding_interval_hours": np.full(len(ft), 8),
                "last_funding_rate": rng.normal(1e-4, 2e-4, len(ft)),
            }
        ).with_columns(pl.col("calc_time").cast(UTC_MS)),
        # Phase 6: mark price (a 2 h outage on day 38) and a signed premium index (a 3 h outage on day 41,
        # plus ~2% missing minutes so the 80% window-coverage rule is exercised)
        "mark_price_1m": klines(1.0001, mark_keep),
        "premium_index_1m": pl.DataFrame(
            {
                "open_time": t[prem_keep],
                **{c: rng.normal(1e-4, 3e-4, n)[prem_keep] for c in ("open", "high", "low", "close")},
                "volume": np.zeros(prem_keep.sum()),
                "taker_buy_volume": np.zeros(prem_keep.sum()),
            }
        ).with_columns(pl.col("open_time").cast(UTC_MS)),
    }


def decision_times(data, n: int, seed: int, warmup_days: int = 31, extra: list[int] = ()) -> np.ndarray:
    fut = data["futures_klines_1m"]["open_time"].dt.epoch("ms")
    first = (fut.min() // HOUR + 1 + warmup_days * 24) * HOUR
    last = ((fut.max() + MIN) // HOUR) * HOUR
    hours = np.arange(first, last + 1, HOUR)
    picks = np.random.default_rng(seed).choice(hours, n, replace=False)
    return np.unique(np.r_[picks, np.asarray(extra, dtype=np.int64)])


def assert_no_leakage(data: dict[str, pl.DataFrame], times: np.ndarray) -> None:
    full = build_features(data, CFG, at=times)
    failures: dict[str, list[str]] = {}
    for k, t in enumerate(times):
        cut = build_features(truncate(data, pl.lit(int(t)).cast(UTC_MS)), CFG, at=np.array([t]))
        for name in REGISTRY:
            a, b = full[name][k], cut[name][0]
            if not (a == b or (a is None and b is None)):
                when = datetime.fromtimestamp(t / 1000, UTC).isoformat()
                failures.setdefault(name, []).append(f"{when}: full={a} truncated={b}")
    if failures:
        lines = [f"LEAKAGE in {len(failures)} feature(s):"]
        lines += [f"  {name}: {len(v)} time(s), e.g. {v[0]}" for name, v in failures.items()]
        pytest.fail("\n".join(lines))


def test_every_feature_has_metadata():
    for f in REGISTRY.values():
        assert f.group in {"technical", "derivatives", "macd_ema", "mark_premium"}, f.name
        assert set(f.inputs) <= set(DATASETS), f.name


def test_no_leakage_synthetic():
    data = synthetic()
    start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    near_gaps = (
        [start + (35 * 24 + h) * HOUR for h in range(0, 6)]
        + [start + (40 * 24 + h) * HOUR for h in range(5)]
        + [start + (38 * 24 + h) * HOUR for h in range(4, 9)]  # mark-price outage
        + [start + (41 * 24 + h) * HOUR for h in range(9, 15)]  # premium outage
    )
    times = decision_times(data, N_TIMES, seed=0, extra=near_gaps)
    assert len(times) >= N_TIMES
    assert_no_leakage(data, times)


def test_evaluating_at_selected_times_equals_full_table():
    data = synthetic(days=45, seed=2)
    times = decision_times(data, 50, seed=1, warmup_days=10)
    full = build_features(data, CFG).filter(pl.col("decision_time").dt.epoch("ms").is_in(times.tolist()))
    assert full.equals(build_features(data, CFG, at=times))


@pytest.mark.skipif(not (CLEAN / "metrics.parquet").exists(), reason="clean Binance data not built")
def test_no_leakage_real_data_slice():
    """Mar-May 2021: real spot gaps (04-20, 04-25), a metrics gap (04-27) and zero-OI placeholders (05-22)."""
    lo, hi = datetime(2021, 3, 1, tzinfo=UTC), datetime(2021, 5, 25, tzinfo=UTC)
    data = {
        k: df.filter(pl.col(c).is_between(lo, hi, closed="left"))
        for k, df, c in (
            (k, df, df.columns[0] if k != "metrics" else "create_time") for k, df in load_data().items()
        )
    }
    gaps = [
        datetime(2021, 4, d, h, tzinfo=UTC)
        for d, h in ((20, 3), (20, 5), (25, 6), (25, 9), (27, 13), (27, 14))
    ]
    extra = [int(g.timestamp() * 1000) for g in gaps] + [
        int(datetime(2021, 5, 22, 5, tzinfo=UTC).timestamp() * 1000)
    ]
    assert_no_leakage(data, decision_times(data, N_TIMES, seed=3, extra=extra))


def test_harness_catches_a_leaky_feature():
    """A feature that peeks one hour ahead must make the leakage check fail, naming it."""
    from mbpe.features.market import Feature

    REGISTRY["leaky_future_return"] = Feature(
        "leaky_future_return",
        "technical",
        ("futures_klines_1m",),
        lambda ctx: np.log(ctx.price_at(ctx.grid + HOUR) / ctx.price),
    )
    try:
        data = synthetic(days=40, seed=4)
        with pytest.raises(pytest.fail.Exception, match="leaky_future_return"):
            assert_no_leakage(data, decision_times(data, 20, seed=5, warmup_days=5))
    finally:
        del REGISTRY["leaky_future_return"]


def test_oi_price_quadrant_matches_signs():
    data = synthetic(days=40, seed=6)
    times = decision_times(data, 100, seed=7, warmup_days=5)
    df = build_features(data, CFG, at=times)
    for w in ("1h", "4h", "24h"):
        r, oi, q = df[f"ret_{w}"], df[f"oi_chg_{w}"], df[f"oi_price_quadrant_{w}"]
        ok = r.is_not_null() & oi.is_not_null()
        expected = 1 + 2 * (r < 0).cast(pl.Int64) + (oi < 0).cast(pl.Int64)
        assert (q.filter(ok) == expected.filter(ok).cast(pl.Float64)).all(), w
        assert q.filter(~ok).is_null().all(), w
        assert set(q.drop_nulls().unique().to_list()) <= {1.0, 2.0, 3.0, 4.0}


def test_mark_premium_values_and_outages():
    data = synthetic(days=45, seed=8)
    start = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    in_mark_gap, in_prem_gap = start + (38 * 24 + 7) * HOUR, start + (41 * 24 + 13) * HOUR
    ok = start + 20 * 24 * HOUR
    names = ["basis_mark_bps", "premium_last_bps", "premium_mean_1h_bps", "premium_mean_8h_bps"]
    df = build_features(data, CFG, at=np.array([ok, in_mark_gap, in_prem_gap]), names=names)
    a, b, c = df.iter_rows(named=True)
    assert abs(a["basis_mark_bps"] - (1 / 1.0001 - 1) * 1e4) < 1e-6  # futures vs a 1 bp higher mark
    assert b["basis_mark_bps"] is None and b["premium_last_bps"] is not None  # mark stale, premium fine
    assert c["premium_last_bps"] is None and c["premium_mean_1h_bps"] is None  # premium outage -> NaN
    assert c["premium_mean_8h_bps"] is None  # 3 of 8 hours missing: below the 80% rule
    # coverage edge of the 8 h window after the 10:00-13:00 gap: at 19:00 2 h are missing (75%) -> NaN;
    # at 20:00 1 h is missing (~86% after the random 2% gaps) -> a value
    edge = build_features(
        data, CFG, at=np.array([start + (41 * 24 + 19) * HOUR, start + (41 * 24 + 20) * HOUR]),
        names=["premium_mean_8h_bps"],
    )["premium_mean_8h_bps"]  # fmt: skip
    assert edge[0] is None and edge[1] is not None
    p = data["premium_index_1m"].filter(pl.col("open_time").dt.epoch("ms").is_between(ok - HOUR, ok - 1))
    assert a["premium_mean_1h_bps"] == pytest.approx(p["close"].mean() * 1e4)
