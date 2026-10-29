"""Paper executor (Phase 6 Workstream D): internal simulator on live data. No orders are ever placed.

Every trade is settled by the backtest's own label code (mbpe.labels.triple_barrier, label set A):
- entry at the open of the 1m candle starting at t + 1 min; +2 / -1 ATR(14, 1h) barriers, stop checked
  first, gap fills at the open; 4 h horizon; funding for every settlement in (entry, exit]; taker fees and
  slippage (maker_entry_tp reported alongside)
- while a position is open, each run labels it on the candles available so far: a barrier hit inside them is
  final (later candles can't change the first hit); with the full 4 h available the trade expires
- one position at a time: a new trade opens only at a decision time at or after the last exit (the backtest's
  position rule)

State: data/live/paper/state.json (open position, last exit).
Ledger: data/live/paper/ledger.jsonl, append-only; events open, close, skip.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from mbpe.labels.triple_barrier import (
    MissingFundingError,
    decision_atr,
    label_trades,
    load_config,
    scenario_net_r,
)

PAPER = Path("data/live/paper")
LABEL_CONFIG = Path("config/labels.yaml")  # label set A, the frozen model's labels
MIN = 60_000
R_FIELDS = ("gross_R", "fee_R", "slippage_R", "funding_R", "net_R", "net_R_maker_entry_tp")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def settle(pos: dict, candles: pl.DataFrame, funding: pl.DataFrame, cfg) -> dict | None:
    """Label the open position on the candles available; None while it is still open (or data is missing)."""
    entry = pos["entry_ms"]
    t = candles.filter(pl.col("open_time").dt.epoch("ms") >= entry).sort("open_time")["open_time"]
    t = t.dt.epoch("ms").to_numpy()
    k = 0
    while (
        k < len(t) and k < cfg.horizon_minutes and t[k] == entry + k * MIN
    ):  # consecutive minutes from entry
        k += 1
    if k == 0:
        return None
    dec = pl.DataFrame({"decision_time": [pos["decision_ms"]], "atr": [pos["atr"]]}).with_columns(
        pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
    )
    cfg_k = cfg.model_copy(update={"horizon_minutes": k})
    try:
        lab, _ = label_trades(candles, funding, dec, cfg_k)
    except MissingFundingError:
        return None  # a settlement inside the trade isn't published yet: retry next run
    row = lab.filter(pl.col("side") == pos["side"])
    if not row.height:
        return None
    r = row.row(0, named=True)
    if r["exit_reason"] == "expiry" and k < cfg.horizon_minutes:
        return None  # no barrier hit yet and the horizon isn't over
    return {**r, "net_R_maker_entry_tp": scenario_net_r(row, cfg, "maker_entry_tp")[0]}


def _close(pos: dict, res: dict) -> dict:
    exit_ms = int(res["exit_time"].timestamp() * 1000)
    return {
        "event": "close",
        **{k: pos[k] for k in ("trade_id", "side", "decision_time", "entry_time", "entry_price")},
        "exit_ms": exit_ms,
        "exit_time": _iso(exit_ms),
        "exit_price": float(res["exit_price"]),
        "exit_reason": res["exit_reason"],
        **{k: float(res[k]) for k in R_FIELDS},
    }


def _open(decision: dict, candles: pl.DataFrame, cfg, model_version: str) -> dict:
    """An open position from this hour's decision, or a skip event if the entry bar or ATR is missing."""
    t = decision["decision_ms"]
    entry_ms = t + cfg.latency_minutes * MIN
    bar = candles.filter(pl.col("open_time").dt.epoch("ms") == entry_ms)
    atr = decision_atr(candles.filter(pl.col("open_time").dt.epoch("ms") < t), cfg)
    atr = atr.filter(pl.col("decision_time").dt.epoch("ms") == t)["atr"]
    if not bar.height or not atr.len() or atr[0] is None:
        return {"event": "skip", "decision_time": _iso(t), "reason": "entry candle or ATR missing"}
    side, price, a = decision["decision"], float(bar["open"][0]), float(atr[0])
    sgn = 1 if side == "long" else -1
    return {
        "event": "open",
        "trade_id": f"{t}-{side}",
        "side": side,
        "decision_ms": t,
        "decision_time": _iso(t),
        "entry_ms": entry_ms,
        "entry_time": _iso(entry_ms),
        "entry_price": price,
        "atr": a,
        "tp": price + sgn * cfg.tp_atr * a,
        "sl": price - sgn * cfg.sl_atr * a,
        "meta_p": decision.get("meta_p"),
        "model_version": model_version,
    }


def step(
    now: datetime,
    candles: pl.DataFrame,
    funding: pl.DataFrame,
    decision: dict | None,
    model_version: str,
    root: Path = PAPER,
) -> list[dict]:
    """Settle the open position if its exit is known, then act on this hour's decision. Returns new events."""
    cfg = load_config(LABEL_CONFIG)
    state_p, ledger_p = root / "state.json", root / "ledger.jsonl"
    state = json.loads(state_p.read_text()) if state_p.exists() else {"open": None, "last_exit_ms": 0}
    events = []
    if state["open"] and (res := settle(state["open"], candles, funding, cfg)) is not None:
        events.append(_close(state["open"], res))
        state["open"], state["last_exit_ms"] = None, events[-1]["exit_ms"]
    if decision and decision.get("decision") in ("long", "short"):
        t = decision["decision_ms"]
        if state["open"] is not None or t < state["last_exit_ms"]:
            events.append(
                {"event": "skip", "decision_time": _iso(t), "reason": "position open (one at a time)"}
            )
        else:
            ev = _open(decision, candles, cfg, model_version)
            events.append(ev)
            if ev["event"] == "open":
                state["open"] = ev
                if (res := settle(ev, candles, funding, cfg)) is not None:  # data already past the exit
                    events.append(_close(ev, res))
                    state["open"], state["last_exit_ms"] = None, events[-1]["exit_ms"]
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


def ledger(root: Path = PAPER) -> pl.DataFrame:
    p = root / "ledger.jsonl"
    return pl.read_ndjson(p) if p.exists() and p.stat().st_size else pl.DataFrame()
