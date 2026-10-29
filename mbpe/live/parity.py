"""Live/archive parity check (Phase 6 Workstream D; tolerances in SCOPE.md, Workstream D definitions).

For every logged decision time t the archive now covers (every market dataset's archive reaches t):
- market features (market + derivatives + mark/premium groups) recomputed from the archive clean layer only;
  news / calendar / regulatory features recomputed from all news rows known now (live dedup store), which
  includes GDELT batches that arrived after t
- each feature: equal within TOL (relative to max(1, |archive|)) or both missing
- the decision re-made by the same frozen model version on the archive features: same or not
- every closed paper trade: equal to the label set A label computed from archive candles and funding
Results: data/live/parity/<run date>.json (summary) and .parquet (per hour and feature).

Usage: uv run python -m mbpe.live.parity
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.features.dedup import load_news_config
from mbpe.features.market import DATASETS, REGISTRY, build_features, load_data, load_feature_config
from mbpe.features.news import NEWS_REGISTRY, build_news_features, load_inputs
from mbpe.labels.triple_barrier import label_trades, load_config
from mbpe.live import model as lmod
from mbpe.live import paper
from mbpe.live.market import archive_end
from mbpe.live.news import LIVE_DEDUP
from mbpe.live.run import LIVE

TOL = 1e-9


def _equal(a: pl.Series, b: pl.Series) -> np.ndarray:
    x, y = a.cast(pl.Float64).to_numpy(), b.cast(pl.Float64).to_numpy()
    both_missing = np.isnan(x) & np.isnan(y)
    close = np.abs(x - y) <= TOL * np.maximum(1.0, np.abs(y))
    return both_missing | np.nan_to_num(close, nan=False).astype(bool)


def check(root: Path = LIVE, model_root: Path = lmod.MODEL_ROOT) -> dict:
    files = sorted((root / "features").glob("*.parquet"))
    if not files:
        return {"hours": 0, "note": "no live feature rows yet"}
    live = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    covered = min(archive_end(ds) for ds in DATASETS)
    live = live.filter(pl.col("decision_time").dt.epoch("ms") <= covered)
    if not live.height:
        return {"hours": 0, "note": "the archive doesn't cover any logged hour yet"}
    at = live["decision_time"].dt.epoch("ms").to_numpy()
    market = build_features(load_data(), load_feature_config(), at=at)
    cfg = load_news_config()
    inp = load_inputs(cfg, require_scores=False, dedup_path=LIVE_DEDUP)
    news = build_news_features(inp.prep, inp.coverage, inp.events, cfg, at=at)
    arch = market.join(news, on="decision_time").sort("decision_time")
    live = live.sort("decision_time")
    groups = {n: f.group for n, f in {**REGISTRY, **NEWS_REGISTRY}.items()}
    rows = []
    for n in groups:
        if n in live.columns and n in arch.columns:
            eq = _equal(live[n], arch[n])
            diff = np.abs(live[n].cast(pl.Float64).to_numpy() - arch[n].cast(pl.Float64).to_numpy())
            worst = float(np.nanmax(diff)) if np.isfinite(diff).any() else 0.0
            rows.append(
                {
                    "feature": n,
                    "group": groups[n],
                    "hours": len(eq),
                    "equal": int(eq.sum()),
                    "max_abs_diff": worst,
                }
            )
    feats = pl.DataFrame(rows)
    # decisions re-made on the archive features by the logged model version
    preds = pl.read_ndjson(root / "predictions.jsonl").filter(pl.col("decision").is_not_null())
    agree, compared = 0, 0
    for r in preds.filter(pl.col("decision_ms").is_in(at.tolist())).iter_rows(named=True):
        fz = lmod.load(model_root / r["model_version"])
        row = arch.filter(pl.col("decision_time").dt.epoch("ms") == r["decision_ms"])
        if not r.get("reason", "").startswith("stale"):
            again = lmod.predict(fz, row, bool(r["eligible"]))
            agree += again["decision"] == r["decision"]
            compared += 1
    # closed paper trades vs labels from archive data
    led = paper.ledger(root / "paper")
    closes = led.filter(pl.col("event") == "close") if led.height else led
    trade_ok, trade_n = 0, 0
    if closes.height:
        data = load_data()
        lcfg = load_config(paper.LABEL_CONFIG)
        for c in closes.iter_rows(named=True):
            t = int(pl.Series([c["decision_time"]]).str.to_datetime(time_zone="UTC").dt.epoch("ms")[0])
            if t + lcfg.horizon_minutes * 60_000 > archive_end("futures_klines_1m"):
                continue
            opened = led.filter((pl.col("event") == "open") & (pl.col("trade_id") == c["trade_id"])).row(
                0, named=True
            )
            dec = pl.DataFrame({"decision_time": [t], "atr": [opened["atr"]]}).with_columns(
                pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
            )
            lab, _ = label_trades(data["futures_klines_1m"], data["funding"], dec, lcfg)
            b = lab.filter(pl.col("side") == c["side"])
            trade_n += 1
            trade_ok += bool(
                b.height
                and b["exit_reason"][0] == c["exit_reason"]
                and abs(b["net_R"][0] - c["net_R"]) < 1e-12
            )
    summary = {
        "checked_at": datetime.now(UTC).isoformat(),
        "hours": live.height,
        "first": str(live["decision_time"].min()),
        "last": str(live["decision_time"].max()),
        "features_within_tolerance": {
            g: f"{int(d['equal'].sum())}/{int(d['hours'].sum())}"
            for (g,), d in feats.group_by("group", maintain_order=True)
        },
        "features_not_all_equal": feats.filter(pl.col("equal") < pl.col("hours"))["feature"].to_list(),
        "decisions_agree": f"{agree}/{compared}",
        "paper_trades_match_archive_labels": f"{trade_ok}/{trade_n}",
    }
    out = root / "parity" / f"{datetime.now(UTC):%Y-%m-%d}"
    out.parent.mkdir(parents=True, exist_ok=True)
    feats.write_parquet(out.with_suffix(".parquet"))
    out.with_suffix(".json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    print(json.dumps(check(), indent=1))
