"""Triple-barrier trade labels from futures 1m candles.

For every decision time t on the hour (UTC):
- ATR: Wilder ATR over bars of `atr_timeframe` (1h, 4h, ...) built from 1m candles, taken from the
  latest bar that closed at or before t, so only candles that open before t are used.
- Entry: open of the 1m candle starting at t + latency.
- Window: the 1m candles from the entry minute for `horizon_minutes`, checked on high/low.

Barrier rules, per side:
- Within a candle the stop is checked before the take-profit (both touched -> stop; conservative).
- A candle that opens past the stop fills at its open. Other stops fill at the stop price.
- Take-profit fills at the barrier price.
- No touch -> exit at the close of the last candle in the window (expiry).

exit_time = the open of the gapping candle for gap fills, otherwise the close of the exit candle.

R = PnL / stop distance |entry - sl| (= sl_atr x ATR).
fee_R, slippage_R = fee and slippage on entry and exit notional under a cost scenario (see costs()).
funding_R = rate x entry notional for every funding time in (entry_time, exit_time]; longs pay
positive funding, shorts receive it.
net_R = gross_R - fee_R - slippage_R + funding_R; stored columns use the "taker" scenario.

Usage: uv run python -m mbpe.labels.triple_barrier [CONFIG]   (default config/labels.yaml)
"""

import logging
import sys
from pathlib import Path

import numpy as np
import polars as pl
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
)

log = logging.getLogger(__name__)
CONFIG_PATH = Path("config/labels.yaml")
MINUTE = np.timedelta64(1, "m")


class LabelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tp_atr: PositiveFloat = 2.0
    sl_atr: PositiveFloat = 1.0
    horizon_minutes: PositiveInt = 240
    atr_period: PositiveInt = 14
    atr_timeframe: str = Field("1h", pattern=r"^[1-9][0-9]*h$")  # bar size for the ATR
    taker_fee: NonNegativeFloat = 0.0005  # fraction of notional, per side
    maker_fee: NonNegativeFloat = 0.0002  # used by the "maker_entry_tp" cost scenario
    slippage_bps: NonNegativeFloat = 2.0  # per side, taker fills only
    latency_minutes: NonNegativeInt = 1
    funding_interval_hours: PositiveInt = 8  # expected settlement schedule, on the UTC grid
    output: Path = Path("data/labels/triple_barrier.parquet")


def load_config(path: Path = CONFIG_PATH) -> LabelConfig:
    return LabelConfig(**yaml.safe_load(path.read_text()))


def atr_bars(candles: pl.DataFrame, every: str, period: int) -> pl.DataFrame:
    """Wilder ATR per bar of size `every`, keyed by the bar's close time. Null during warm-up."""
    # ponytail: bars are built from whatever 1m candles exist; a bar with no candles at all is skipped,
    # making its neighbours adjacent. Futures 1m has no gaps; revisit if that ever changes.
    bars = (
        candles.sort("open_time")
        .group_by_dynamic("open_time", every=every)
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
    return bars.select(bar_close=pl.col("open_time").dt.offset_by(every), atr=pl.Series(atr).fill_nan(None))


def decision_atr(candles: pl.DataFrame, cfg: LabelConfig) -> pl.DataFrame:
    """Hourly decision times (each 1h close) with the ATR of the latest bar closed at or before t."""
    hours = (
        candles.sort("open_time")
        .group_by_dynamic("open_time", every="1h")
        .agg()
        .select(decision_time=pl.col("open_time") + pl.duration(hours=1))
    )
    bars = atr_bars(candles, cfg.atr_timeframe, cfg.atr_period)
    # a bar whose close lies after the last candle is still forming; asof never reaches it for t <= data end
    return hours.join_asof(bars, left_on="decision_time", right_on="bar_close", strategy="backward").select(
        "decision_time", "atr"
    )


class MissingFundingError(ValueError):
    """A scheduled funding time inside a trade window has no funding data."""


def check_funding_coverage(f_t: np.ndarray, intervals: np.ndarray, entry_t, exit_t, cfg: LabelConfig) -> None:
    """Fail if any trade window (entry, exit] contains a scheduled funding time missing from the data.

    The schedule is every funding_interval_hours on the UTC grid (00:00, 08:00, 16:00 for 8 h). Missing
    funding is never treated as zero.
    """
    if len(intervals) and set(np.unique(intervals)) != {cfg.funding_interval_hours}:
        raise ValueError(
            f"funding intervals {sorted(set(intervals))} != configured {cfg.funding_interval_hours} h"
        )
    if not len(entry_t):
        return
    step = cfg.funding_interval_hours * 3_600_000  # ms; the schedule sits on the UTC grid of this interval
    first = (entry_t.min().astype(np.int64) // step + 1) * step
    expected = np.arange(first, exit_t.max().astype(np.int64) + 1, step).astype("datetime64[ms]")
    missing = np.setdiff1d(expected, f_t)
    if missing.size:
        hit = np.searchsorted(missing, exit_t, "right") - np.searchsorted(missing, entry_t, "right") > 0
        if hit.any():
            raise MissingFundingError(
                f"{int(hit.sum())} trades have a funding time with no funding data; "
                f"{missing.size} missing settlement(s), first {missing[:3].astype('datetime64[m]')}"
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

    f = funding.sort("calc_time")
    # calc_time carries millisecond jitter (e.g. 16:00:00.002); round to the minute it belongs to
    f_t = f["calc_time"].dt.round("1m").to_numpy()
    cum = np.concatenate([[0.0], np.cumsum(f["last_funding_rate"].to_numpy())])

    frames = []
    for side, name in ((1, "long"), (-1, "short")):
        tp, sl, exit_price, reason, k, gapped = _scan(side, entry, atr, wo, wh, wl, wc, cfg)
        exit_t = ts[i + k] + np.where(gapped, 0, 1) * MINUTE
        check_funding_coverage(f_t, f["funding_interval_hours"].to_numpy(), entry_t, exit_t, cfg)
        rate = cum[np.searchsorted(f_t, exit_t, "right")] - cum[np.searchsorted(f_t, entry_t, "right")]
        unit = np.abs(entry - sl)  # = sl_atr x ATR; same expression as in costs()
        gross_r = side * (exit_price - entry) / unit
        funding_r = -side * rate * entry / unit
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
                    "exit_price": exit_price,
                    "exit_reason": reason,
                    "gross_R": gross_r,
                    "funding_R": funding_r,
                }
            )
        )
    out = (
        pl.concat(frames)
        .with_columns(
            pl.col("decision_time", "entry_time", "exit_time")
            .cast(pl.Datetime("ms"))
            .dt.replace_time_zone("UTC")
        )
        .with_columns(costs(cfg, "taker"))
        .with_columns(net_R=NET_R)
        .with_columns(label=pl.col("net_R") > 0)
        .select(COLUMNS)
    )
    return out.sort("decision_time", "side"), skipped


COST_SCENARIOS = ("taker", "maker_entry_tp")
NET_R = pl.col("gross_R") - pl.col("fee_R") - pl.col("slippage_R") + pl.col("funding_R")
COLUMNS = [
    "decision_time", "side", "entry_time", "entry_price", "atr", "tp", "sl", "exit_time", "exit_price",
    "exit_reason", "gross_R", "fee_R", "slippage_R", "funding_R", "net_R", "label",
]  # fmt: skip


def costs(cfg: LabelConfig, scenario: str) -> list[pl.Expr]:
    """fee_R and slippage_R for a cost scenario, from entry_price, exit_price, sl and exit_reason.

    taker:          every fill pays the taker fee plus slippage (headline result).
    maker_entry_tp: entry and take-profit exits pay the maker fee with no slippage; stop and expiry
                    exits pay taker fee plus slippage. Optimistic: limit-order fills aren't modeled.
    """
    slip = cfg.slippage_bps / 1e4
    if scenario == "taker":
        entry_fee, entry_slip = pl.lit(cfg.taker_fee), pl.lit(slip)
        exit_fee, exit_slip = pl.lit(cfg.taker_fee), pl.lit(slip)
    elif scenario == "maker_entry_tp":
        is_tp = pl.col("exit_reason") == "tp"
        entry_fee, entry_slip = pl.lit(cfg.maker_fee), pl.lit(0.0)
        exit_fee = pl.when(is_tp).then(cfg.maker_fee).otherwise(cfg.taker_fee)
        exit_slip = pl.when(is_tp).then(0.0).otherwise(slip)
    else:
        raise ValueError(f"unknown cost scenario {scenario!r}; expected one of {COST_SCENARIOS}")
    entry, exit_, unit = (
        pl.col("entry_price"),
        pl.col("exit_price"),
        (pl.col("entry_price") - pl.col("sl")).abs(),
    )
    return [
        ((entry_fee * entry + exit_fee * exit_) / unit).alias("fee_R"),
        ((entry_slip * entry + exit_slip * exit_) / unit).alias("slippage_R"),
    ]


def scenario_net_r(labels: pl.DataFrame, cfg: LabelConfig, scenario: str) -> pl.Series:
    """Recompute net_R under a named cost scenario from the stored label columns."""
    return labels.with_columns(costs(cfg, scenario)).select(NET_R.alias(f"net_R_{scenario}")).to_series()


def build_labels(candles: pl.DataFrame, funding: pl.DataFrame, cfg: LabelConfig) -> tuple[pl.DataFrame, dict]:
    return label_trades(candles, funding, decision_atr(candles, cfg), cfg)


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
    cfg = load_config(Path(sys.argv[1]) if len(sys.argv) > 1 else CONFIG_PATH)
    candles = pl.read_parquet("data/clean/binance/futures_klines_1m.parquet")
    funding = pl.read_parquet("data/clean/binance/funding.parquet")
    labels, skipped = build_labels(candles, funding, cfg)
    cfg.output.parent.mkdir(parents=True, exist_ok=True)
    labels.write_parquet(cfg.output)
    log.info("config: %s", cfg.model_dump())
    log.info("labels: %d rows -> %s; skipped decision times: %s", labels.height, cfg.output, skipped)
    with pl.Config(tbl_rows=40, tbl_cols=12, tbl_width_chars=200, float_precision=3):
        log.info("%s", summary(labels))


if __name__ == "__main__":
    main()
