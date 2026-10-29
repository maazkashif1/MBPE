import hashlib
import importlib
import json
import logging
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.features.news import load_calendar
from mbpe.live import frozen_model as lmod
from mbpe.live import paper_trading as paper
from mbpe.research.backtest import blackout
from mbpe.research.experiment import PHASE6_RUNG, rung_features
from mbpe.research.phase6_models import META_VOL, cfg_p6

log = logging.getLogger("mbpe.live")
LIVE = Path("data/live")
HOUR, MIN = 3_600_000, 60_000
STALE_MIN = {
    "futures_klines_1m": 5,
    "spot_klines_1m": 5,
    "mark_price_1m": 5,
    "premium_index_1m": 5,
    "metrics": 20,
    "funding": 9 * 60,
}
MAX_LATE_MIN = 15


def _append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def alert(kind: str, detail: str, root: Path = LIVE) -> None:
    log.warning("ALERT %s: %s", kind, detail)
    _append(root / "alerts.jsonl", {"at": datetime.now(UTC).isoformat(), "kind": kind, "detail": detail})


def logged_times(root: Path = LIVE) -> set[int]:
    p = root / "predictions.jsonl"
    return {json.loads(x)["decision_ms"] for x in p.read_text().splitlines()} if p.exists() else set()


def freshness(data: dict, t: int) -> dict[str, float]:
    from mbpe.features.market import TS_COL

    out = {}
    for ds, df in data.items():
        ts = df[TS_COL[ds]].dt.epoch("ms")
        before = ts.filter(ts < t)
        last = before.max() if before.len() else None
        close = (last + MIN) if last is not None and ds.endswith("_1m") else last
        out[ds] = (t - close) / MIN if close is not None else float("inf")
    return out


def save_features(row: pl.DataFrame, t: int, root: Path = LIVE) -> str:
    p = root / "features" / f"{datetime.fromtimestamp(t / 1000, UTC):%Y-%m}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    table = row
    if p.exists():
        old = pl.read_parquet(p).filter(pl.col("decision_time") != row["decision_time"][0])
        table = pl.concat([old, row], how="diagonal_relaxed")
    tmp = p.with_name(p.name + ".part")
    table.sort("decision_time").write_parquet(tmp)
    tmp.replace(p)
    x = row.select(rung_features(PHASE6_RUNG)).cast(pl.Float64).to_numpy()
    return hashlib.sha256(np.nan_to_num(x, nan=np.inf).tobytes()).hexdigest()


def _paper_b_step(*args):
    return importlib.import_module("mbpe.live.paper_trading_b").step(*args)


def run_hourly(now: datetime | None = None, model_dir: Path | None = None, root: Path = LIVE) -> dict:
    from mbpe.data import binance_rest as lm
    from mbpe.data import gdelt_live
    from mbpe.features.live import live_features
    from mbpe.live import news_update as ln

    now = now or datetime.now(UTC)
    t = int(now.timestamp() * 1000) // HOUR * HOUR
    rec = {
        "decision_ms": t,
        "decision_time": datetime.fromtimestamp(t / 1000, UTC).isoformat(),
        "run_at": now.isoformat(),
        "steps": {},
    }

    def step(name, fn):
        try:
            out = fn()
            rec["steps"][name] = "ok"
            return out
        except Exception as e:
            rec["steps"][name] = f"failed: {type(e).__name__}: {e}"[:300]
            alert("step_failed", f"{name}: {e}", root)
            log.debug(traceback.format_exc())
            return None

    step("market_top_up", lm.top_up)
    step("gdelt_collect", gdelt_live.collect)
    step("news_dedup", ln.update_dedup)
    step("news_sentiment", ln.update_scores)
    data = step("load_market", lm.load_live_data)
    fz = step("load_model", lambda: lmod.load(model_dir or lmod.latest()))
    decision = None
    if t in logged_times(root):
        rec["note"] = "decision for this hour already logged; settling paper positions only"
    elif (int(now.timestamp() * 1000) - t) / MIN > MAX_LATE_MIN:
        rec |= {"decision": "none", "reason": f"late run (> {MAX_LATE_MIN} min after t): no decision"}
        alert("late_run", f"run for {rec['decision_time']} started at {now.isoformat()}", root)
        _append(root / "predictions.jsonl", rec)
    elif data is not None and fz is not None:
        row = step("features", lambda: live_features(t, data))
        if row is not None:
            fresh = freshness(data, t)
            rec["staleness_min"] = fresh
            stale = [ds for ds, m in fresh.items() if m > STALE_MIN[ds]]
            names = rung_features(PHASE6_RUNG)
            rec["features_missing"] = [n for n in names if row[n][0] is None]
            rec["features_sha256"] = save_features(row, t, root)
            for ds in stale:
                alert("stale_source", f"{ds}: last data {fresh[ds]:.0f} min before t", root)
            events = load_calendar(events=("CPI", "FOMC", "NFP"))
            c = cfg_p6()
            eligible = not bool(
                blackout(
                    np.array([t]),
                    events,
                    c.blackout_before_minutes,
                    c.blackout_horizon_minutes,
                    c.latency_minutes,
                )[0]
            )
            missing_vol = [v for v in META_VOL if row[v][0] is None]
            if stale or missing_vol:
                decision = {"decision": "none", "reason": f"stale or missing inputs: {stale + missing_vol}"}
            else:
                decision = lmod.predict(fz, row, eligible)
            decision["decision_ms"] = t
            rec |= {"model_version": fz.version, "eligible": eligible, **decision}
        _append(root / "predictions.jsonl", rec)
    if data is not None and fz is not None:
        candles, funding = data["futures_klines_1m"], data["funding"]
        step("paper", lambda: paper.step(now, candles, funding, decision, fz.version, root / "paper"))
        step("paper_b", lambda: _paper_b_step(now, candles, funding, decision, fz.version, root / "paper_b"))
    if now.hour == 0 and now.minute < 30:
        step("daily_summary", lambda: daily_summary(now, root))
    return rec


def daily_summary(now: datetime, root: Path = LIVE) -> Path:
    day = (now - timedelta(days=1)).date().isoformat()
    preds = (
        pl.read_ndjson(root / "predictions.jsonl")
        if (root / "predictions.jsonl").exists()
        else pl.DataFrame()
    )
    led = paper.ledger(root / "paper")
    alerts = pl.read_ndjson(root / "alerts.jsonl") if (root / "alerts.jsonl").exists() else pl.DataFrame()

    def on_day(df, col):
        return df.filter(pl.col(col).str.starts_with(day)) if df.height and col in df.columns else df.head(0)

    closes = led.filter(pl.col("event") == "close") if led.height else led
    out = {
        "day": day,
        "predictions": on_day(preds, "decision_time").height,
        "decisions": on_day(preds, "decision_time").filter(pl.col("decision").is_in(["long", "short"])).height
        if preds.height and "decision" in preds.columns
        else 0,
        "alerts": on_day(alerts, "at").height,
        "trades_closed_total": closes.height,
        "net_R_total_taker": float(closes["net_R"].sum()) if closes.height else 0.0,
        "net_R_mean_taker": float(closes["net_R"].mean()) if closes.height else None,
    }
    p = root / "daily" / f"{day}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    return p


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    md = Path(sys.argv[sys.argv.index("--model") + 1]) if "--model" in sys.argv else None
    print(json.dumps(run_hourly(model_dir=md), indent=1, default=str))
