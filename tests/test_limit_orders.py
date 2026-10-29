from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from mbpe.labels.limit_orders import FILL_MINUTES, limit_order_trade, run_book
from mbpe.labels.triple_barrier import label_trades, load_config

CFG = load_config()
T0 = datetime(2026, 1, 1, tzinfo=UTC)
DEC = int(T0.timestamp() * 1000)
MIN = 60_000


def frame(overrides=None, drop=(), minutes=400):
    overrides = overrides or {}
    rows = []
    for m in range(1, minutes):
        if m in drop:
            continue
        r = {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
        r.update(overrides.get(m - 1, {}))
        rows.append({"open_time": T0 + timedelta(minutes=m), **r})
    return pl.DataFrame(rows).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


def funding(hours=48):
    times = [T0 + timedelta(hours=h) for h in range(0, hours, 8)]
    return pl.DataFrame(
        {
            "calc_time": times,
            "last_funding_rate": [0.0] * len(times),
            "funding_interval_hours": [8] * len(times),
        }
    ).with_columns(pl.col("calc_time").cast(pl.Datetime("ms", "UTC")))


def trade(overrides=None, side=1, **kw):
    return limit_order_trade(DEC, side, 1.0, frame(overrides, **kw), funding(), CFG)


def test_unfilled_when_only_touched():
    r = trade()
    assert r["status"] == "unfilled"
    assert r["free_ms"] == DEC + 16 * MIN


def test_fill_window_edges():
    r = trade({14: {"low": 99.9}})
    assert r["status"] == "closed"
    assert r["fill_ms"] == DEC + 15 * MIN
    assert FILL_MINUTES == 15
    assert trade({15: {"low": 99.9}})["status"] == "unfilled"


def test_take_profit_needs_trade_through():
    base = {0: {"low": 99.9}}
    assert trade({**base, 5: {"high": 102.0}})["exit_reason"] == "expiry"
    r = trade({**base, 6: {"high": 102.1}})
    assert r["exit_reason"] == "tp"
    assert r["exit_price"] == 102.0
    assert r["gross_R"] == pytest.approx(2.0)


def test_fill_candle_stop_and_tp():
    r = trade({3: {"low": 98.9}})
    assert r["exit_reason"] == "sl"
    assert r["exit_ms"] == DEC + 4 * MIN + MIN
    r = trade({3: {"low": 99.9, "high": 102.5}})
    assert r["exit_reason"] == "expiry"


def test_costs():
    r = trade({0: {"low": 99.9}, 6: {"high": 102.1}})
    assert r["fee_R"] == pytest.approx(0.0002 * 100 + 0.0002 * 102)
    assert r["slippage_R"] == 0
    r = trade({0: {"low": 99.9}, 6: {"low": 98.9}})
    assert r["exit_reason"] == "sl"
    assert r["fee_R"] == pytest.approx(0.0002 * 100 + 0.0005 * 99)
    assert r["slippage_R"] == pytest.approx(2e-4 * 99)


def test_short_mirror():
    r = trade({0: {"high": 100.1}, 6: {"low": 97.9}}, side=-1)
    assert r["exit_reason"] == "tp"
    assert r["gross_R"] == pytest.approx(2.0)


def test_gap_stop_exits_at_open():
    r = trade({0: {"low": 99.9}, 6: {"open": 98.0, "low": 97.0}})
    assert r["exit_price"] == 98.0
    assert r["exit_ms"] == DEC + 7 * MIN


def test_gap_returns_none():
    assert trade({0: {"low": 99.9}}, drop=(101,)) is None


def test_missing_funding_returns_none():
    short = funding().filter(pl.col("calc_time") < T0 + timedelta(hours=1))
    c = frame({420: {"low": 99.9}}, minutes=700)
    assert limit_order_trade(DEC + 420 * MIN, 1, 1.0, c, short, CFG) is None
    assert limit_order_trade(DEC + 420 * MIN, 1, 1.0, c, funding(), CFG)["status"] == "closed"


def test_run_book_busy_and_free():
    hold = {0: {"low": 99.9}, 175: {"high": 102.1}}
    c = frame(hold, minutes=900)
    dec = [(DEC + h * 60 * MIN, 1, 1.0) for h in (0, 1, 2, 4)]
    out = run_book(dec, c, funding(72), CFG)
    assert [r["status"] for r in out] == ["closed", "skip", "skip", "unfilled"]
    assert out[0]["free_ms"] == DEC + 177 * MIN
    un = frame(minutes=900)
    out = run_book([(DEC, 1, 1.0), (DEC + 15 * MIN, 1, 1.0), (DEC + 16 * MIN, 1, 1.0)], un, funding(72), CFG)
    assert [r["status"] for r in out] == ["unfilled", "skip", "unfilled"]


def test_run_book_missing_does_not_block():
    out = run_book([(DEC, 1, 1.0), (DEC + 60 * MIN, 1, 1.0)], frame(drop=(10,)), funding(), CFG)
    assert out[0]["status"] == "missing_candles"
    assert out[1]["status"] != "skip"


def test_matches_label_trades_when_filled_at_open_with_taker_costs():
    rng = np.random.default_rng(0)
    c = frame(minutes=400)
    n = c.height
    walk = 100 + np.cumsum(rng.normal(0, 0.05, n))
    c = c.with_columns(
        open=pl.Series(walk),
        close=pl.Series(np.append(walk[1:], walk[-1])),
        high=pl.Series(walk + 0.1),
        low=pl.Series(walk - 0.1),
    )
    f = funding().with_columns(last_funding_rate=pl.Series([0.0003] * 6))
    dec = pl.DataFrame({"decision_time": [T0], "atr": [1.0]}).with_columns(
        pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
    )
    ref, _ = label_trades(c, f, dec, CFG)
    for side, name in ((1, "long"), (-1, "short")):
        r = limit_order_trade(DEC, side, 1.0, c, f, CFG)
        row = ref.filter(pl.col("side") == name).row(0, named=True)
        assert r["status"] == "closed"
        assert r["fill_ms"] == DEC + MIN
        assert r["exit_reason"] == row["exit_reason"]
        assert r["gross_R"] == pytest.approx(row["gross_R"])
        assert r["funding_R"] == pytest.approx(row["funding_R"])
