import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from mbpe.labels.limit_orders import limit_order_trade
from mbpe.labels.triple_barrier import decision_atr, load_config
from mbpe.live.paper_trading import fees

PAPER_B = Path("data/live/paper_b")
LABEL_CONFIG = Path("config/labels_usdc.yaml")
MIN = 60_000
SEEN = 48


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _atr(t: int, candles: pl.DataFrame, cfg) -> float | None:
    a = decision_atr(candles.filter(pl.col("open_time").dt.epoch("ms") < t), cfg)
    a = a.filter(pl.col("decision_time").dt.epoch("ms") == t)["atr"]
    if not a.len() or a[0] is None or not a[0] > 0:
        return None
    return float(a[0])


def _settle(pending: dict, candles: pl.DataFrame, funding: pl.DataFrame, cfg) -> dict | None:
    r = limit_order_trade(pending["decision_ms"], pending["side"], pending["atr"], candles, funding, cfg)
    if r is None:
        return None
    t = pending["decision_ms"]
    if r["status"] == "unfilled":
        return {
            "event": "unfilled",
            "decision_ms": t,
            "decision_time": _iso(t),
            "side": r["side"],
            "limit_price": float(r["limit_price"]),
            "free_ms": r["free_ms"],
        }
    return {
        "event": "close",
        "decision_ms": t,
        "decision_time": _iso(t),
        "side": r["side"],
        "limit_price": float(r["limit_price"]),
        "fill_time": _iso(r["fill_ms"]),
        "exit_ms": r["exit_ms"],
        "exit_time": _iso(r["exit_ms"]),
        "exit_price": r["exit_price"],
        "exit_reason": r["exit_reason"],
        **{k: r[k] for k in ("gross_R", "fee_R", "slippage_R", "funding_R", "net_R")},
        "free_ms": r["free_ms"],
    }


def step(
    now: datetime,
    candles: pl.DataFrame,
    funding: pl.DataFrame,
    decision: dict | None,
    model_version: str,
    root: Path = PAPER_B,
    atr_candles: pl.DataFrame | None = None,
) -> list[dict]:
    cfg = load_config(LABEL_CONFIG)
    state_p, ledger_p = root / "state.json", root / "ledger.jsonl"
    state = (
        json.loads(state_p.read_text()) if state_p.exists() else {"pending": None, "free_ms": 0, "seen": []}
    )
    events = []

    def settle() -> None:
        if state["pending"] and (ev := _settle(state["pending"], candles, funding, cfg)) is not None:
            events.append(ev)
            state["pending"], state["free_ms"] = None, ev["free_ms"]

    settle()
    if (
        decision
        and decision.get("decision") in ("long", "short")
        and decision["decision_ms"] not in state["seen"]
    ):
        t = decision["decision_ms"]
        state["seen"] = (state["seen"] + [t])[-SEEN:]
        skip = {"event": "skip", "decision_ms": t, "decision_time": _iso(t)}
        if state["pending"] is not None or t < state["free_ms"]:
            events.append({**skip, "reason": "busy"})
        else:
            atr = _atr(t, candles if atr_candles is None else atr_candles, cfg)
            entry = candles.filter(pl.col("open_time").dt.epoch("ms") == t + cfg.latency_minutes * MIN)
            if atr is None or not entry.height:
                events.append({**skip, "reason": "entry candle or ATR missing"})
            else:
                side = 1 if decision["decision"] == "long" else -1
                state["pending"] = {"decision_ms": t, "side": side, "atr": atr}
                events.append(
                    {
                        "event": "order",
                        "decision_ms": t,
                        "decision_time": _iso(t),
                        "side": side,
                        "atr": atr,
                        "meta_p": decision.get("meta_p"),
                        "fees": fees(cfg),
                    }
                )
                settle()
    root.mkdir(parents=True, exist_ok=True)
    if events:
        with ledger_p.open("a") as f:
            for e in events:
                rec = {k: v for k, v in e.items() if not k.endswith("_ms")}
                f.write(
                    json.dumps({"logged_at": now.isoformat(), "model_version": model_version, **rec}) + "\n"
                )
    tmp = state_p.with_name("state.json.part")
    tmp.write_text(json.dumps(state))
    tmp.replace(state_p)
    return events


def ledger(root: Path = PAPER_B) -> pl.DataFrame:
    p = root / "ledger.jsonl"
    return pl.read_ndjson(p) if p.exists() and p.stat().st_size else pl.DataFrame()
