"""Market and derivatives features: one row per hourly decision time t.

Timing: a feature at t uses only data with timestamps before t:
- 1m candles that opened before t (so closed at or before t)
- 5-min metrics with create_time < t
- funding settlements with calc_time < t

Rules (SCOPE.md):
- levels (prices, open interest, funding): last value within a staleness limit, else NaN
- flows (volume, taker volume): sums over the minutes present, never forward-filled, with coverage
  features alongside
- changes: NaN if either endpoint is stale
- only relative values: returns, ratios, z-scores, distances in ATR units
- excluded per SCOPE.md: top-trader ratios and metrics' taker ratio (taker flow comes from klines)

Every feature is a pure function of the input context, registered with name, group (technical /
derivatives; the group decides the ablation rung) and input datasets.

Usage: uv run python -m mbpe.features.market   -> data/features/market.parquet + NaN report
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np
import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict

from mbpe.labels.triple_barrier import atr_bars

FEATURE_CONFIG = Path("config/features.yaml")
MARKET_PATH = Path("data/features/market.parquet")
CLEAN = Path("data/clean/binance")
MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000  # milliseconds
DATASETS = ("futures_klines_1m", "spot_klines_1m", "metrics", "funding", "mark_price_1m", "premium_index_1m")
TS_COL = {"futures_klines_1m": "open_time", "spot_klines_1m": "open_time", "metrics": "create_time",
          "funding": "calc_time",
          "mark_price_1m": "open_time", "premium_index_1m": "open_time"}  # fmt: skip
UTC_MS = pl.Datetime("ms", "UTC")


class FeatureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    staleness_minutes: dict[str, int]
    funding_z_min_settlements: int
    premium_min_coverage: float  # Phase 6: share of a window's 1m premium-index bars required
    vol_z_min_hours: int
    volume_profile_bin_pct: float
    value_area: float


def load_feature_config(path: Path = FEATURE_CONFIG) -> FeatureConfig:
    return FeatureConfig(**yaml.safe_load(path.read_text()))


def ms(col: pl.Series) -> np.ndarray:
    return col.cast(UTC_MS).dt.epoch("ms").to_numpy()


# ---------- 1m market arrays ----------


class Minutes:
    """1m candles of one market as arrays, with prefix sums for window aggregates."""

    def __init__(self, df: pl.DataFrame):
        df = df.sort("open_time")
        self.t = ms(df["open_time"])
        self.o, self.h, self.l, self.c, self.v, self.tbv = (
            df[x].to_numpy().astype(np.float64)
            for x in ("open", "high", "low", "close", "volume", "taker_buy_volume")
        )
        self.tp = (self.h + self.l + self.c) / 3

    @staticmethod
    def prefix(x: np.ndarray) -> np.ndarray:
        return np.concatenate([[0.0], np.cumsum(x)])

    def bounds(self, end: np.ndarray, length: int) -> tuple[np.ndarray, np.ndarray]:
        """Index range of candles that opened in [end - length, end)."""
        return np.searchsorted(self.t, end - length, "left"), np.searchsorted(self.t, end, "left")

    def last_index(self, at: np.ndarray, stale_ms: int) -> tuple[np.ndarray, np.ndarray]:
        """Index of the last candle closed by `at`, and whether it is within the staleness limit."""
        i = np.searchsorted(self.t, at, "left") - 1
        ok = (i >= 0) & (at - (self.t[np.maximum(i, 0)] + MIN) <= stale_ms)
        return np.maximum(i, 0), ok


def nan_where(ok: np.ndarray, x: np.ndarray) -> np.ndarray:
    return np.where(ok, x, np.nan)


def safe_div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b != 0, a / np.where(b != 0, b, 1), np.nan)


# ---------- context: inputs + shared intermediates ----------


class Ctx:
    """Inputs plus cached intermediates.

    `hours` is the regular hourly sequence the data supports (first full hour .. last hour whose candles have
    closed). `grid` holds the decision times to evaluate: all hours by default, or `at`, a subset of hours.
    Features that depend on the hourly sequence (RSI, %B, 24 h extremes, 7-day baselines) are computed over
    `hours` and then picked at `grid`, so the result doesn't depend on which times are evaluated.
    """

    def __init__(self, data: dict[str, pl.DataFrame], cfg: FeatureConfig, at: np.ndarray | None = None):
        self.data, self.cfg = data, cfg
        fut_t = ms(data["futures_klines_1m"]["open_time"])
        first = -(-fut_t.min() // HOUR) * HOUR  # first hour boundary at or after the first candle
        last = ((fut_t.max() + MIN) // HOUR) * HOUR  # last hour boundary with its candles closed
        self.hours = np.arange(first + HOUR, last + 1, HOUR, dtype=np.int64)
        self.grid = self.hours if at is None else np.asarray(at, dtype=np.int64)
        self.pos = np.searchsorted(self.hours, self.grid)
        if not (
            (self.pos < len(self.hours))
            & (self.hours[np.minimum(self.pos, len(self.hours) - 1)] == self.grid)
        ).all():
            raise ValueError("decision times must be hour boundaries covered by the data")
        self.stale = {k: v * MIN for k, v in cfg.staleness_minutes.items()}
        self.memo: dict = {}

    def on_grid(self, x_hours: np.ndarray) -> np.ndarray:
        """Pick values computed over `hours` at the decision times."""
        return x_hours[self.pos]

    @cached_property
    def fut(self) -> Minutes:
        return Minutes(self.data["futures_klines_1m"])

    @cached_property
    def spot(self) -> Minutes:
        return Minutes(self.data["spot_klines_1m"])

    @cached_property
    def mark(self) -> Minutes:
        return Minutes(self.data["mark_price_1m"])

    @cached_property
    def premium(self) -> Minutes:
        return Minutes(self.data["premium_index_1m"])

    def price_at(self, at: np.ndarray) -> np.ndarray:
        """Futures last close by `at` (level, staleness-limited)."""
        i, ok = self.fut.last_index(at, self.stale["price_1m"])
        return nan_where(ok, self.fut.c[i])

    @cached_property
    def price(self) -> np.ndarray:
        return self.price_at(self.grid)

    def window(
        self, x: np.ndarray, length: int, ends: np.ndarray | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """(sum of x over futures candles opened in [t - length, t), number of those candles) per t in `ends`
        (default: the decision times)."""
        p = Minutes.prefix(x)
        lo, hi = self.fut.bounds(self.grid if ends is None else ends, length)
        return p[hi] - p[lo], (hi - lo).astype(np.float64)

    @cached_property
    def bars(self) -> pl.DataFrame:
        """1h futures bars ending at each of `hours` (null where the hour has no candles)."""
        b = (
            self.data["futures_klines_1m"]
            .sort("open_time")
            .group_by_dynamic("open_time", every="1h")
            .agg(pl.col("high").max(), pl.col("low").min(), pl.col("close").last())
            .select(t=pl.col("open_time").dt.epoch("ms") + HOUR, high="high", low="low", close="close")
        )
        return pl.DataFrame({"t": self.hours}).join(b, on="t", how="left")

    def atr(self, every: str) -> np.ndarray:
        """Wilder ATR(14) of the latest `every` bar closed at or before t (same definition as the labels)."""
        a = atr_bars(self.data["futures_klines_1m"], every, 14).select(
            bar=pl.col("bar_close").dt.epoch("ms"), atr="atr"
        )
        g = pl.DataFrame({"t": self.grid}).join_asof(a, left_on="t", right_on="bar", strategy="backward")
        return g["atr"].fill_null(np.nan).to_numpy()

    @cached_property
    def atr1h(self) -> np.ndarray:
        return self.atr("1h")

    @cached_property
    def atr4h(self) -> np.ndarray:
        return self.atr("4h")

    @cached_property
    def both(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Per futures minute: spot candle present?, its volume (0 if absent) and close (NaN if absent)."""
        j = np.searchsorted(self.spot.t, self.fut.t)
        jj = np.minimum(j, len(self.spot.t) - 1)
        present = (j < len(self.spot.t)) & (self.spot.t[jj] == self.fut.t)
        return present, np.where(present, self.spot.v[jj], 0.0), np.where(present, self.spot.c[jj], np.nan)

    @cached_property
    def oi(self) -> tuple[np.ndarray, np.ndarray]:
        # OI <= 0 is a placeholder for a missing snapshot (481 rows, e.g. 2022-03-07): skip it, so the
        # staleness limit decides whether the previous snapshot still counts
        m = self.data["metrics"].filter(pl.col("sum_open_interest") > 0).sort("create_time")
        return ms(m["create_time"]), m["sum_open_interest"].to_numpy().astype(np.float64)

    def oi_at(self, at: np.ndarray) -> np.ndarray:
        t, v = self.oi
        i = np.searchsorted(t, at, "left") - 1
        ok = (i >= 0) & (at - t[np.maximum(i, 0)] <= self.stale["open_interest"])
        return nan_where(ok, v[np.maximum(i, 0)])

    @cached_property
    def funding(self) -> dict[str, np.ndarray]:
        f = self.data["funding"].sort("calc_time")
        return {
            "t": ms(f["calc_time"]),
            "t_round": ms(f["calc_time"].dt.round("1m")),  # calc_time carries ms jitter
            "rate": f["last_funding_rate"].to_numpy().astype(np.float64),
            "interval": f["funding_interval_hours"].to_numpy().astype(np.int64) * HOUR,
        }

    @cached_property
    def funding_last(self) -> tuple[np.ndarray, np.ndarray]:
        """(index of the last settlement before t, whether it is within interval + slack)."""
        f = self.funding
        i = np.searchsorted(f["t"], self.grid, "left") - 1
        ii = np.maximum(i, 0)
        ok = (i >= 0) & (self.grid - f["t"][ii] <= f["interval"][ii] + self.stale["funding_slack"])
        return ii, ok


# ---------- registry ----------


@dataclass(frozen=True)
class Feature:
    name: str
    group: str  # technical | derivatives | macd_ema (Phase 4) | mark_premium (Phase 6)
    inputs: tuple[str, ...]
    fn: Callable[[Ctx], np.ndarray]


REGISTRY: dict[str, Feature] = {}
FUT, SPOT, METRICS, FUNDING, MARK, PREMIUM = DATASETS


def register(name: str, group: str, inputs: tuple[str, ...]):
    def deco(fn: Callable[[Ctx], np.ndarray]) -> Callable[[Ctx], np.ndarray]:
        if name in REGISTRY:
            raise ValueError(f"duplicate feature {name}")
        REGISTRY[name] = Feature(name, group, inputs, fn)
        return fn

    return deco


WINDOWS = {"1h": HOUR, "4h": 4 * HOUR, "24h": DAY, "7d": 7 * DAY}

# --- technical: returns and volatility


def _ret(ctx: Ctx, length: int) -> np.ndarray:
    return np.log(ctx.price / ctx.price_at(ctx.grid - length))


for _w in ("1h", "4h", "24h", "7d"):
    register(f"ret_{_w}", "technical", (FUT,))(lambda ctx, n=WINDOWS[_w]: _ret(ctx, n))


def _rv(ctx: Ctx, length: int) -> np.ndarray:
    """Realized vol: sqrt(mean squared 1m log return x minutes in window), adjacent minutes only."""
    f = ctx.fut
    r = np.zeros(len(f.t))
    adjacent = np.r_[False, np.diff(f.t) == MIN]
    r[1:] = np.log(f.c[1:] / f.c[:-1])
    r = np.where(adjacent, r, 0.0)
    s, _ = ctx.window(r * r, length)
    n, _ = ctx.window(adjacent.astype(np.float64), length)
    return np.sqrt(safe_div(s, n) * (length / MIN))


for _w in ("1h", "4h", "24h"):
    register(f"rv_{_w}", "technical", (FUT,))(lambda ctx, n=WINDOWS[_w]: _rv(ctx, n))

register("atr_1h_pct", "technical", (FUT,))(lambda ctx: ctx.atr1h / ctx.price)
register("atr_4h_pct", "technical", (FUT,))(lambda ctx: ctx.atr4h / ctx.price)


@register("rsi_14_1h", "technical", (FUT,))
def rsi_14_1h(ctx: Ctx) -> np.ndarray:
    """Wilder RSI(14) on 1h closes (bars present, in time order), value of the bar ending at t."""
    b = ctx.bars.filter(pl.col("close").is_not_null())
    c = b["close"].to_numpy()
    d = np.diff(c, prepend=np.nan)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    rsi = np.full(len(c), np.nan)
    if len(c) > 14:
        au, ad = up[1:15].mean(), dn[1:15].mean()
        for i in range(14, len(c)):
            if i > 14:
                au, ad = (au * 13 + up[i]) / 14, (ad * 13 + dn[i]) / 14
            rsi[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    out = pl.DataFrame({"t": b["t"], "v": rsi})
    return pl.DataFrame({"t": ctx.grid}).join(out, on="t", how="left")["v"].fill_null(np.nan).to_numpy()


@register("bb_pctb_20_1h", "technical", (FUT,))
def bb_pctb_20_1h(ctx: Ctx) -> np.ndarray:
    """Bollinger %B(20, 2 sd) on 1h closes, bar ending at t."""
    b = ctx.bars.filter(pl.col("close").is_not_null()).with_columns(
        m=pl.col("close").rolling_mean(20), s=pl.col("close").rolling_std(20, ddof=0)
    )
    pctb = b.select("t", v=(pl.col("close") - (pl.col("m") - 2 * pl.col("s"))) / (4 * pl.col("s")))
    return (
        pl.DataFrame({"t": ctx.grid})
        .join(pctb, on="t", how="left")["v"]
        .fill_null(np.nan)
        .fill_nan(np.nan)
        .to_numpy()
    )


def _extreme(ctx: Ctx, col: str, fn) -> np.ndarray:
    """Max/min of 1h bar highs/lows over the 24 bars ending at t (bars present)."""
    x = ctx.bars[col].fill_null(np.nan).to_numpy()
    pad = np.concatenate([np.full(23, np.nan), x])
    win = np.lib.stride_tricks.sliding_window_view(pad, 24)[ctx.pos]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN window -> NaN
        return fn(win, axis=1)


register("dist_high_24h_atr", "technical", (FUT,))(
    lambda ctx: (ctx.price - _extreme(ctx, "high", np.nanmax)) / ctx.atr1h
)
register("dist_low_24h_atr", "technical", (FUT,))(
    lambda ctx: (ctx.price - _extreme(ctx, "low", np.nanmin)) / ctx.atr1h
)

# --- technical: volume and taker flow (flows: minutes present only)


for _w in ("1h", "4h", "24h", "7d"):
    register(f"fut_cov_{_w}", "technical", (FUT,))(
        lambda ctx, n=WINDOWS[_w]: ctx.window(np.ones(len(ctx.fut.t)), n)[1] / (n / MIN)
    )


@register("vol_z_7d", "technical", (FUT,))
def vol_z_7d(ctx: Ctx) -> np.ndarray:
    """log volume per present minute in the last hour, z-scored against the previous 168 hours."""
    v, n = ctx.window(ctx.fut.v, HOUR, ends=ctx.hours)
    with np.errstate(divide="ignore"):
        x = np.log(safe_div(v, n))
    x = np.where(np.isfinite(x), x, np.nan)
    base = pl.DataFrame({"x": pl.Series(x).fill_nan(None)}).select(
        m=pl.col("x").shift(1).rolling_mean(168, min_samples=ctx.cfg.vol_z_min_hours),
        sd=pl.col("x").shift(1).rolling_std(168, min_samples=ctx.cfg.vol_z_min_hours),
    )
    m, sd = (base[c].fill_null(np.nan).to_numpy() for c in ("m", "sd"))
    return ctx.on_grid(safe_div(x - m, sd))


def _taker_ratio(ctx: Ctx, length: int) -> np.ndarray:
    tb, _ = ctx.window(ctx.fut.tbv, length)
    v, _ = ctx.window(ctx.fut.v, length)
    return safe_div(tb, v)


def _cvd_norm(ctx: Ctx, length: int) -> np.ndarray:
    """Taker buy - taker sell volume over the window, / (7-day volume per minute x window minutes)."""
    delta, _ = ctx.window(2 * ctx.fut.tbv - ctx.fut.v, length)
    v7, n7 = ctx.window(ctx.fut.v, 7 * DAY)
    return safe_div(delta, safe_div(v7, n7) * (length / MIN))


for _w in ("1h", "4h", "24h"):
    register(f"taker_buy_ratio_{_w}", "technical", (FUT,))(lambda ctx, n=WINDOWS[_w]: _taker_ratio(ctx, n))
    register(f"cvd_norm_{_w}", "technical", (FUT,))(lambda ctx, n=WINDOWS[_w]: _cvd_norm(ctx, n))


# --- technical: volume profile, VWAP


def _volume_profile(ctx: Ctx, length: int) -> tuple[np.ndarray, np.ndarray]:
    """(distance of price from the point of control in ATR units, position in the value area).

    Each 1m candle's volume goes to the bin of its typical price; bins are volume_profile_bin_pct of the price
    at t. Value area (classic, contiguous): start at the point of control and keep adding the neighbouring
    bin (just above or just below the current range) with more volume (ties: above) until the range holds
    value_area of the volume.
    """
    key = ("vp", length)
    if key in ctx.memo:
        return ctx.memo[key]
    f, cfg = ctx.fut, ctx.cfg
    lo, hi = f.bounds(ctx.grid, length)
    poc_d, va_pos = np.full(len(ctx.grid), np.nan), np.full(len(ctx.grid), np.nan)
    for k in range(len(ctx.grid)):
        p, a = ctx.price[k], ctx.atr1h[k]
        if hi[k] <= lo[k] or not np.isfinite(p):
            continue
        width = p * cfg.volume_profile_bin_pct / 100
        bins = np.floor(f.tp[lo[k] : hi[k]] / width).astype(np.int64)
        b0 = bins.min()
        vol = np.bincount(bins - b0, weights=f.v[lo[k] : hi[k]])
        poc = (b0 + vol.argmax() + 0.5) * width
        lo_b = hi_b = int(vol.argmax())
        inside, target = vol[lo_b], cfg.value_area * vol.sum()
        while inside < target:
            up = vol[hi_b + 1] if hi_b + 1 < len(vol) else -1.0
            down = vol[lo_b - 1] if lo_b > 0 else -1.0
            if up >= down:
                hi_b += 1
                inside += up
            else:
                lo_b -= 1
                inside += down
        val, vah = (b0 + lo_b) * width, (b0 + hi_b + 1) * width
        poc_d[k] = (p - poc) / a
        va_pos[k] = (p - val) / (vah - val)
    ctx.memo[key] = poc_d, va_pos
    return poc_d, va_pos


for _w in ("24h", "7d"):
    register(f"vp_poc_dist_{_w}", "technical", (FUT,))(lambda ctx, n=WINDOWS[_w]: _volume_profile(ctx, n)[0])
    register(f"vp_va_pos_{_w}", "technical", (FUT,))(lambda ctx, n=WINDOWS[_w]: _volume_profile(ctx, n)[1])


def _vwap_dist(ctx: Ctx, anchor: np.ndarray) -> np.ndarray:
    """(price - VWAP since anchor) / ATR_1h, over futures candles opened in [anchor, t)."""
    f = ctx.fut
    pv, v = Minutes.prefix(f.tp * f.v), Minutes.prefix(f.v)
    lo, hi = np.searchsorted(f.t, anchor, "left"), np.searchsorted(f.t, ctx.grid, "left")
    vwap = safe_div(pv[hi] - pv[lo], v[hi] - v[lo])
    return (ctx.price - vwap) / ctx.atr1h


def _day_anchor(t: np.ndarray) -> np.ndarray:
    return ((t - 1) // DAY) * DAY  # session containing the last closed minute; at 00:00 -> previous day


def _week_anchor(t: np.ndarray) -> np.ndarray:
    d = _day_anchor(t)
    return d - ((d // DAY + 3) % 7) * DAY  # 1970-01-01 was a Thursday; Monday = 0


register("vwap_day_dist_atr", "technical", (FUT,))(lambda ctx: _vwap_dist(ctx, _day_anchor(ctx.grid)))
register("vwap_week_dist_atr", "technical", (FUT,))(lambda ctx: _vwap_dist(ctx, _week_anchor(ctx.grid)))

# --- technical: spot vs perp


def _spot_share(ctx: Ctx, length: int) -> np.ndarray:
    """Spot share of spot + perp volume, over minutes where both markets have a candle."""
    present, sv, _ = ctx.both
    s, _ = ctx.window(sv, length)
    fv, _ = ctx.window(np.where(present, ctx.fut.v, 0.0), length)
    return safe_div(s, s + fv)


for _w in ("1h", "4h"):
    register(f"spot_share_{_w}", "technical", (FUT, SPOT))(lambda ctx, n=WINDOWS[_w]: _spot_share(ctx, n))
    register(f"spot_cov_{_w}", "technical", (FUT, SPOT))(
        lambda ctx, n=WINDOWS[_w]: ctx.window(ctx.both[0].astype(np.float64), n)[0] / (n / MIN)
    )


@register("basis_spot_perp", "technical", (FUT, SPOT))
def basis_spot_perp(ctx: Ctx) -> np.ndarray:
    """(perp close - spot close) / spot close at the latest minute where both closed, staleness-limited."""
    present, _, sc = ctx.both
    idx = np.flatnonzero(present)
    j = np.searchsorted(ctx.fut.t[idx], ctx.grid, "left") - 1
    i = idx[np.maximum(j, 0)]
    ok = (j >= 0) & (ctx.grid - (ctx.fut.t[i] + MIN) <= ctx.stale["price_1m"])
    return nan_where(ok, (ctx.fut.c[i] - sc[i]) / sc[i])


register("hour_of_day", "technical", ())(lambda ctx: ((ctx.grid // HOUR) % 24).astype(np.float64))
register("day_of_week", "technical", ())(lambda ctx: ((ctx.grid // DAY + 3) % 7 + 1).astype(np.float64))

# --- derivatives


@register("funding_last", "derivatives", (FUNDING,))
def funding_last(ctx: Ctx) -> np.ndarray:
    i, ok = ctx.funding_last
    return nan_where(ok, ctx.funding["rate"][i])


@register("funding_z_30d", "derivatives", (FUNDING,))
def funding_z_30d(ctx: Ctx) -> np.ndarray:
    """Last settled rate vs settlements with calc_time in [t - 30d, t)."""
    f = ctx.funding
    lo, hi = np.searchsorted(f["t"], ctx.grid - 30 * DAY, "left"), np.searchsorted(f["t"], ctx.grid, "left")
    s1, s2 = Minutes.prefix(f["rate"]), Minutes.prefix(f["rate"] ** 2)
    n = (hi - lo).astype(np.float64)
    mean = safe_div(s1[hi] - s1[lo], n)
    sd = np.sqrt(np.maximum(safe_div(s2[hi] - s2[lo], n) - mean**2, 0))
    z = safe_div(funding_last(ctx) - mean, sd)
    return np.where(n >= ctx.cfg.funding_z_min_settlements, z, np.nan)


@register("hours_to_next_funding", "derivatives", (FUNDING,))
def hours_to_next_funding(ctx: Ctx) -> np.ndarray:
    i, ok = ctx.funding_last
    f = ctx.funding
    return nan_where(ok, (f["t_round"][i] + f["interval"][i] - ctx.grid) / HOUR)


def _oi_chg(ctx: Ctx, length: int) -> np.ndarray:
    return ctx.oi_at(ctx.grid) / ctx.oi_at(ctx.grid - length) - 1


OI_QUADRANTS = {
    1: "price up, OI up",
    2: "price up, OI down",
    3: "price down, OI up",
    4: "price down, OI down",
}


def _oi_price_quadrant(ctx: Ctx, length: int) -> np.ndarray:
    """Sign of price change x sign of OI change over the window, as a category 1-4 (OI_QUADRANTS).

    A zero change counts as up. NaN if either change is missing. Treat as categorical when modeling.
    """
    r, oi = _ret(ctx, length), _oi_chg(ctx, length)
    q = 1 + 2 * (r < 0) + (oi < 0)
    return np.where(np.isfinite(r) & np.isfinite(oi), q, np.nan)


for _w in ("1h", "4h", "24h"):
    register(f"oi_chg_{_w}", "derivatives", (METRICS,))(lambda ctx, n=WINDOWS[_w]: _oi_chg(ctx, n))
    register(f"oi_price_quadrant_{_w}", "derivatives", (METRICS, FUT))(
        lambda ctx, n=WINDOWS[_w]: _oi_price_quadrant(ctx, n)
    )


# --- mark_premium (Phase 6 Workstream A; SCOPE.md): mark price and premium index, 1m bars closed by t


def _last_close(ctx: Ctx, m: Minutes) -> np.ndarray:
    i, ok = m.last_index(ctx.grid, ctx.stale["price_1m"])
    return nan_where(ok, m.c[i])


def _premium_mean(ctx: Ctx, length: int, ends: np.ndarray) -> np.ndarray:
    """Mean premium index (bps) of 1m bars opened in [t - length, t); NaN below premium_min_coverage."""
    m = ctx.premium
    p = Minutes.prefix(m.c)
    lo, hi = m.bounds(ends, length)
    n = (hi - lo).astype(np.float64)
    return np.where(
        n >= ctx.cfg.premium_min_coverage * length / MIN, safe_div(p[hi] - p[lo], n) * 1e4, np.nan
    )


register("basis_mark_bps", "mark_premium", (FUT, MARK))(
    lambda ctx: safe_div(ctx.price - _last_close(ctx, ctx.mark), _last_close(ctx, ctx.mark)) * 1e4
)
register("premium_last_bps", "mark_premium", (PREMIUM,))(lambda ctx: _last_close(ctx, ctx.premium) * 1e4)
register("premium_mean_1h_bps", "mark_premium", (PREMIUM,))(lambda ctx: _premium_mean(ctx, HOUR, ctx.grid))
register("premium_mean_8h_bps", "mark_premium", (PREMIUM,))(
    lambda ctx: _premium_mean(ctx, 8 * HOUR, ctx.grid)
)


@register("premium_z_7d", "mark_premium", (PREMIUM,))
def premium_z_7d(ctx: Ctx) -> np.ndarray:
    """1h mean premium, z-scored against the previous 168 hourly values (>= vol_z_min_hours present)."""
    x = _premium_mean(ctx, HOUR, ctx.hours)
    base = pl.DataFrame({"x": pl.Series(x).fill_nan(None)}).select(
        m=pl.col("x").shift(1).rolling_mean(168, min_samples=ctx.cfg.vol_z_min_hours),
        sd=pl.col("x").shift(1).rolling_std(168, min_samples=ctx.cfg.vol_z_min_hours),
    )
    m, sd = (base[c].fill_null(np.nan).to_numpy() for c in ("m", "sd"))
    return ctx.on_grid(safe_div(x - m, sd))


# --- macd_ema (Phase 4 rung 4a; SCOPE.md): MACD(12, 26, 9) and EMA distances on 1h closes, in ATR units


def _ema(x: pl.Expr, n: int) -> pl.Expr:
    return x.ewm_mean(alpha=2 / (n + 1), adjust=False)  # recursive from the first bar present


def _macd_ema(ctx: Ctx) -> pl.DataFrame:
    """MACD line/signal/histogram and close - EMA_n on the 1h bars present, at the decision times."""
    if "macd_ema" not in ctx.memo:
        c = pl.col("close")
        b = ctx.bars.filter(c.is_not_null()).with_columns(line=_ema(c, 12) - _ema(c, 26))
        b = b.with_columns(signal=_ema(pl.col("line"), 9)).with_columns(
            hist=pl.col("line") - pl.col("signal")
        )
        b = b.with_columns(*[(c - _ema(c, n)).alias(f"d{n}") for n in (20, 50, 200)])
        ctx.memo["macd_ema"] = pl.DataFrame({"t": ctx.grid}).join(b, on="t", how="left")
    return ctx.memo["macd_ema"]


def _per_atr(ctx: Ctx, col: str) -> np.ndarray:
    return safe_div(_macd_ema(ctx)[col].fill_null(np.nan).to_numpy(), ctx.atr1h)


for _c in ("line", "signal", "hist"):
    register(f"macd_{_c}_atr", "macd_ema", (FUT,))(lambda ctx, c=_c: _per_atr(ctx, c))
for _n in (20, 50, 200):
    register(f"ema{_n}_dist_atr", "macd_ema", (FUT,))(lambda ctx, n=_n: _per_atr(ctx, f"d{n}"))


# ---------- build ----------


def load_data(root: Path = CLEAN) -> dict[str, pl.DataFrame]:
    return {name: pl.read_parquet(root / f"{name}.parquet") for name in DATASETS}


def truncate(data: dict[str, pl.DataFrame], t) -> dict[str, pl.DataFrame]:
    """Only data with timestamps before t."""
    return {k: df.filter(pl.col(TS_COL[k]) < t) for k, df in data.items()}


def build_features(
    data: dict[str, pl.DataFrame],
    cfg: FeatureConfig,
    at: np.ndarray | None = None,
    names: list[str] | None = None,
) -> pl.DataFrame:
    """Features at every supported hour, or only at the decision times `at` (epoch ms, hour boundaries)."""
    ctx = Ctx(data, cfg, at)
    cols = {n: np.asarray(REGISTRY[n].fn(ctx), dtype=np.float64) for n in (names or REGISTRY)}
    out = pl.DataFrame({"decision_time": pl.Series(ctx.grid).cast(UTC_MS), **cols})
    return out.with_columns(pl.col(list(cols)).fill_nan(None))  # NaN -> null: one missing-value marker


def nan_report(df: pl.DataFrame) -> pl.DataFrame:
    """Share of missing values per feature per year (long format)."""
    feats = [c for c in df.columns if c != "decision_time"]
    return (
        df.group_by(year=pl.col("decision_time").dt.year())
        .agg([pl.col(c).is_null().mean().alias(c) for c in feats])
        .sort("year")
        .unpivot(index="year", variable_name="feature", value_name="nan_share")
    )


def main() -> None:
    import time

    t0 = time.perf_counter()
    df = build_features(load_data(), load_feature_config())
    MARKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(MARKET_PATH)
    print(f"{df.height:,} decision times x {df.width - 1} features, {time.perf_counter() - t0:.0f}s")
    rep = nan_report(df).pivot("year", index="feature", values="nan_share")
    with pl.Config(tbl_rows=80, tbl_cols=12, tbl_width_chars=200, float_precision=4):
        print(rep)
        after = df.filter(pl.col("decision_time") >= pl.datetime(2021, 1, 1, time_zone="UTC"))
        share = after.select(pl.all().exclude("decision_time").is_null().mean()).unpivot()
        print("features with > 5% missing since 2021-01-01:")
        print(share.filter(pl.col("value") > 0.05).sort("value", descending=True))


if __name__ == "__main__":
    main()
