from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from mbpe.labels.triple_barrier import (
    LabelConfig,
    atr_bars,
    build_labels,
    decision_atr,
    label_trades,
    scenario_net_r,
)

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


FUNDING_SCHEMA = {
    "calc_time": pl.Datetime("ms", "UTC"),
    "funding_interval_hours": pl.Int64,
    "last_funding_rate": pl.Float64,
}


def funding(*minutes_rates: tuple[int, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "calc_time": [T + timedelta(minutes=m) for m, _ in minutes_rates],
            "funding_interval_hours": [8] * len(minutes_rates),
            "last_funding_rate": [r for _, r in minutes_rates],
        },
        schema=FUNDING_SCHEMA,
    )


def funding_schedule(start: datetime, hours: int, rate: float = 1e-4) -> pl.DataFrame:
    """A complete 8 h settlement schedule (00/08/16 UTC) covering `hours` from `start`, plus one interval."""
    times = [
        start + timedelta(hours=h) for h in range(0, hours + 9) if (start + timedelta(hours=h)).hour % 8 == 0
    ]
    return pl.DataFrame(
        {
            "calc_time": times,
            "funding_interval_hours": [8] * len(times),
            "last_funding_rate": [rate] * len(times),
        },
        schema=FUNDING_SCHEMA,
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


def random_candles(hours: int, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    n = hours * 60
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    return pl.DataFrame(
        {
            "open_time": [START + timedelta(minutes=m) for m in range(n)],
            "open": close,
            "high": close + rng.uniform(0, 1, n),
            "low": close - rng.uniform(0, 1, n),
            "close": close,
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


def shock_from(df: pl.DataFrame, t: datetime) -> pl.DataFrame:
    after = pl.col("open_time") >= t
    return df.with_columns(
        high=pl.when(after).then(pl.col("high") * 3).otherwise("high"),
        low=pl.when(after).then(pl.col("low") / 3).otherwise("low"),
        close=pl.when(after).then(pl.col("close") * 2).otherwise("close"),
    )


def atr_at(df: pl.DataFrame, cfg: LabelConfig, t: datetime):
    return decision_atr(df, cfg).filter(pl.col("decision_time") == t)["atr"][0]


START = datetime(2024, 1, 1, tzinfo=UTC)


def test_atr_uses_only_bars_closed_by_t():
    df, cfg = random_candles(30), LabelConfig()
    t = START + timedelta(hours=20)
    atr = atr_at(df, cfg, t)
    assert atr is not None
    assert atr_at(shock_from(df, t), cfg, t) == atr
    assert atr == atr_bars(df, "1h", 14).filter(pl.col("bar_close") == t)["atr"][0]  # the bar closing at t
    # warm-up: no ATR before 14 complete bars
    assert decision_atr(df, cfg)["atr"].head(13).is_null().all()


def test_4h_atr_uses_only_4h_bars_closed_by_t():
    df, cfg = random_candles(80), LabelConfig(atr_timeframe="4h")
    closed = START + timedelta(hours=64)  # a 4h bar closes here (bars start 00:00 UTC)
    atr = atr_at(df, cfg, closed)
    assert atr is not None
    # t = 64h..67h all use the bar that closed at 64h; the 64h-68h bar is still forming
    for h in range(4):
        assert atr_at(df, cfg, closed + timedelta(hours=h)) == atr
    # changing candles from 64h on (the forming bar and later) doesn't change those ATRs ...
    shocked = shock_from(df, closed)
    for h in range(4):
        assert atr_at(shocked, cfg, closed + timedelta(hours=h)) == atr
    # ... but does change the ATR once the next bar has closed (sanity: the shock is visible)
    assert atr_at(shocked, cfg, closed + timedelta(hours=4)) != atr_at(df, cfg, closed + timedelta(hours=4))
    # warm-up: 14 bars of 4h = 56h
    assert atr_at(df, cfg, START + timedelta(hours=55)) is None
    assert atr_at(df, cfg, START + timedelta(hours=56)) is not None


def test_taker_scenario_reproduces_stored_net_r_exactly():
    labels, _ = build_labels(random_candles(80, seed=1), funding_schedule(START, 80), LabelConfig())
    assert labels.height > 100
    assert (scenario_net_r(labels, LabelConfig(), "taker") == labels["net_R"]).all()


@pytest.mark.parametrize(
    "path, expected",
    [
        ({3: (100, 121, 99, 118)}, 2.0 - 0.0002 * (100 + 120) / 10),  # maker entry + maker tp, no slippage
        (
            {2: (100, 101, 89, 92)},
            -1.0 - (0.0002 * 100 + (0.0005 + 0.0002) * 90) / 10,
        ),  # maker entry, taker stop
        ({5: (100, 106, 99, 105)}, 0.5 - (0.0002 * 100 + (0.0005 + 0.0002) * 105) / 10),  # taker expiry
    ],
    ids=["tp", "sl", "expiry"],
)
def test_maker_entry_tp_scenario(path, expected):
    decisions = pl.DataFrame({"decision_time": [T], "atr": [ATR]}).with_columns(
        pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
    )
    labels, _ = label_trades(candles(path), funding(), decisions, CFG)
    long = labels.filter(pl.col("side") == "long")
    assert scenario_net_r(long, CFG, "maker_entry_tp")[0] == pytest.approx(expected)


def test_unknown_cost_scenario_raises():
    with pytest.raises(ValueError, match="unknown cost scenario"):
        scenario_net_r(pl.DataFrame(), CFG, "free")


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


def test_missing_funding_inside_a_trade_window_fails():
    from mbpe.labels.triple_barrier import MissingFundingError

    full = funding_schedule(START, 80)
    gap = full.filter(pl.col("calc_time") != START + timedelta(hours=40))  # one settlement missing
    with pytest.raises(MissingFundingError, match="funding time with no funding data"):
        build_labels(random_candles(80, seed=1), gap, LabelConfig())
    with pytest.raises(MissingFundingError):
        build_labels(random_candles(80, seed=1), full.head(0), LabelConfig())  # none at all: never zero
    build_labels(random_candles(80, seed=1), full, LabelConfig())  # complete schedule passes


def test_funding_interval_must_match_config():
    odd = funding_schedule(START, 80).with_columns(funding_interval_hours=pl.lit(4, pl.Int64))
    with pytest.raises(ValueError, match="funding intervals"):
        build_labels(random_candles(80, seed=1), odd, LabelConfig())


def test_daily_atr_and_4_hourly_decisions():
    """Phase 5: decisions only at 00/04/.../20 UTC; daily ATR from the UTC daily bar closed at or before t."""
    df = random_candles(24 * 17)
    cfg = LabelConfig(atr_timeframe="1d", decision_every_hours=4)
    d = decision_atr(df, cfg)
    assert set(d["decision_time"].dt.hour().unique().to_list()) == {0, 4, 8, 12, 16, 20}
    midnight = START + timedelta(days=15)  # the 15th daily bar closes here: first ATR(14) = after warm-up
    atr = atr_at(df, cfg, midnight)
    assert atr is not None and atr_at(df, cfg, midnight - timedelta(hours=4)) is not None
    assert atr == atr_bars(df, "1d", 14).filter(pl.col("bar_close") == midnight)["atr"][0]
    shocked = shock_from(df, midnight)  # the forming day must not leak into t = 00:00 ... 20:00
    for h in range(0, 24, 4):
        assert atr_at(shocked, cfg, midnight + timedelta(hours=h)) == atr
    assert atr_at(shocked, cfg, midnight + timedelta(days=1)) != atr_at(df, cfg, midnight + timedelta(days=1))


def test_default_config_stays_hourly():
    d = decision_atr(random_candles(30), LabelConfig())
    assert d["decision_time"].dt.hour().n_unique() == 24
