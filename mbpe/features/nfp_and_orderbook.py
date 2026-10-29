from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.data.clean import BOOK_BANDS, book_col, clean_path
from mbpe.features.market import HOUR, MARKET_PATH, MIN, UTC_MS, safe_div
from mbpe.features.news import load_calendar

BOOK_COLUMNS_DEPTH = [book_col("depth", s, b) for s in ("bid", "ask") for b in BOOK_BANDS]

PHASE4_PATH = Path("data/features/phase4.parquet")
BOOK_STALE = 2 * MIN
MEDIAN_HOURS, MEDIAN_MIN = 168, 84


class P4Ctx:
    def __init__(self, book: pl.DataFrame, nfp: np.ndarray, grid: np.ndarray):
        book = book.sort("timestamp")
        self.bt = book["timestamp"].cast(UTC_MS).dt.epoch("ms").to_numpy()
        self.book = {c: book[c].fill_null(np.nan).to_numpy() for c in book.columns if c != "timestamp"}
        self.nfp, self.grid = nfp, np.asarray(grid, dtype=np.int64)
        self.memo: dict = {}

    def snap(self, at: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        i = np.searchsorted(self.bt, at, "left") - 1
        ii = np.maximum(i, 0)
        return ii, (i >= 0) & (at - self.bt[ii] <= BOOK_STALE)

    def at(self, col: str, at: np.ndarray) -> np.ndarray:
        if not len(self.bt):
            return np.full(len(at), np.nan)
        i, ok = self.snap(at)
        return np.where(ok, self.book[col][i], np.nan)

    def total(self, at: np.ndarray) -> np.ndarray:
        return self.at(book_col("depth", "bid", 5.0), at) + self.at(book_col("depth", "ask", 5.0), at)


@dataclass(frozen=True)
class P4Feature:
    name: str
    group: str
    fn: Callable[[P4Ctx], np.ndarray]


PHASE4_REGISTRY: dict[str, P4Feature] = {}


def register(name: str, group: str):
    def deco(fn):
        if name in PHASE4_REGISTRY:
            raise ValueError(f"duplicate feature {name}")
        PHASE4_REGISTRY[name] = P4Feature(name, group, fn)
        return fn

    return deco


def _next_nfp(ctx: P4Ctx) -> np.ndarray:
    i = np.searchsorted(ctx.nfp, ctx.grid, "right")
    ok = i < len(ctx.nfp)
    return np.where(ok, (ctx.nfp[np.minimum(i, len(ctx.nfp) - 1)] - ctx.grid) / HOUR, np.nan)


register("nfp_hours_to_next", "nfp")(_next_nfp)


@register("nfp_hours_since_last", "nfp")
def nfp_hours_since_last(ctx: P4Ctx) -> np.ndarray:
    i = np.searchsorted(ctx.nfp, ctx.grid, "right") - 1
    return np.where(i >= 0, (ctx.grid - ctx.nfp[np.maximum(i, 0)]) / HOUR, np.nan)


@register("nfp_next_4h", "nfp")
def nfp_next_4h(ctx: P4Ctx) -> np.ndarray:
    nxt = _next_nfp(ctx)
    return np.where(np.isfinite(nxt), (nxt <= 4).astype(np.float64), np.nan)


for _b in BOOK_BANDS:
    register(f"book_imbalance_{round(_b * 100)}bp", "orderbook")(
        lambda ctx, b=_b: safe_div(
            ctx.at(book_col("depth", "bid", b), ctx.grid) - ctx.at(book_col("depth", "ask", b), ctx.grid),
            ctx.at(book_col("depth", "bid", b), ctx.grid) + ctx.at(book_col("depth", "ask", b), ctx.grid),
        )
    )


@register("book_depth_chg_1h", "orderbook")
def book_depth_chg_1h(ctx: P4Ctx) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(ctx.total(ctx.grid) / ctx.total(ctx.grid - HOUR))


@register("book_depth_vs_7d_median", "orderbook")
def book_depth_vs_7d_median(ctx: P4Ctx) -> np.ndarray:
    lags = ctx.grid[:, None] - HOUR * np.arange(1, MEDIAN_HOURS + 1)[None, :]
    past = ctx.total(lags.ravel()).reshape(lags.shape)
    n = np.isfinite(past).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        med = np.where(n >= MEDIAN_MIN, np.nanmedian(np.where(n[:, None] > 0, past, 0.0), axis=1), np.nan)
        return np.log(ctx.total(ctx.grid) / med)


def load_book(path: Path | None = None) -> pl.DataFrame:
    return pl.read_parquet(path or clean_path("book_depth"))


def build_phase4_features(book: pl.DataFrame, nfp: np.ndarray, grid: np.ndarray) -> pl.DataFrame:
    ctx = P4Ctx(book, nfp, grid)
    cols = {n: np.asarray(f.fn(ctx), dtype=np.float64) for n, f in PHASE4_REGISTRY.items()}
    out = pl.DataFrame({"decision_time": pl.Series(ctx.grid).cast(UTC_MS), **cols})
    return out.with_columns(pl.col(list(cols)).fill_nan(None))


def coverage(p4: pl.DataFrame) -> pl.DataFrame:
    from mbpe.features.market import REGISTRY

    macd = [n for n, f in REGISTRY.items() if f.group == "macd_ema"]
    df = pl.read_parquet(MARKET_PATH, columns=["decision_time", *macd]).join(p4, on="decision_time")
    rows = []
    for name, group in [
        *[(n, "macd_ema") for n in macd],
        *[(n, f.group) for n, f in PHASE4_REGISTRY.items()],
    ]:
        start = (2023, 1, 1) if group == "orderbook" else (2021, 1, 1)
        w = df.filter(
            (pl.col("decision_time") >= pl.datetime(*start, time_zone="UTC"))
            & (pl.col("decision_time") < pl.datetime(2026, 4, 26, time_zone="UTC"))
        )
        rows.append(
            {
                "feature": name,
                "group": group,
                "window_start": "-".join(f"{x:02d}" for x in start),
                "hours": w.height,
                "missing_share": w[name].is_null().mean(),
            }
        )
    return pl.DataFrame(rows)


def scan(out: Path = Path("reports/phase4")) -> dict:
    import json
    from datetime import date, timedelta

    from mbpe.data.binance_archive import raw_files
    from mbpe.data.clean import BOOK_DEPTH_MIN_BYTES

    files = raw_files("book_depth")

    def day(f):
        return f.stem.rsplit("-", 3)[-3:]

    have = {"-".join(day(f)): f.stat().st_size for f in files}
    first, last = date.fromisoformat(min(have)), date.fromisoformat(max(have))
    days = [(first + timedelta(n)).isoformat() for n in range((last - first).days + 1)]
    book = load_book()
    t = book["timestamp"].dt.epoch("ms").to_numpy()
    step = np.diff(t)
    snap_days = set(book["timestamp"].dt.date().cast(pl.String).unique().to_list())
    gaps = (
        pl.DataFrame({"start": t[:-1], "end": t[1:], "seconds": step / 1000})
        .filter(pl.col("seconds") > BOOK_STALE / 1000)
        .with_columns(pl.col("start", "end").cast(UTC_MS))
        .sort("seconds", descending=True)
    )
    bands = {c: str(book.filter(pl.col(c).is_not_null())["timestamp"].min()) for c in BOOK_COLUMNS_DEPTH}
    grid = np.arange(int(datetime_ms(2023, 1, 1)), int(datetime_ms(2026, 4, 26)), HOUR)
    _, fresh = P4Ctx(book, np.zeros(0), grid).snap(grid)
    res = {
        "first_day": str(first),
        "last_day": str(last),
        "days": len(days),
        "raw_files": len(have),
        "missing_days": [d for d in days if d not in have],
        "small_files": {d: n for d, n in have.items() if n < BOOK_DEPTH_MIN_BYTES},
        "days_without_snapshots": [d for d in days if d not in snap_days],
        "snapshots": len(t),
        "median_step_s": float(np.median(step) / 1000),
        "steps_over_45s": int((step > 45_000).sum()),
        "gaps_over_2min": gaps.height,
        "gap_hours_over_2min": float(gaps["seconds"].sum() / 3600),
        "band_first_seen": bands,
        "hours_2023_to_holdout": len(grid),
        "hours_with_fresh_snapshot": int(fresh.sum()),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "book_depth_scan.json").write_text(json.dumps(res, indent=1))
    gaps.write_csv(out / "book_depth_gaps.csv")
    return res


def datetime_ms(y: int, m: int, d: int) -> int:
    from datetime import UTC, datetime

    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def main() -> None:
    import sys

    if "scan" in sys.argv:
        print(scan())
        return
    grid = pl.read_parquet(MARKET_PATH, columns=["decision_time"])["decision_time"].dt.epoch("ms").to_numpy()
    out = []
    for chunk in np.array_split(grid, max(1, len(grid) // 5000)):
        out.append(build_phase4_features(load_book(), load_calendar(events=("NFP",)), chunk))
    df = pl.concat(out)
    df.write_parquet(PHASE4_PATH)
    coverage(df).write_csv(Path("reports/phase4/feature_missing.csv"))
    since = df.filter(pl.col("decision_time") >= pl.datetime(2023, 1, 8, time_zone="UTC"))
    print(f"{df.height:,} decision times x {df.width - 1} features -> {PHASE4_PATH}")
    print("missing share since 2023-01-08:")
    print(since.select(pl.all().exclude("decision_time").is_null().mean()).unpivot().sort("value"))


if __name__ == "__main__":
    main()
