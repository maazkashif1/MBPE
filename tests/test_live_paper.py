from datetime import timedelta

import numpy as np
import polars as pl

from mbpe.labels.triple_barrier import build_labels, load_config
from mbpe.live import paper_trading as paper
from mbpe.research.backtest import simulate
from tests.test_labels import START, funding_schedule, random_candles

HOUR = 3_600_000


def test_live_paper_trading_matches_the_backtest(tmp_path):
    cfg = load_config(paper.LABEL_CONFIG)
    candles = random_candles(48, seed=4)
    fund = funding_schedule(START, 48)
    labels, _ = build_labels(candles, fund, cfg)
    lab = labels.with_columns(t=pl.col("decision_time").dt.epoch("ms"), x=pl.col("exit_time").dt.epoch("ms"))
    hours = sorted(set(lab["t"].to_list()))
    rng = np.random.default_rng(1)
    want = {h: str(rng.choice(["long", "short", "none"], p=[0.35, 0.35, 0.3])) for h in hours}
    want = {
        h: s for h, s in want.items() if h + 5 * HOUR <= int(candles["open_time"].max().timestamp() * 1000)
    }

    for h in sorted(want):
        now = START + timedelta(milliseconds=h - int(START.timestamp() * 1000) + 3 * 60_000)
        seen = candles.filter(pl.col("open_time") + timedelta(minutes=1) <= now)
        dec = {"decision_ms": h, "decision": want[h]} if want[h] != "none" else None
        paper.step(now, seen, fund, dec, "test", root=tmp_path)
    end = candles["open_time"].max() + timedelta(minutes=1)
    paper.step(end, candles, fund, None, "test", root=tmp_path)

    led = paper.ledger(tmp_path)
    closes = led.filter(pl.col("event") == "close")
    assert closes.height >= 3
    for r in closes.iter_rows(named=True):
        t = int(pl.Series([r["decision_time"]]).str.to_datetime(time_zone="UTC").dt.epoch("ms")[0])
        b = lab.filter((pl.col("t") == t) & (pl.col("side") == r["side"])).row(0, named=True)
        assert r["exit_reason"] == b["exit_reason"] and r["exit_price"] == b["exit_price"]
        assert abs(r["net_R"] - b["net_R"]) < 1e-12 and abs(r["funding_R"] - b["funding_R"]) < 1e-12
    wide = lab.pivot(on="side", index="t", values="x").sort("t")
    dt = wide["t"].to_numpy()
    sig_l = np.array([want.get(t) == "long" for t in dt])
    sig_s = np.array([want.get(t) == "short" for t in dt])
    idx, sd = simulate(dt, sig_l, sig_s, wide["long"].to_numpy(), wide["short"].to_numpy())
    taken = led.filter(pl.col("event") == "open").select(
        t=pl.col("decision_time").str.to_datetime(time_zone="UTC").dt.epoch("ms"), side="side"
    )
    assert taken["t"].to_list() == dt[idx].tolist()
    assert taken["side"].to_list() == ["long" if s == 1 else "short" for s in sd]
    assert led.filter(pl.col("event") == "skip").height > 0


def test_open_position_waits_for_data(tmp_path):
    candles = random_candles(30, seed=2)
    fund = funding_schedule(START, 30)
    h = int(START.timestamp() * 1000) + 20 * HOUR
    now = START + timedelta(hours=20, minutes=3)
    seen = candles.filter(pl.col("open_time") + timedelta(minutes=1) <= now)
    ev = paper.step(now, seen, fund, {"decision_ms": h, "decision": "long"}, "v", root=tmp_path)
    assert [e["event"] for e in ev] == ["open"]
    assert paper.step(now + timedelta(hours=1), seen, fund, None, "v", root=tmp_path) == []
    ev = paper.step(now + timedelta(hours=5), candles, fund.head(0), None, "v", root=tmp_path)
    midnight = START + timedelta(days=1)
    for e in ev:
        assert (
            e["event"] == "close"
            and pl.Series([e["exit_time"]]).str.to_datetime(time_zone="UTC")[0] <= midnight
        )
