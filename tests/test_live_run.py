import json
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from mbpe.features.market import TS_COL
from mbpe.live import hourly_run as lr
from mbpe.live import scheduler as every
from mbpe.research.experiment import PHASE6_RUNG, rung_features

NOW = datetime(2026, 11, 3, 10, 3, tzinfo=UTC)
T = int(datetime(2026, 11, 3, 10, tzinfo=UTC).timestamp() * 1000)


def fake_data(lag_min: int = 0) -> dict:
    out = {}
    for ds, ts in TS_COL.items():
        last = T - 60_000 - lag_min * 60_000 if ds.endswith("_1m") else T - 5 * 60_000 - lag_min * 60_000
        out[ds] = pl.DataFrame({ts: [last - 60_000, last]}).with_columns(
            pl.col(ts).cast(pl.Datetime("ms", "UTC"))
        )
    return out


def fake_row() -> pl.DataFrame:
    names = rung_features(PHASE6_RUNG)
    return pl.DataFrame({"decision_time": [T], **{n: [0.1] for n in names}}).with_columns(
        pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
    )


@pytest.fixture
def faked(monkeypatch, tmp_path):
    from mbpe.data import binance_rest as market
    from mbpe.data import btcusdc, gdelt_live
    from mbpe.features import live as features
    from mbpe.live import news_update as news
    from mbpe.live import paper_trading as paper

    calls = {"predict": 0, "paper": 0}
    monkeypatch.setattr(market, "top_up", lambda: {})
    monkeypatch.setattr(gdelt_live, "collect", lambda: (_ for _ in ()).throw(TimeoutError("GDELT down")))
    monkeypatch.setattr(news, "update_dedup", lambda: 0)
    monkeypatch.setattr(news, "update_scores", lambda: None)
    state = {"data": fake_data()}
    monkeypatch.setattr(market, "load_live_data", lambda: state["data"])
    monkeypatch.setattr(btcusdc, "update", lambda: {})
    monkeypatch.setattr(btcusdc, "load", lambda: {k: state["data"][k] for k in btcusdc.DATASETS})
    monkeypatch.setattr(features, "live_features", lambda t, data: fake_row())

    class FZ:
        version = "v-test"

    monkeypatch.setattr(lr.lmod, "latest", lambda: tmp_path)
    monkeypatch.setattr(lr.lmod, "load", lambda p: FZ())

    def predict(fz, row, eligible):
        calls["predict"] += 1
        return {"decision": "long", "meta_p": 0.7, "reason": "meta P(success) >= threshold"}

    monkeypatch.setattr(lr.lmod, "predict", predict)

    def pstep(now, candles, funding, decision, version, root, atr_candles=None):
        calls["paper"] += 1
        return []

    monkeypatch.setattr(paper, "step", pstep)
    return tmp_path, calls, state


def test_failed_source_is_alerted_and_the_run_continues(faked):
    root, calls, _ = faked
    rec = lr.run_hourly(NOW, root=root)
    assert rec["steps"]["gdelt_collect"].startswith("failed: TimeoutError")
    assert rec["decision"] == "long" and calls == {"predict": 1, "paper": 1}
    alerts = [json.loads(x) for x in (root / "alerts.jsonl").read_text().splitlines()]
    assert alerts[0]["kind"] == "step_failed" and "gdelt_collect" in alerts[0]["detail"]
    assert (root / "features" / "2026-11.parquet").exists() and len(rec["features_sha256"]) == 64


def test_test_b_failure_never_touches_test_a(faked, monkeypatch):
    from mbpe.live import paper_trading_b as paper_b

    root, calls, _ = faked

    def boom(*a, **k):
        raise RuntimeError("test b down")

    monkeypatch.setattr(paper_b, "step", boom)
    rec = lr.run_hourly(NOW, root=root)
    assert rec["steps"]["paper"] == "ok" and rec["steps"]["paper_b"].startswith("failed: RuntimeError")
    assert rec["decision"] == "long" and calls["paper"] == 1


def test_same_hour_is_logged_once(faked):
    root, calls, _ = faked
    lr.run_hourly(NOW, root=root)
    rec = lr.run_hourly(NOW + timedelta(minutes=20), root=root)
    assert "already logged" in rec["note"] and calls["predict"] == 1 and calls["paper"] == 2
    assert len((root / "predictions.jsonl").read_text().splitlines()) == 1


def test_stale_market_data_means_no_decision(faked):
    root, calls, state = faked
    state["data"] = fake_data(lag_min=30)
    rec = lr.run_hourly(NOW, root=root)
    assert rec["decision"] == "none" and rec["reason"].startswith("stale") and calls["predict"] == 0
    kinds = {json.loads(x)["kind"] for x in (root / "alerts.jsonl").read_text().splitlines()}
    assert "stale_source" in kinds


def test_freshness_minutes():
    f = lr.freshness(fake_data(lag_min=0), T)
    assert f["futures_klines_1m"] == 0 and f["metrics"] == 5
    assert np.isinf(lr.freshness({"funding": fake_data()["funding"].head(0)}, T)["funding"])


@pytest.mark.parametrize(
    "now,minute,interval,expected",
    [
        (datetime(2026, 10, 29, 12, 5, 30, tzinfo=UTC), 3, None, datetime(2026, 10, 29, 13, 3, tzinfo=UTC)),
        (datetime(2026, 10, 29, 12, 2, tzinfo=UTC), 3, None, datetime(2026, 10, 29, 12, 3, tzinfo=UTC)),
        (datetime(2026, 10, 29, 12, 5, 30, tzinfo=UTC), None, 15, datetime(2026, 10, 29, 12, 15, tzinfo=UTC)),
        (datetime(2026, 10, 29, 12, 5, tzinfo=UTC), None, 1440, datetime(2026, 10, 30, 0, 0, tzinfo=UTC)),
    ],
)
def test_scheduler_slots(now, minute, interval, expected):
    assert every.next_slot(now, minute, interval) == expected


def test_late_run_makes_no_decision(faked):
    root, calls, _ = faked
    rec = lr.run_hourly(NOW + timedelta(minutes=40), root=root)
    assert rec["decision"] == "none" and rec["reason"].startswith("late run") and calls["predict"] == 0
    assert calls["paper"] == 1
