import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

FORWARD_START = datetime(2026, 10, 29, 13, tzinfo=UTC)
VERDICT_EARLIEST = datetime(2027, 4, 29, tzinfo=UTC)
TRADES_TARGET = 100
RISK_PER_TRADE = 0.01
HOUR = timedelta(hours=1)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def trade_stats(closes: list[dict]) -> dict:
    r = [c["net_R"] for c in closes]
    wins, losses = [x for x in r if x > 0], [x for x in r if x <= 0]
    equity, peak, drawdown, points = 0.0, 0.0, 0.0, []
    for c in closes:
        equity += c["net_R"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        points.append({"t": c["exit_time"], "equity_R": equity, "net_R": c["net_R"]})
    return {
        "closed": len(r),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(r) if r else None,
        "net_R": sum(r),
        "mean_R": sum(r) / len(r) if r else None,
        "won_R": sum(wins),
        "lost_R": sum(losses),
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "max_drawdown_R": drawdown,
        "return_pct": sum(r) * RISK_PER_TRADE * 100,
        "net_R_maker": sum(c.get("net_R_maker_entry_tp", 0.0) for c in closes),
        "best_R": max(r) if r else None,
        "worst_R": min(r) if r else None,
        "equity": points,
    }


def open_position(state: dict, last_price: float | None) -> dict | None:
    pos = state.get("open")
    if not pos:
        return None
    sgn = 1 if pos["side"] == "long" else -1
    unit = abs(pos["entry_price"] - pos["sl"])
    out = {
        k: pos[k] for k in ("side", "decision_time", "entry_time", "entry_price", "tp", "sl", "atr", "meta_p")
    }
    out["horizon_end"] = (parse_time(pos["entry_time"]) + timedelta(hours=4)).isoformat()
    out["last_price"] = last_price
    out["unrealized_gross_R"] = (
        sgn * (last_price - pos["entry_price"]) / unit if last_price and unit else None
    )
    return out


def decision_stats(preds: list[dict], now: datetime) -> dict:
    last_hour = now.replace(minute=0, second=0, microsecond=0)
    if now.minute < 4:
        last_hour -= HOUR
    expected = max(0, int((last_hour - FORWARD_START) / HOUR) + 1)
    logged = {p["decision_ms"] for p in preds}
    counts = {"trade": 0, "no_side": 0, "meta_below": 0, "blackout": 0, "stale": 0, "late": 0}
    for p in preds:
        reason = p.get("reason", "")
        if p.get("decision") in ("long", "short"):
            counts["trade"] += 1
        elif reason.startswith("no side"):
            counts["no_side"] += 1
        elif reason.startswith("meta"):
            counts["meta_below"] += 1
        elif reason.startswith("blackout"):
            counts["blackout"] += 1
        elif reason.startswith("late"):
            counts["late"] += 1
        else:
            counts["stale"] += 1
    hours = []
    for k in range(min(expected, 48)):
        t = last_hour - k * HOUR
        p = next((x for x in preds if x["decision_ms"] == int(t.timestamp() * 1000)), None)
        hours.append(
            {
                "t": t.isoformat(),
                "status": "missed" if p is None else p.get("decision", "none"),
                "reason": "no run recorded" if p is None else p.get("reason", ""),
                "meta_p": None if p is None else p.get("meta_p"),
                "expected_R": None if p is None else p.get("expected_R"),
            }
        )
    return {
        "hours_expected": expected,
        "hours_logged": len(logged),
        "hours_missed": max(0, expected - len(logged)),
        "outcomes": counts,
        "recent": hours,
    }


def test_b(live: Path) -> dict:
    led = read_jsonl(live / "paper_b" / "ledger.jsonl")
    closes = sorted((e for e in led if e["event"] == "close"), key=lambda e: e["exit_time"])
    state_p = live / "paper_b" / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    stats = trade_stats(closes)
    stats.pop("equity")
    return {
        "running": state_p.exists(),
        "orders": sum(e["event"] == "order" for e in led),
        "unfilled": sum(e["event"] == "unfilled" for e in led),
        "pending": state.get("pending"),
        "trades": stats,
        "recent": list(reversed(closes[-5:])),
    }


def collectors(raw: Path, now: datetime) -> dict:
    rss = sorted(raw.glob("rss_live/*/*.json"))
    rss_last = json.loads(rss[-1].read_text()) if rss else None
    cov = read_jsonl(raw / "liquidations" / "coverage.jsonl")
    today = raw / "liquidations" / f"{now:%Y-%m-%d}.jsonl"
    return {
        "rss": {
            "last_run": rss_last["run_at"] if rss_last else None,
            "feeds_ok": sum(f.get("ok", False) for f in rss_last["feeds"].values()) if rss_last else 0,
            "feeds": len(rss_last["feeds"]) if rss_last else 0,
        },
        "liquidations": {
            "last_seen": cov[-1]["t"] if cov else None,
            "connected_since": next((c["t"] for c in reversed(cov) if c["kind"] == "start"), None),
            "events_today": len(read_jsonl(today)),
        },
    }


def last_price(live: Path) -> tuple[float | None, str | None]:
    p = live / "binance" / "futures_klines_1m.parquet"
    if not p.exists():
        return None, None
    df = pl.read_parquet(p, columns=["open_time", "close"]).sort("open_time").tail(1)
    if not df.height:
        return None, None
    return float(df["close"][0]), (df["open_time"][0] + timedelta(minutes=1)).isoformat()


def build(root: Path = Path("data"), now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    live = root / "live"
    preds = read_jsonl(live / "predictions.jsonl")
    ledger = read_jsonl(live / "paper" / "ledger.jsonl")
    state_p = live / "paper" / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {"open": None}
    closes = sorted((e for e in ledger if e["event"] == "close"), key=lambda e: e["exit_time"])
    price, price_time = last_price(live)
    alerts = [
        a for a in read_jsonl(live / "alerts.jsonl") if parse_time(a["at"]) >= now - timedelta(hours=24)
    ]
    parity = sorted((live / "parity").glob("*.json"))
    last_pred = preds[-1] if preds else None
    model = next((p for p in reversed(preds) if p.get("model_version")), {})
    next_run = now.replace(minute=3, second=0, microsecond=0)
    if next_run <= now:
        next_run += HOUR
    stats = trade_stats(closes)
    return {
        "generated_at": now.isoformat(),
        "model": {
            "version": model.get("model_version"),
            "threshold": model.get("threshold"),
        },
        "forward_test": {
            "start": FORWARD_START.isoformat(),
            "verdict_earliest": VERDICT_EARLIEST.isoformat(),
            "days_elapsed": (now - FORWARD_START) / timedelta(days=1),
            "days_total": (VERDICT_EARLIEST - FORWARD_START) / timedelta(days=1),
            "trades_closed": stats["closed"],
            "trades_target": TRADES_TARGET,
        },
        "trades": stats,
        "recent_trades": list(reversed(closes[-20:])),
        "open_position": open_position(state, price),
        "market": {"last_price": price, "price_time": price_time},
        "decisions": decision_stats(preds, now),
        "last_run": {
            "decision_time": last_pred.get("decision_time") if last_pred else None,
            "run_at": last_pred.get("run_at") if last_pred else None,
            "steps": last_pred.get("steps", {}) if last_pred else {},
            "staleness_min": last_pred.get("staleness_min", {}) if last_pred else {},
        },
        "next_run": next_run.isoformat(),
        "collectors": collectors(root / "raw", now),
        "alerts_24h": alerts[-20:],
        "parity": json.loads(parity[-1].read_text()) if parity else None,
        "test_b": test_b(live),
    }
