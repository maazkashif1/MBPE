"""News and calendar features: one row per hourly decision time t.

News (group "news"):
- Every input goes through availability: a row counts at t only if its batch label (DATE) + 15 min <= t, the
  same rule as gdelt_bq.news_as_of. Coverage batches likewise.
- Computed separately for the crypto and macro keyword groups. A row with keywords from both groups counts in
  both.
- Windows end at t: (t - W, t] in availability time.
- Coverage = share of the window's 15-min batches that GDELT published (from the coverage table). A window
  below `min_coverage` makes every feature of that window NaN, never zero. An outage is no data, not no news.
- Sentiment counts each near-duplicate cluster once: the mean score of its scored titles available by t, for
  clusters with a title in the window. Syndicated copies don't outweigh single stories. Empty titles count
  toward volume only (no score, no tone).
- The sentiment model is `default_sentiment` in config/news.yaml; switching models is a config change.

Calendar (group "calendar"): from data/reference/macro_events.csv (US CPI releases and FOMC statements at
their actual UTC times; see SCOPE.md for the rescheduling limitation).

Usage: uv run python -m mbpe.features.news   -> data/features/news.parquet + missingness report
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.features.dedup import NewsConfig, load_news_config, normalize_title
from mbpe.features.market import DAY, HOUR, MIN, UTC_MS, Feature, safe_div
from mbpe.sources.gdelt_bq import AVAILABILITY_LAG, CRYPTO_KEYWORDS, MACRO_KEYWORDS

NEWS_PATH = Path("data/features/news.parquet")
DEDUP_PATH = Path("data/features/news_dedup.parquet")
CALENDAR_PATH = Path("data/reference/macro_events.csv")
BATCH = 15 * MIN
LAG = int(AVAILABILITY_LAG.total_seconds() * 1000)
GROUPS = ("crypto", "macro")
WINDOWS = {"1h": HOUR, "4h": 4 * HOUR, "24h": DAY}
NEWS_INPUTS = ("gdelt_rows", "gdelt_coverage", "news_dedup", "sentiment")
CAL_INPUTS = ("macro_events",)


def label_ms(col: str = "DATE") -> pl.Expr:
    return pl.col(col).cast(pl.String).str.strptime(UTC_MS, "%Y%m%d%H%M%S").dt.epoch("ms")


def prepare(rows: pl.DataFrame, clusters: pl.DataFrame, scores: pl.DataFrame | None) -> pl.DataFrame:
    """One row per article with availability time, group flags, cluster info, sentiment score and tone.

    rows: BACKFILL_SCHEMA. clusters: DATE, DocumentIdentifier, cluster_id, match_sim (dedup output).
    scores: norm, score (None -> no sentiment). Sorted by availability.
    """
    key = ["DATE", "DocumentIdentifier"]
    df = rows.join(clusters.select(*key, "cluster_id", "match_sim"), on=key, how="left")
    if (missing := df["cluster_id"].null_count()) > 0:
        raise ValueError(f"{missing} rows have no dedup cluster; re-run mbpe.features.dedup")
    df = df.with_columns(
        avail=label_ms() + LAG,
        crypto=pl.col("keywords").list.eval(pl.element().is_in(list(CRYPTO_KEYWORDS))).list.any(),
        macro=pl.col("keywords").list.eval(pl.element().is_in(list(MACRO_KEYWORDS))).list.any(),
        norm=pl.col("title").map_elements(normalize_title, return_dtype=pl.String),
        new_cluster=pl.col("match_sim").is_null(),
        tone=pl.col("V2Tone").str.split(",").list.first().cast(pl.Float64, strict=False),
    ).with_columns(empty=pl.col("norm") == "")
    if scores is not None:
        df = df.join(scores.select("norm", "score"), on="norm", how="left")
    else:
        df = df.with_columns(score=pl.lit(None, pl.Float64))
    # empty titles: volume only
    df = df.with_columns(
        score=pl.when(pl.col("empty")).then(None).otherwise("score"),
        tone=pl.when(pl.col("empty")).then(None).otherwise("tone"),
    )
    return df.select("avail", "crypto", "macro", "empty", "new_cluster", "cluster_id", "score", "tone").sort(
        "avail"
    )


def load_calendar(path: Path = CALENDAR_PATH) -> np.ndarray:
    """Sorted UTC epoch-ms times of CPI releases and FOMC statements (released or scheduled)."""
    ev = pl.read_csv(path).filter(
        pl.col("event").is_in(["CPI", "FOMC"]) & (pl.col("release_utc").is_not_null())
    )
    t = ev.select(pl.col("release_utc").str.strptime(UTC_MS, "%Y-%m-%d %H:%M").dt.epoch("ms"))["release_utc"]
    return np.sort(t.to_numpy())


class NewsCtx:
    def __init__(
        self,
        prep: pl.DataFrame,
        coverage: pl.DataFrame,
        events: np.ndarray,
        cfg: NewsConfig,
        at: np.ndarray | None = None,
    ):
        self.cfg = cfg
        self.events = events
        self.a = prep["avail"].to_numpy()
        self.mask = {g: prep[g].to_numpy() for g in GROUPS}
        self.new = prep["new_cluster"].to_numpy()
        self.cid = prep["cluster_id"].to_numpy()
        self.score = prep["score"].fill_null(np.nan).to_numpy()
        self.tone = prep["tone"].fill_null(np.nan).to_numpy()
        present = coverage.filter(pl.col("n_rows") > 0).select(label_ms())["DATE"].to_numpy()
        self.batch_avail = np.sort(present) + LAG
        start = (coverage.select(label_ms())["DATE"].min() + LAG + HOUR - 1) // HOUR * HOUR
        last = int(self.batch_avail.max()) // HOUR * HOUR if at is None else int(np.max(at))
        self.hours = np.arange(start, last + 1, HOUR, dtype=np.int64)
        self.grid = self.hours if at is None else np.asarray(at, dtype=np.int64)
        self.pos = np.searchsorted(self.hours, self.grid)
        if not (
            (self.pos < len(self.hours))
            & (self.hours[np.minimum(self.pos, len(self.hours) - 1)] == self.grid)
        ).all():
            raise ValueError("decision times must be hour boundaries within the news data")
        self.memo: dict = {}

    def on_grid(self, x_hours: np.ndarray) -> np.ndarray:
        """Pick values computed over `hours` at the decision times."""
        return x_hours[self.pos]

    def rows_in(self, ends: np.ndarray, length: int) -> tuple[np.ndarray, np.ndarray]:
        """Index range of rows with availability in (end - length, end]."""
        return np.searchsorted(self.a, ends - length, "right"), np.searchsorted(self.a, ends, "right")

    def count(self, x: np.ndarray, length: int, ends: np.ndarray | None = None) -> np.ndarray:
        ends = self.grid if ends is None else ends
        p = np.concatenate([[0], np.cumsum(x)])
        lo, hi = self.rows_in(ends, length)
        return (p[hi] - p[lo]).astype(np.float64)

    def coverage(self, length: int, ends: np.ndarray | None = None) -> np.ndarray:
        """Share of the window's 15-min batches that were published (availability in (end - length, end])."""
        ends = self.grid if ends is None else ends
        n = np.searchsorted(self.batch_avail, ends, "right") - np.searchsorted(
            self.batch_avail, ends - length, "right"
        )
        return n / (length / BATCH)

    def covered(self, length: int) -> np.ndarray:
        return self.coverage(length) >= self.cfg.min_coverage

    def cluster_stats(self, g: str, length: int, values: str) -> dict[str, np.ndarray]:
        """Per t: mean, std and strong-negative share over clusters with a `values` row in the window.

        A cluster's value is the mean of its group rows with a value available by t. Clusters close 24 h after
        their first member, so every such row is available within (t - length - cap, t].
        """
        key = (g, length, values)
        if key in self.memo:
            return self.memo[key]
        val = self.score if values == "score" else self.tone
        ok_row = self.mask[g] & np.isfinite(val)
        cap = self.cfg.dedup_cluster_max_hours * HOUR
        lo, _ = self.rows_in(self.grid, length + cap)
        _, hi = self.rows_in(self.grid, 0)
        mean, std, neg = (np.full(len(self.grid), np.nan) for _ in range(3))
        for k, t in enumerate(self.grid):
            s = slice(lo[k], hi[k])
            m = ok_row[s]
            if not m.any():
                continue
            cl, v, inwin = self.cid[s][m], val[s][m], self.a[s][m] > t - length
            if not inwin.any():
                continue
            keep = np.isin(cl, np.unique(cl[inwin]))
            _, inv = np.unique(cl[keep], return_inverse=True)
            cm = np.bincount(inv, weights=v[keep]) / np.bincount(inv)
            mean[k] = cm.mean()
            std[k] = cm.std(ddof=1) if len(cm) > 1 else np.nan
            neg[k] = (cm < self.cfg.strong_negative_threshold).mean()
        ok = self.covered(length)
        out = {name: np.where(ok, x, np.nan) for name, x in (("mean", mean), ("std", std), ("neg", neg))}
        self.memo[key] = out
        return out


# ---------- registry ----------

NEWS_REGISTRY: dict[str, Feature] = {}


def register(name: str, group: str, inputs: tuple[str, ...]):
    def deco(fn):
        if name in NEWS_REGISTRY:
            raise ValueError(f"duplicate feature {name}")
        NEWS_REGISTRY[name] = Feature(name, group, inputs, fn)
        return fn

    return deco


def _gated(ctx: NewsCtx, x: np.ndarray, length: int) -> np.ndarray:
    return np.where(ctx.covered(length), x, np.nan)


for _w, _n in WINDOWS.items():
    register(f"news_cov_{_w}", "news", NEWS_INPUTS)(lambda ctx, n=_n: ctx.coverage(n))

for _g in GROUPS:
    for _w, _n in WINDOWS.items():
        register(f"news_{_g}_articles_{_w}", "news", NEWS_INPUTS)(
            lambda ctx, g=_g, n=_n: _gated(ctx, ctx.count(ctx.mask[g], n), n)
        )
        register(f"news_{_g}_new_clusters_{_w}", "news", NEWS_INPUTS)(
            lambda ctx, g=_g, n=_n: _gated(ctx, ctx.count(ctx.mask[g] & ctx.new, n), n)
        )
        for _stat in ("mean", "std", "neg"):
            _name = {"mean": "sent_mean", "std": "sent_std", "neg": "sent_neg_share"}[_stat]
            register(f"news_{_g}_{_name}_{_w}", "news", NEWS_INPUTS)(
                lambda ctx, g=_g, n=_n, s=_stat: ctx.cluster_stats(g, n, "score")[s]
            )
    for _w in ("1h", "4h"):
        register(f"news_{_g}_novelty_{_w}", "news", NEWS_INPUTS)(
            lambda ctx, g=_g, n=WINDOWS[_w]: _gated(
                ctx, safe_div(ctx.count(ctx.mask[g] & ctx.new, n), ctx.count(ctx.mask[g], n)), n
            )
        )
    register(f"news_{_g}_sent_change_4h_24h", "news", NEWS_INPUTS)(
        lambda ctx, g=_g: (
            ctx.cluster_stats(g, 4 * HOUR, "score")["mean"] - ctx.cluster_stats(g, DAY, "score")["mean"]
        )
    )

    @register(f"news_{_g}_count_z_4h", "news", NEWS_INPUTS)
    def _count_z(ctx: NewsCtx, g: str = _g) -> np.ndarray:
        """4h article count (scaled to full coverage) vs the previous 168 hourly values (NaN-skipping)."""
        c = ctx.count(ctx.mask[g], 4 * HOUR, ends=ctx.hours)
        cov = ctx.coverage(4 * HOUR, ends=ctx.hours)
        x = np.where(cov >= ctx.cfg.min_coverage, safe_div(c, cov), np.nan)  # count scaled to full coverage
        need = int(np.ceil(ctx.cfg.min_coverage * 168))
        s = pl.DataFrame({"x": pl.Series(x).fill_nan(None)}).select(
            m=pl.col("x").shift(1).rolling_mean(168, min_samples=need),
            sd=pl.col("x").shift(1).rolling_std(168, min_samples=need),
        )
        m, sd = (s[c].fill_null(np.nan).to_numpy() for c in ("m", "sd"))
        return ctx.on_grid(safe_div(x - m, sd))


for _w in ("4h", "24h"):
    register(f"news_macro_tone_{_w}", "news", NEWS_INPUTS)(
        lambda ctx, n=WINDOWS[_w]: ctx.cluster_stats("macro", n, "tone")["mean"]
    )


def _next_event(ctx: NewsCtx) -> np.ndarray:
    i = np.searchsorted(ctx.events, ctx.grid, "right")
    ok = i < len(ctx.events)
    return np.where(ok, (ctx.events[np.minimum(i, len(ctx.events) - 1)] - ctx.grid) / HOUR, np.nan)


@register("cal_hours_to_next_event", "calendar", CAL_INPUTS)
def cal_hours_to_next_event(ctx: NewsCtx) -> np.ndarray:
    """Hours until the next CPI release or FOMC statement strictly after t."""
    return _next_event(ctx)


@register("cal_hours_since_last_event", "calendar", CAL_INPUTS)
def cal_hours_since_last_event(ctx: NewsCtx) -> np.ndarray:
    """Hours since the last CPI release or FOMC statement at or before t."""
    i = np.searchsorted(ctx.events, ctx.grid, "right") - 1
    return np.where(i >= 0, (ctx.grid - ctx.events[np.maximum(i, 0)]) / HOUR, np.nan)


@register("cal_event_next_4h", "calendar", CAL_INPUTS)
def cal_event_next_4h(ctx: NewsCtx) -> np.ndarray:
    nxt = _next_event(ctx)
    return np.where(np.isfinite(nxt), (nxt <= 4).astype(np.float64), np.nan)


# ---------- build ----------


def build_news_features(
    prep: pl.DataFrame,
    coverage: pl.DataFrame,
    events: np.ndarray,
    cfg: NewsConfig,
    at: np.ndarray | None = None,
) -> pl.DataFrame:
    ctx = NewsCtx(prep, coverage, events, cfg, at)
    cols = {n: np.asarray(f.fn(ctx), dtype=np.float64) for n, f in NEWS_REGISTRY.items()}
    out = pl.DataFrame({"decision_time": pl.Series(ctx.grid).cast(UTC_MS), **cols})
    return out.with_columns(pl.col(list(cols)).fill_nan(None))


@dataclass
class NewsInputs:
    prep: pl.DataFrame
    coverage: pl.DataFrame
    events: np.ndarray


def load_inputs(cfg: NewsConfig, require_scores: bool = True) -> NewsInputs:
    from mbpe.features.sentiment import scores_path, unique_titles
    from mbpe.sources.gdelt_live import load_coverage, load_news_rows

    rows = load_news_rows()
    path = scores_path(cfg.default_sentiment)
    scores = pl.read_parquet(path) if path.exists() else None
    if require_scores:
        n_titles = unique_titles(rows).height
        if scores is None or scores.height < n_titles:
            have = 0 if scores is None else scores.height
            raise SystemExit(
                f"{cfg.default_sentiment} scores incomplete ({have:,}/{n_titles:,}); run score-all"
            )
    return NewsInputs(prepare(rows, pl.read_parquet(DEDUP_PATH), scores), load_coverage(), load_calendar())


def main() -> None:
    import time

    from mbpe.features.market import nan_report

    cfg = load_news_config()
    t0 = time.perf_counter()
    inp = load_inputs(cfg)
    df = build_news_features(inp.prep, inp.coverage, inp.events, cfg)
    NEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(NEWS_PATH)
    print(
        f"{df.height:,} decision times x {df.width - 1} features ({cfg.default_sentiment}), "
        f"{time.perf_counter() - t0:.0f}s"
    )
    with pl.Config(tbl_rows=80, tbl_cols=12, tbl_width_chars=220, float_precision=4):
        print(nan_report(df).pivot("year", index="feature", values="nan_share"))


if __name__ == "__main__":
    main()
