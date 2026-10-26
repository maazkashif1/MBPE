"""Triple-barrier trade labels from futures 1m candles.

For every decision time t on the hour (UTC):
- ATR: Wilder ATR over 1h bars built from 1m candles. The bar used is the one that closes at t,
  so only candles that open before t are used.
- Entry: open of the 1m candle starting at t + latency.
- Window: the 1m candles from the entry minute for `horizon_minutes`, checked on high/low.

Barrier rules, per side:
- Within a candle the stop is checked before the take-profit (both touched -> stop; conservative).
- A candle that opens past the stop fills at its open. Other stops fill at the stop price.
- Take-profit fills at the barrier price.
- No touch -> exit at the close of the last candle in the window (expiry).

exit_time = the open of the gapping candle for gap fills, otherwise the close of the exit candle.

R = PnL / stop distance (sl_atr x ATR).
fees_R = taker fee + slippage on entry and exit notional.
funding_R = rate x entry notional for every funding time in (entry_time, exit_time]; longs pay
positive funding, shorts receive it.
net_R = gross_R - fees_R + funding_R.

Usage: uv run python -m mbpe.labels.triple_barrier
"""

import logging
from pathlib import Path

import numpy as np
import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict, NonNegativeFloat, NonNegativeInt, PositiveFloat, PositiveInt

log = logging.getLogger(__name__)
CONFIG_PATH = Path("config/labels.yaml")
LABELS_PATH = Path("data/labels/triple_barrier.parquet")
MINUTE = np.timedelta64(1, "m")


class LabelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tp_atr: PositiveFloat = 2.0
    sl_atr: PositiveFloat = 1.0
    horizon_minutes: PositiveInt = 240
    atr_period: PositiveInt = 14
    taker_fee: NonNegativeFloat = 0.0005  # fraction of notional, per side
    slippage_bps: NonNegativeFloat = 2.0  # per side
    latency_minutes: NonNegativeInt = 1


def load_config(path: Path = CONFIG_PATH) -> LabelConfig:
    return LabelConfig(**yaml.safe_load(path.read_text()))


def hourly_atr(candles: pl.DataFrame, period: int) -> pl.DataFrame:
    """Wilder ATR per 1h bar, keyed by the bar's close time (= decision_time). Null during warm-up."""
    # ponytail: bars are built from whatever 1m candles exist; an hour with no candles at all is skipped,
    # making its neighbours adjacent. Futures 1m has no gaps; revisit if that ever changes.
    bars = (
        candles.sort("open_time")
        .group_by_dynamic("open_time", every="1h")
        .agg(pl.col("high").max(), pl.col("low").min(), pl.col("close").last())
    )
    h, lo, c = (bars[x].to_numpy() for x in ("high", "low", "close"))
    prev = np.roll(c, 1)
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    if len(tr):
        tr[0] = h[0] - lo[0]
    atr = np.full(len(tr), np.nan)
    if len(tr) >= period:
        atr[period - 1] = tr[:period].mean()
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return bars.select(
        decision_time=pl.col("open_time") + pl.duration(hours=1), atr=pl.Series(atr).fill_nan(None)
    )


def _first(mask: np.ndarray) -> np.ndarray:
    """Index of the first True per row, or the row length if none."""
    return np.where(mask.any(axis=1), mask.argmax(axis=1), mask.shape[1])


def _scan(side: int, entry, atr, o, h, lo, c, cfg: LabelConfig):
    """Barrier scan for one side (+1 long, -1 short) over window arrays of shape (rows, horizon)."""
    n = cfg.horizon_minutes
    tp = entry + side * cfg.tp_atr * atr
    sl = entry - side * cfg.sl_atr * atr
    adverse, favourable = (lo, h) if side == 1 else (h, lo)
    k_sl = _first(side * (adverse - sl[:, None]) <= 0)
    k_tp = _first(side * (favourable - tp[:, None]) >= 0)
    rows = np.arange(len(entry))
    is_sl = (k_sl < n) & (k_sl <= k_tp)  # same candle -> stop first
    is_tp = (k_tp < n) & ~is_sl
    k = np.where(is_sl, k_sl, np.where(is_tp, k_tp, n - 1))
    k_open = o[rows, np.minimum(k, n - 1)]
    gapped = is_sl & (side * (k_open - sl) <= 0)
    exit_price = np.where(is_sl, np.where(gapped, k_open, sl), np.where(is_tp, tp, c[:, n - 1]))
    reason = np.where(is_sl, "sl", np.where(is_tp, "tp", "expiry"))
    return tp, sl, exit_price, reason, k, gapped


def label_trades(
    candles: pl.DataFrame, funding: pl.DataFrame, decisions: pl.DataFrame, cfg: LabelConfig
) -> tuple[pl.DataFrame, dict]:
    """Label every (decision_time, side) in `decisions` (columns decision_time, atr).

    Returns (labels, skip counts). Rows are skipped when ATR is undefined (warm-up) or when any
    1m candle in the trade window is missing.
    """
    c = candles.sort("open_time")
    ts = c["open_time"].to_numpy()
    o, h, lo, cl = (c[x].to_numpy() for x in ("open", "high", "low", "close"))
    n = cfg.horizon_minutes

    dec = decisions["decision_time"].to_numpy()
    atr_all = decisions["atr"].fill_null(np.nan).to_numpy()
    entry_t = dec + cfg.latency_minutes * MINUTE
    i = np.searchsorted(ts, entry_t)
    last = i + n - 1
    in_range = last < len(ts)
    i_s, last_s = np.where(in_range, i, 0), np.where(in_range, last, 0)
    # timestamps are strictly increasing, so matching first and last minute means all n are present
    complete = in_range & (ts[i_s] == entry_t) & (ts[last_s] == entry_t + (n - 1) * MINUTE)
    warm = ~(atr_all > 0)
    keep = complete & ~warm
    skipped = {"warmup": int(warm.sum()), "missing_candles": int((~complete & ~warm).sum())}

    dec, atr, entry_t, i = dec[keep], atr_all[keep], entry_t[keep], i[keep]
    idx = i[:, None] + np.arange(n)
    wo, wh, wl, wc = o[idx], h[idx], lo[idx], cl[idx]
    entry = wo[:, 0]
    unit = cfg.sl_atr * atr
    cost = cfg.taker_fee + cfg.slippage_bps / 1e4

    f = funding.sort("calc_time")
    # calc_time carries millisecond jitter (e.g. 16:00:00.002); round to the minute it belongs to
    f_t = f["calc_time"].dt.round("1m").to_numpy()
    cum = np.concatenate([[0.0], np.cumsum(f["last_funding_rate"].to_numpy())])

    frames = []
    for side, name in ((1, "long"), (-1, "short")):
        tp, sl, exit_price, reason, k, gapped = _scan(side, entry, atr, wo, wh, wl, wc, cfg)
        exit_t = ts[i + k] + np.where(gapped, 0, 1) * MINUTE
        rate = cum[np.searchsorted(f_t, exit_t, "right")] - cum[np.searchsorted(f_t, entry_t, "right")]
        gross_r = side * (exit_price - entry) / unit
        fees_r = cost * (entry + exit_price) / unit
        funding_r = -side * rate * entry / unit
        net_r = gross_r - fees_r + funding_r
        frames.append(
            pl.DataFrame(
                {
                    "decision_time": dec,
                    "side": [name] * len(dec),
                    "entry_time": entry_t,
                    "entry_price": entry,
                    "atr": atr,
                    "tp": tp,
                    "sl": sl,
                    "exit_time": exit_t,
                    "exit_reason": reason,
                    "gross_R": gross_r,
                    "fees_R": fees_r,
                    "funding_R": funding_r,
                    "net_R": net_r,
                    "label": net_r > 0,
                }
            )
        )
    out = pl.concat(frames).with_columns(
        pl.col("decision_time", "entry_time", "exit_time").cast(pl.Datetime("ms")).dt.replace_time_zone("UTC")
    )
    return out.sort("decision_time", "side"), skipped


def build_labels(candles: pl.DataFrame, funding: pl.DataFrame, cfg: LabelConfig) -> tuple[pl.DataFrame, dict]:
    return label_trades(candles, funding, hourly_atr(candles, cfg.atr_period), cfg)


def summary(labels: pl.DataFrame) -> pl.DataFrame:
    """Per year and side: label=1 share, exit-reason split, mean holding time, mean net_R."""
    return (
        labels.with_columns(
            year=pl.col("decision_time").dt.year(),
            hold_min=(pl.col("exit_time") - pl.col("entry_time")).dt.total_minutes(),
        )
        .group_by("year", "side")
        .agg(
            rows=pl.len(),
            label_1=pl.col("label").mean(),
            tp=(pl.col("exit_reason") == "tp").mean(),
            sl=(pl.col("exit_reason") == "sl").mean(),
            expiry=(pl.col("exit_reason") == "expiry").mean(),
            hold_min=pl.col("hold_min").mean(),
            mean_net_R=pl.col("net_R").mean(),
        )
        .sort("year", "side")
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    candles = pl.read_parquet("data/clean/binance/futures_klines_1m.parquet")
    funding = pl.read_parquet("data/clean/binance/funding.parquet")
    labels, skipped = build_labels(candles, funding, cfg)
    LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    labels.write_parquet(LABELS_PATH)
    log.info("config: %s", cfg.model_dump())
    log.info("labels: %d rows -> %s; skipped decision times: %s", labels.height, LABELS_PATH, skipped)
    with pl.Config(tbl_rows=40, tbl_cols=12, tbl_width_chars=200, float_precision=3):
        log.info("%s", summary(labels))


if __name__ == "__main__":
    main()
