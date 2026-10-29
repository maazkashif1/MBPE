import numpy as np
import polars as pl

from mbpe.labels.triple_barrier import LabelConfig, MissingFundingError, check_funding_coverage

FILL_MINUTES = 15
MINUTE_MS = 60_000


def limit_order_trade(
    decision_ms: int, side: int, atr: float, candles: pl.DataFrame, funding: pl.DataFrame, cfg: LabelConfig
) -> dict | None:
    n = cfg.horizon_minutes
    t0 = decision_ms + cfg.latency_minutes * MINUTE_MS
    t_end = t0 + (n - 1) * MINUTE_MS
    c = candles.filter(
        (pl.col("open_time").dt.epoch("ms") >= t0) & (pl.col("open_time").dt.epoch("ms") <= t_end)
    ).sort("open_time")
    pos = (c["open_time"].dt.epoch("ms").to_numpy() - t0) // MINUTE_MS
    present = np.zeros(n, dtype=bool)
    present[pos] = True
    o, h, lo, cl = (np.full(n, np.nan) for _ in range(4))
    for arr, name in ((o, "open"), (h, "high"), (lo, "low"), (cl, "close")):
        arr[pos] = c[name].to_numpy()
    if not present[0]:
        return None

    limit = o[0]
    tp = limit + side * cfg.tp_atr * atr
    sl = limit - side * cfg.sl_atr * atr
    base = {"decision_ms": decision_ms, "side": side, "limit_price": limit, "tp": tp, "sl": sl}
    adverse, favourable = (lo, h) if side == 1 else (h, lo)

    fill = None
    for k in range(FILL_MINUTES):
        if not present[k]:
            return None
        if side * (adverse[k] - limit) < 0:
            fill = k
            break
    if fill is None:
        return {"status": "unfilled", **base, "free_ms": decision_ms + (1 + FILL_MINUTES) * MINUTE_MS}

    reason, exit_k, gapped = "expiry", n - 1, False
    for k in range(fill, n):
        if not present[k]:
            return None
        if side * (adverse[k] - sl) <= 0:
            reason, exit_k = "sl", k
            gapped = k > fill and side * (o[k] - sl) <= 0
            break
        if k > fill and side * (favourable[k] - tp) > 0:
            reason, exit_k = "tp", k
            break
    exit_price = {"sl": o[exit_k] if gapped else sl, "tp": tp, "expiry": cl[exit_k]}[reason]
    fill_ms = t0 + fill * MINUTE_MS
    exit_ms = t0 + exit_k * MINUTE_MS + (0 if gapped else MINUTE_MS)

    f = funding.sort("calc_time")
    f_t = f["calc_time"].dt.round("1m").to_numpy()
    entry_t = np.array([fill_ms], dtype="datetime64[ms]")
    exit_t = np.array([exit_ms], dtype="datetime64[ms]")
    try:
        check_funding_coverage(f_t, f["funding_interval_hours"].to_numpy(), entry_t, exit_t, cfg)
    except MissingFundingError:
        return None
    cum = np.concatenate([[0.0], np.cumsum(f["last_funding_rate"].to_numpy())])
    rate = cum[np.searchsorted(f_t, exit_t, "right")[0]] - cum[np.searchsorted(f_t, entry_t, "right")[0]]

    unit = abs(limit - sl)
    is_tp = reason == "tp"
    gross = side * (exit_price - limit) / unit
    fee = (cfg.maker_fee * limit + (cfg.maker_fee if is_tp else cfg.taker_fee) * exit_price) / unit
    slip = 0.0 if is_tp else cfg.slippage_bps / 1e4 * exit_price / unit
    funding_r = -side * rate * limit / unit
    return {
        "status": "closed",
        **base,
        "fill_ms": fill_ms,
        "exit_ms": exit_ms,
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "gross_R": float(gross),
        "fee_R": float(fee),
        "slippage_R": float(slip),
        "funding_R": float(funding_r),
        "net_R": float(gross - fee - slip + funding_r),
        "free_ms": exit_ms,
    }


def run_book(
    decisions: list[tuple[int, int, float]], candles: pl.DataFrame, funding: pl.DataFrame, cfg: LabelConfig
) -> list[dict]:
    out, free_ms = [], None
    for decision_ms, side, atr in decisions:
        if free_ms is not None and decision_ms < free_ms:
            out.append({"status": "skip", "decision_ms": decision_ms, "reason": "busy"})
            continue
        r = limit_order_trade(decision_ms, side, atr, candles, funding, cfg)
        if r is None:
            out.append({"status": "missing_candles", "decision_ms": decision_ms})
            continue
        free_ms = r["free_ms"]
        out.append(r)
    return out
