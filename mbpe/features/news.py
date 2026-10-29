from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.data.gdelt_backfill import AVAILABILITY_LAG, CRYPTO_KEYWORDS, MACRO_KEYWORDS
from mbpe.features.market import DAY, HOUR, MIN, UTC_MS, Feature, safe_div
from mbpe.features.news_dedup import NewsConfig, load_news_config, normalize_title

NEWS_PATH = Path("data/features/news.parquet")
DEDUP_PATH = Path("data/features/news_dedup.parquet")
CALENDAR_PATH = Path("data/reference/macro_events.csv")
BATCH = 15 * MIN
LAG = int(AVAILABILITY_LAG.total_seconds() * 1000)
GROUPS = ("crypto", "macro")
WINDOWS = {"1h": HOUR, "4h": 4 * HOUR, "24h": DAY}
NEWS_INPUTS = ("gdelt_rows", "gdelt_coverage", "news_dedup", "sentiment")
CAL_INPUTS = ("macro_events",)
REGULATORY_PATTERN = (
    r"\b(?:SEC|CFTC|ETFs?)\b|(?i:\b(?:regulators?|regulatory|regulations?|regulated?|regulates|lawsuits?|sues?|sued|"
    r"indict(?:ed|ment)?|fined|bans?|banned|crackdown|enforcement|legislation|lawmakers|congress|senate|"
    r"approv(?:al|e|es|ed)|sanctions?|sanctioned|licen[cs]es?|licen[cs]ed|subpoenas?)\b)"
)
MAJOR_PATTERN = (
    r"(?i:\b(?:hacks?|hacked|exploits?|exploited|breach(?:ed)?|stolen|halving|bankrupt(?:cy)?|insolven(?:cy|t)|"
    r"delist(?:s|ed|ing)?|collapses?|collapsed|outages?|depegs?|depegged)\b)"
)


def label_ms(col: str = "DATE") -> pl.Expr:
    return pl.col(col).cast(pl.String).str.strptime(UTC_MS, "%Y%m%d%H%M%S").dt.epoch("ms")


def prepare(rows: pl.DataFrame, clusters: pl.DataFrame, scores: pl.DataFrame | None) -> pl.DataFrame:
    key = ["DATE", "DocumentIdentifier"]
    df = rows.join(clusters.select(*key, "cluster_id", "match_sim"), on=key, how="left")
    if (missing := df["cluster_id"].null_count()) > 0:
        raise ValueError(f"{missing} rows have no dedup cluster; re-run mbpe.features.news_dedup")
    df = df.with_columns(
        avail=label_ms() + LAG,
        crypto=pl.col("keywords").list.eval(pl.element().is_in(list(CRYPTO_KEYWORDS))).list.any(),
        macro=pl.col("keywords").list.eval(pl.element().is_in(list(MACRO_KEYWORDS))).list.any(),
        norm=pl.col("title").map_elements(normalize_title, return_dtype=pl.String),
        new_cluster=pl.col("match_sim").is_null(),
        tone=pl.col("V2Tone").str.split(",").list.first().cast(pl.Float64, strict=False),
    ).with_columns(
        empty=pl.col("norm") == "",
        reg=pl.col("crypto") & pl.col("title").str.contains(REGULATORY_PATTERN),
        major=pl.col("crypto") & pl.col("title").str.contains(MAJOR_PATTERN),
    )
    if scores is not None:
        df = df.join(scores.select("norm", "score"), on="norm", how="left")
    else:
        df = df.with_columns(score=pl.lit(None, pl.Float64))
    df = df.with_columns(
        score=pl.when(pl.col("empty")).then(None).otherwise("score"),
        tone=pl.when(pl.col("empty")).then(None).otherwise("tone"),
    )
    return df.select(
        "avail", "crypto", "macro", "reg", "major", "empty", "new_cluster", "cluster_id", "score", "tone"
    ).sort("avail")


def load_calendar(path: Path = CALENDAR_PATH, events: tuple[str, ...] = ("CPI", "FOMC")) -> np.ndarray:
    ev = pl.read_csv(path).filter(pl.col("event").is_in(list(events)) & (pl.col("release_utc").is_not_null()))
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
        self.flag = {f: prep[f].to_numpy() for f in ("reg", "major")}
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
        return x_hours[self.pos]

    def rows_in(self, ends: np.ndarray, length: int) -> tuple[np.ndarray, np.ndarray]:
        return np.searchsorted(self.a, ends - length, "right"), np.searchsorted(self.a, ends, "right")

    def count(self, x: np.ndarray, length: int, ends: np.ndarray | None = None) -> np.ndarray:
        ends = self.grid if ends is None else ends
        p = np.concatenate([[0], np.cumsum(x)])
        lo, hi = self.rows_in(ends, length)
        return (p[hi] - p[lo]).astype(np.float64)

    def coverage(self, length: int, ends: np.ndarray | None = None) -> np.ndarray:
        ends = self.grid if ends is None else ends
        n = np.searchsorted(self.batch_avail, ends, "right") - np.searchsorted(
            self.batch_avail, ends - length, "right"
        )
        return n / (length / BATCH)

    def covered(self, length: int) -> np.ndarray:
        return self.coverage(length) >= self.cfg.min_coverage

    def cluster_stats(self, g: str, length: int, values: str) -> dict[str, np.ndarray]:
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
        c = ctx.count(ctx.mask[g], 4 * HOUR, ends=ctx.hours)
        cov = ctx.coverage(4 * HOUR, ends=ctx.hours)
        x = np.where(cov >= ctx.cfg.min_coverage, safe_div(c, cov), np.nan)
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


for _f, _ws in (("reg", ("1h", "4h", "24h")), ("major", ("4h", "24h"))):
    for _w in _ws:
        register(f"{_f}_new_clusters_{_w}", "regulatory", NEWS_INPUTS)(
            lambda ctx, f=_f, n=WINDOWS[_w]: _gated(ctx, ctx.count(ctx.flag[f] & ctx.new, n), n)
        )
register("reg_share_24h", "regulatory", NEWS_INPUTS)(
    lambda ctx: _gated(
        ctx,
        safe_div(ctx.count(ctx.flag["reg"] & ctx.new, DAY), ctx.count(ctx.mask["crypto"] & ctx.new, DAY)),
        DAY,
    )
)


def _next_event(ctx: NewsCtx) -> np.ndarray:
    i = np.searchsorted(ctx.events, ctx.grid, "right")
    ok = i < len(ctx.events)
    return np.where(ok, (ctx.events[np.minimum(i, len(ctx.events) - 1)] - ctx.grid) / HOUR, np.nan)


@register("cal_hours_to_next_event", "calendar", CAL_INPUTS)
def cal_hours_to_next_event(ctx: NewsCtx) -> np.ndarray:
    return _next_event(ctx)


@register("cal_hours_since_last_event", "calendar", CAL_INPUTS)
def cal_hours_since_last_event(ctx: NewsCtx) -> np.ndarray:
    i = np.searchsorted(ctx.events, ctx.grid, "right") - 1
    return np.where(i >= 0, (ctx.grid - ctx.events[np.maximum(i, 0)]) / HOUR, np.nan)


@register("cal_event_next_4h", "calendar", CAL_INPUTS)
def cal_event_next_4h(ctx: NewsCtx) -> np.ndarray:
    nxt = _next_event(ctx)
    return np.where(np.isfinite(nxt), (nxt <= 4).astype(np.float64), np.nan)


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


def load_inputs(cfg: NewsConfig, require_scores: bool = True, dedup_path: Path = DEDUP_PATH) -> NewsInputs:
    from mbpe.data.gdelt_live import load_coverage, load_news_rows
    from mbpe.features.sentiment import scores_path, unique_titles

    clusters = pl.read_parquet(dedup_path)
    last = clusters["DATE"].max()
    rows = load_news_rows().filter(pl.col("DATE") <= last)
    coverage = load_coverage().filter(pl.col("DATE") <= last)
    path = scores_path(cfg.default_sentiment)
    scores = pl.read_parquet(path) if path.exists() else None
    if require_scores:
        n_titles = unique_titles(rows).height
        if scores is None or scores.height < n_titles:
            have = 0 if scores is None else scores.height
            raise SystemExit(
                f"{cfg.default_sentiment} scores incomplete ({have:,}/{n_titles:,}); run score-all"
            )
    return NewsInputs(prepare(rows, clusters, scores), coverage, load_calendar())


def main() -> None:
    import sys
    import time

    from mbpe.features.market import nan_report

    cfg = load_news_config(Path(sys.argv[1])) if len(sys.argv) > 1 else load_news_config()
    t0 = time.perf_counter()
    inp = load_inputs(cfg)
    df = build_news_features(inp.prep, inp.coverage, inp.events, cfg)
    cfg.news_output.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(cfg.news_output)
    print(
        f"{df.height:,} decision times x {df.width - 1} features ({cfg.default_sentiment}), "
        f"{time.perf_counter() - t0:.0f}s"
    )
    with pl.Config(tbl_rows=80, tbl_cols=12, tbl_width_chars=220, float_precision=4):
        print(nan_report(df).pivot("year", index="feature", values="nan_share"))


if __name__ == "__main__":
    main()
