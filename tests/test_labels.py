from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from mbpe.labels.triple_barrier import LabelConfig, hourly_atr, label_trades

T = datetime(2024, 1, 1, 12, tzinfo=UTC)  # decision time
CFG = LabelConfig(horizon_minutes=5, latency_minutes=1)  # entry at T+1m, window T+1m .. T+5m
COST = 0.0005 + 2 / 1e4  # taker fee + slippage, per side
ATR = 10.0  # entry 100 -> long tp 120, sl 90; short tp 80, sl 110
FLAT = (100.0, 101.0, 99.0, 100.0)


def candles(path: dict[int, tuple] | None = None, minutes: range = range(0, 7)) -> pl.DataFrame:
    """1m candles at T+m for m in `minutes`; flat (o,h,l,c) unless overridden in `path`."""
    path = path or {}
    rows = [(T + timedelta(minutes=m), *path.get(m, FLAT)) for m in minutes]
    return pl.DataFrame(
        rows, schema=["open_time", "open", "high", "low", "close"], orient="row"
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


def funding(*minutes_rates: tuple[int, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "calc_time": [T + timedelta(minutes=m) for m, _ in minutes_rates],
            "last_funding_rate": [r for _, r in minutes_rates],
        },
        schema={"calc_time": pl.Datetime("ms", "UTC"), "last_funding_rate": pl.Float64},
    )


def label(path=None, fund=None, side="long", minutes=range(0, 7), atr=ATR) -> dict:
    decisions = pl.DataFrame({"decision_time": [T], "atr": [atr]}).with_columns(
        pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
    )
    out, _ = label_trades(candles(path, minutes), fund if fund is not None else funding(), decisions, CFG)
    return out.filter(pl.col("side") == side).row(0, named=True)


def test_take_profit():
    r = label({3: (100, 121, 99, 118)})
    assert r["exit_reason"] == "tp"
    assert r["exit_time"] == T + timedelta(minutes=4)  # close of the T+3m candle
    assert r["gross_R"] == pytest.approx(2.0)
    assert r["net_R"] == pytest.approx(2.0 - COST * (100 + 120) / 10)  # 1.9846
    assert r["label"]


def test_stop():
    r = label({2: (100, 101, 89, 92)})
    assert r["exit_reason"] == "sl"
    assert r["net_R"] == pytest.approx(-1.0 - COST * (100 + 90) / 10)  # -1.0133
    assert not r["label"]


def test_expiry_exits_at_last_close():
    r = label({5: (100, 106, 99, 105)})
    assert r["exit_reason"] == "expiry"
    assert r["exit_time"] == T + timedelta(minutes=6)  # close of the last window candle (T+5m)
    assert r["net_R"] == pytest.approx(0.5 - COST * (100 + 105) / 10)  # 0.48565


def test_both_barriers_in_one_candle_is_a_stop():
    r = label({3: (100, 125, 85, 100)})
    assert r["exit_reason"] == "sl"
    assert r["gross_R"] == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open():
    r = label({3: (85, 86, 84, 85)})
    assert r["exit_reason"] == "sl"
    assert r["exit_time"] == T + timedelta(minutes=3)  # open of the gapping candle
    assert r["gross_R"] == pytest.approx(-1.5)
    assert r["net_R"] == pytest.approx(-1.5 - COST * (100 + 85) / 10)


@pytest.mark.parametrize(
    "events, long_r",
    [
        ([(3, 0.0001)], -0.0001 * 100 / 10),  # inside the trade
        ([(1, 0.0001)], 0.0),  # at entry time: not strictly after entry
        ([(7, 0.0001)], 0.0),  # after the exit (expiry at T+6m)
        ([(6, 0.0002)], -0.0002 * 100 / 10),  # exactly at exit: included
        ([(3, -0.0001)], 0.0001 * 100 / 10),  # negative funding: longs receive
    ],
)
def test_funding_sign_and_window(events, long_r):
    fund = funding(*events)
    assert label(fund=fund)["funding_R"] == pytest.approx(long_r)
    assert label(fund=fund, side="short")["funding_R"] == pytest.approx(-long_r)


def test_funding_time_jitter_rounds_to_minute():
    fund = funding((6, 0.0002)).with_columns(pl.col("calc_time") + pl.duration(milliseconds=2))
    assert label(fund=fund)["funding_R"] == pytest.approx(-0.0002 * 100 / 10)


def test_missing_candle_in_window_skips_row():
    decisions = pl.DataFrame({"decision_time": [T], "atr": [ATR]}).with_columns(
        pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
    )
    gap = candles(minutes=range(0, 7)).filter(pl.col("open_time") != T + timedelta(minutes=4))
    out, skipped = label_trades(gap, funding(), decisions, CFG)
    assert out.height == 0 and skipped == {"warmup": 0, "missing_candles": 1}


def test_changing_candles_after_exit_does_not_change_row():
    tp_path = {3: (100, 121, 99, 118)}  # exits on the T+3m candle
    base = label(tp_path)
    changed = label({**tp_path, 4: (50, 200, 10, 60), 5: (60, 70, 1, 2)})
    assert changed == base


def test_atr_uses_only_bars_closed_by_t():
    rng = np.random.default_rng(0)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    n = 30 * 60
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    df = pl.DataFrame(
        {
            "open_time": [start + timedelta(minutes=m) for m in range(n)],
            "open": close,
            "high": close + rng.uniform(0, 1, n),
            "low": close - rng.uniform(0, 1, n),
            "close": close,
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))
    t = start + timedelta(hours=20)
    atr = hourly_atr(df, 14).filter(pl.col("decision_time") == t)["atr"][0]
    after = pl.col("open_time") >= t
    shocked = df.with_columns(
        high=pl.when(after).then(pl.col("high") * 3).otherwise("high"),
        low=pl.when(after).then(pl.col("low") / 3).otherwise("low"),
        close=pl.when(after).then(pl.col("close") * 2).otherwise("close"),
    )
    assert atr is not None
    assert hourly_atr(shocked, 14).filter(pl.col("decision_time") == t)["atr"][0] == atr
    # warm-up: no ATR before 14 complete bars
    assert hourly_atr(df, 14)["atr"].head(13).is_null().all()


def test_short_mirrors_long_on_flipped_path():
    e = 100.0
    path = {2: (100, 104, 97, 103), 3: (103, 112, 101, 110), 4: (110, 121, 108, 119)}
    flipped = {m: (2 * e - o, 2 * e - lo, 2 * e - h, 2 * e - c) for m, (o, h, lo, c) in path.items()}
    flipped_flat = {m: (2 * e - o, 2 * e - lo, 2 * e - h, 2 * e - c) for m, (o, h, lo, c) in [(0, FLAT)]}
    long = label(path)
    short = label({**dict.fromkeys(range(7), flipped_flat[0]), **flipped}, side="short")
    for key in ("exit_reason", "exit_time", "gross_R"):
        assert short[key] == pytest.approx(long[key]) if key == "gross_R" else short[key] == long[key]
    assert short["tp"] == pytest.approx(2 * e - long["tp"])
    assert short["sl"] == pytest.approx(2 * e - long["sl"])
