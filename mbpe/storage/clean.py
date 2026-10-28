"""Clean layer: raw Binance files -> one validated Parquet per dataset under data/clean/binance/.

- timestamps are UTC datetimes (ms)
- exact duplicate rows are dropped; two different rows with the same timestamp raise
- each dataset is validated with a pandera schema
- no gap filling: gaps stay gaps (see gap_report)
"""

from datetime import timedelta
from pathlib import Path

import pandera.polars as pa
import polars as pl

from mbpe.sources.binance import DATASETS, RAW_ROOT, REST_ROOT, UTC_MS, raw_files, read_funding_rest, read_raw

CLEAN_ROOT = Path("data/clean/binance")


class ConflictingDuplicatesError(ValueError):
    """Two rows share a timestamp but differ in some value: a real data problem."""


def dedupe(df: pl.DataFrame, ts_col: str) -> pl.DataFrame:
    """Drop exact duplicate rows, raise on conflicting ones, return sorted by timestamp."""
    df = df.unique(maintain_order=True)
    conflicts = df.filter(pl.col(ts_col).is_duplicated()).sort(ts_col)
    if conflicts.height:
        raise ConflictingDuplicatesError(
            f"{conflicts[ts_col].n_unique()} timestamps have conflicting rows, e.g.\n{conflicts.head(6)}"
        )
    return df.sort(ts_col)


# ---------- schemas ----------

_increasing = pa.Check(
    lambda d: d.lazyframe.select((pl.col(d.key).diff() > timedelta(0)).fill_null(True)),
    name="strictly_increasing",  # implies sorted and unique
    error="timestamps must be unique and sorted",
)
_nonneg = pa.Check.ge(0)


def _ts(name: str) -> dict:
    return {DATASETS[name].ts_col: pa.Column(UTC_MS, _increasing)}


_KLINE = {
    **{
        c: pa.Column(pl.Float64, _nonneg)
        for c in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "taker_buy_volume",
            "taker_buy_quote_volume",
        )
    },
    "close_time": pa.Column(UTC_MS),
    "count": pa.Column(pl.Int64, _nonneg),
}

METRICS_RATIOS = (
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)

SCHEMAS = {
    "futures_klines_1m": pa.DataFrameSchema({**_ts("futures_klines_1m"), **_KLINE}, strict=True),
    "spot_klines_1m": pa.DataFrameSchema({**_ts("spot_klines_1m"), **_KLINE}, strict=True),
    "funding": pa.DataFrameSchema(
        {
            **_ts("funding"),
            "funding_interval_hours": pa.Column(pl.Int64, pa.Check.gt(0)),
            "last_funding_rate": pa.Column(pl.Float64),  # can be negative
            "source": pa.Column(pl.String, pa.Check.isin(["archive", "rest"])),
        },
        strict=True,
    ),
    "metrics": pa.DataFrameSchema(
        {
            **_ts("metrics"),
            "sum_open_interest": pa.Column(pl.Float64, _nonneg),
            "sum_open_interest_value": pa.Column(pl.Float64, _nonneg),
            # Binance leaves these blank for long stretches (top-trader ratios ~Dec 2021 - Dec 2022,
            # taker ratio ~Dec 2021 - May 2022): blanks are kept as nulls, see null_runs()
            **{c: pa.Column(pl.Float64, _nonneg, nullable=True) for c in METRICS_RATIOS},
        },
        strict=True,
    ),
}


BOOK_BANDS = (0.2, 1.0, 2.0, 3.0, 4.0, 5.0)  # % from mid; bid side = negative percentage in the raw files
BOOK_DEPTH_MIN_BYTES = 2048  # smaller raw files count as missing days (SCOPE.md, Phase 4)


def book_col(kind: str, side: str, band: float) -> str:
    """e.g. book_col("depth", "bid", 0.2) -> "bid_depth_20bp" (cumulative depth from mid to the band)."""
    return f"{side}_{kind}_{round(band * 100)}bp"


BOOK_COLUMNS = [book_col(k, s, b) for k in ("depth", "notional") for s in ("bid", "ask") for b in BOOK_BANDS]
SCHEMAS["book_depth"] = pa.DataFrameSchema(
    {**_ts("book_depth"), **{c: pa.Column(pl.Float64, _nonneg, nullable=True) for c in BOOK_COLUMNS}},
    strict=True,
)


def book_depth_wide(long: pl.DataFrame) -> pl.DataFrame:
    """12 rows per snapshot -> one row per snapshot; a band absent from a snapshot stays null.

    Exact duplicate rows are dropped; two different values for the same (timestamp, band) raise.
    """
    long = long.unique(maintain_order=True)
    dup = long.filter(pl.struct("timestamp", "percentage").is_duplicated())
    if dup.height:
        raise ConflictingDuplicatesError(f"{dup.height} conflicting book_depth rows, e.g.\n{dup.head(6)}")
    unknown = set(long["percentage"].abs().unique().to_list()) - set(BOOK_BANDS)
    if unknown:
        raise ValueError(f"unexpected book_depth bands: {sorted(unknown)}")
    named = long.with_columns(
        side=pl.when(pl.col("percentage") < 0).then(pl.lit("bid")).otherwise(pl.lit("ask")),
        band=(pl.col("percentage").abs() * 100).round().cast(pl.Int64),
    )
    wide = named.pivot(on=["side", "band"], index="timestamp", values=["depth", "notional"], separator="|")
    rename = {}
    for c in wide.columns[1:]:  # e.g. 'depth|{"bid",20}' (polars names multi-key pivot columns this way)
        kind, key = c.split("|", 1)
        side, band = key.strip("{}").replace('"', "").split(",")
        rename[c] = f"{side}_{kind}_{band}bp"
    wide = wide.rename(rename)
    for c in BOOK_COLUMNS:
        if c not in wide.columns:
            wide = wide.with_columns(pl.lit(None, pl.Float64).alias(c))
    return wide.select("timestamp", *BOOK_COLUMNS).sort("timestamp")


def validate(name: str, df: pl.DataFrame) -> pl.DataFrame:
    return SCHEMAS[name].validate(df, lazy=True)  # lazy: report every failure, not just the first


# ---------- build ----------


def clean_path(name: str, root: Path = CLEAN_ROOT) -> Path:
    return root / f"{name}.parquet"


class FundingMismatchError(ValueError):
    """REST funding values disagree with the archive month that has since been published."""


def merge_funding_rest(archive: pl.DataFrame, rest_files: list[Path]) -> pl.DataFrame:
    """Archive funding plus REST months the archive doesn't have yet, tagged by source.

    A REST month whose archive file now exists must match it exactly (timestamps and rates), else raise.
    """
    archive = archive.with_columns(source=pl.lit("archive"))
    have = set(archive["calc_time"].dt.strftime("%Y-%m").unique().to_list())
    frames = [archive]
    for f in sorted(rest_files):
        month = f.stem[-7:]  # BTCUSDT-fundingRate-YYYY-MM
        rest = read_funding_rest(f)
        if month in have:
            a = archive.filter(pl.col("calc_time").dt.strftime("%Y-%m") == month)
            j = a.join(rest, on="calc_time", how="full", coalesce=True, suffix="_rest")
            bad = j.filter(
                pl.col("last_funding_rate").is_null()
                | pl.col("last_funding_rate_rest").is_null()
                | (pl.col("last_funding_rate") != pl.col("last_funding_rate_rest"))
            )
            if bad.height:
                raise FundingMismatchError(
                    f"{month}: {bad.height} REST rows differ from the archive, e.g.\n{bad.head(5)}"
                )
        else:
            frames.append(rest.with_columns(source=pl.lit("rest")))
    return pl.concat(frames)


def build_clean(
    name: str, raw_root: Path = RAW_ROOT, clean_root: Path = CLEAN_ROOT, rest_root: Path = REST_ROOT
) -> pl.DataFrame:
    """Rebuild the clean Parquet for `name` from all stored raw files (funding: + REST fallback months)."""
    files = raw_files(name, raw_root)
    if name == "book_depth":  # near-empty files are missing days (SCOPE.md, Phase 4)
        files = [f for f in files if f.stat().st_size >= BOOK_DEPTH_MIN_BYTES]
    if not files:
        raise FileNotFoundError(f"no raw files for {name} under {raw_root}")
    df = pl.concat([read_raw(name, f) for f in files])
    if name == "funding":
        df = merge_funding_rest(df, sorted(rest_root.glob("*.json")))
    if name == "book_depth":
        df = book_depth_wide(df)
    df = validate(name, dedupe(df, DATASETS[name].ts_col))
    out = clean_path(name, clean_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    df.write_parquet(tmp)
    tmp.replace(out)
    return df


# ---------- gaps ----------


def gap_report(name: str, df: pl.DataFrame | None = None) -> pl.DataFrame:
    """Every gap in a dataset's timestamps.

    A gap is any step longer than 1.5x the nominal spacing (1m klines, 5m metrics, 8h funding;
    the 1.5x tolerates funding's millisecond jitter). Columns:
      start   = last timestamp before the gap
      end     = first timestamp after the gap
      length  = end - start
      missing = rows absent on the nominal grid (length / step - 1)
    """
    ds = DATASETS[name]
    if df is None:
        df = pl.read_parquet(clean_path(name))
    step_ms = ds.step.total_seconds() * 1000
    return (
        df.select(start=pl.col(ds.ts_col).shift(1), end=pl.col(ds.ts_col))
        .with_columns(length=pl.col("end") - pl.col("start"))
        .filter(pl.col("length") > ds.step * 1.5)
        .with_columns(missing=(pl.col("length").dt.total_milliseconds() / step_ms).round().cast(pl.Int64) - 1)
    )


def null_runs(df: pl.DataFrame, ts_col: str, columns: list[str] | None = None) -> pl.DataFrame:
    """Field-level gaps: every run of consecutive null values per column.

    Rows exist but a value is blank. Columns: column, start, end (first/last null timestamp), rows.
    """
    columns = columns or [c for c in df.columns if c != ts_col]
    frames = [
        df.select(pl.col(ts_col), null=pl.col(c).is_null())
        .with_columns(run=pl.col("null").rle_id())
        .filter("null")
        .group_by("run")
        .agg(start=pl.col(ts_col).min(), end=pl.col(ts_col).max(), rows=pl.len())
        .select(pl.lit(c).alias("column"), "start", "end", "rows")
        for c in columns
    ]
    return pl.concat(frames).sort("column", "start")
