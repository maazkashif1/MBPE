import json
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from mbpe.labels.limit_orders import run_book
from mbpe.labels.triple_barrier import load_config
from mbpe.live import paper_trading_b as pb

CFG = load_config(pb.LABEL_CONFIG)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = 3_600_000
ATR = 0.5


def candles(minutes=40 * 60):
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 0.08, minutes))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0, 0.1, minutes)
    low = np.minimum(open_, close) - rng.uniform(0, 0.1, minutes)
    return pl.DataFrame(
        {
            "open_time": [T0 + timedelta(minutes=m) for m in range(minutes)],
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


def funding():
    times = [T0 + timedelta(hours=h) for h in range(0, 72, 8)]
    return pl.DataFrame(
        {
            "calc_time": times,
            "last_funding_rate": [0.0001] * len(times),
            "funding_interval_hours": [8] * len(times),
        }
    ).with_columns(pl.col("calc_time").cast(pl.Datetime("ms", "UTC")))


def fake_atr(c, cfg):
    return pl.DataFrame(
        {"decision_time": [T0 + timedelta(hours=h) for h in range(48)], "atr": [ATR] * 48}
    ).with_columns(pl.col("decision_time").cast(pl.Datetime("ms", "UTC")))


def dec(hour, side="long"):
    return {"decision": side, "decision_ms": int((T0 + timedelta(hours=hour)).timestamp() * 1000)}


def upto(c, now):
    return c.filter(pl.col("open_time") + pl.duration(minutes=1) <= now)


def test_live_equals_book(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "decision_atr", fake_atr)
    c, f = candles(), funding()
    decisions = {1: "long", 2: "short", 3: "long", 4: "short", 6: "long", 7: "short"}
    events = []
    for h in range(0, 40):
        now = T0 + timedelta(hours=h, minutes=3)
        d = dec(h, decisions[h]) if h in decisions else None
        events += pb.step(now, upto(c, now), f, d, "v", tmp_path)
    events += pb.step(T0 + timedelta(hours=40), c, f, None, "v", tmp_path)
    live = [(e["event"], e["decision_ms"], e.get("net_R")) for e in events if e["event"] != "order"]
    book = run_book(
        [(dec(h)["decision_ms"], 1 if s == "long" else -1, ATR) for h, s in decisions.items()], c, f, CFG
    )
    ref = [
        (
            {"skip": "skip", "unfilled": "unfilled", "closed": "close"}[b["status"]],
            b["decision_ms"],
            b.get("net_R"),
        )
        for b in book
    ]
    assert sorted(live, key=lambda x: x[1]) == ref
    assert any(x[0] == "close" for x in ref) and any(x[0] == "skip" for x in ref)


def test_step_is_idempotent_for_same_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "decision_atr", fake_atr)
    c, f = candles(), funding()
    now = T0 + timedelta(hours=1, minutes=3)
    a = pb.step(now, upto(c, now), f, dec(1), "v", tmp_path)
    b = pb.step(now, upto(c, now), f, dec(1), "v", tmp_path)
    assert [e["event"] for e in a] == ["order"]
    assert b == []
    assert len(pb.ledger(tmp_path)) == 1


def test_no_decision_only_settles(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "decision_atr", fake_atr)
    c, f = candles(), funding()
    now = T0 + timedelta(hours=1, minutes=3)
    pb.step(now, upto(c, now), f, dec(1), "v", tmp_path)
    ev = pb.step(T0 + timedelta(hours=40), c, f, None, "v", tmp_path)
    assert [e["event"] for e in ev] in (["close"], ["unfilled"])
    assert json.loads((tmp_path / "state.json").read_text())["pending"] is None


def test_missing_atr_skips_without_order(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "decision_atr", lambda c, cfg: fake_atr(c, cfg).head(0))
    c, f = candles(), funding()
    now = T0 + timedelta(hours=1, minutes=3)
    ev = pb.step(now, upto(c, now), f, dec(1), "v", tmp_path)
    assert [(e["event"], e["reason"]) for e in ev] == [("skip", "entry candle or ATR missing")]
