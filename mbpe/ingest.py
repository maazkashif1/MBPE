"""Download Binance raw files and rebuild the clean layer.

Usage: uv run python -m mbpe.ingest [START [END]]   (default 2020-09-01 .. today, UTC dates)
"""

import sys
import time
from datetime import UTC, date, datetime

import polars as pl

from mbpe.sources.binance import DATASETS, fetch_funding_fallback, fetch_history
from mbpe.storage.clean import build_clean, gap_report, null_runs


def main() -> None:
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2020, 9, 1)
    end = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.now(UTC).date()
    t_all = time.perf_counter()
    for name in DATASETS:
        t0 = time.perf_counter()
        files = fetch_history(name, start, end)
        if name == "funding":  # archive months not published yet come from the REST API
            files += fetch_funding_fallback(start, end)
        t1 = time.perf_counter()
        df = build_clean(name)
        gaps = gap_report(name, df)
        t2 = time.perf_counter()
        ts = df[DATASETS[name].ts_col]
        print(
            f"{name}: {len(files)} raw files, {df.height:,} rows, {ts.min()} -> {ts.max()}, "
            f"{gaps.height} gaps / {gaps['missing'].sum()} missing rows; "
            f"download {t1 - t0:.0f}s, clean {t2 - t1:.0f}s",
            flush=True,
        )
        if gaps.height:
            print(gaps.sort("length", descending=True).head(10))
        nulls = null_runs(df, DATASETS[name].ts_col)
        if nulls.height:
            print("blank values (rows present, field empty):")
            print(
                nulls.group_by("column")
                .agg(
                    runs=pl.len(),
                    rows=pl.col("rows").sum(),
                    first=pl.col("start").min(),
                    last=pl.col("end").max(),
                    longest_run=pl.col("rows").max(),
                )
                .sort("column")
            )
    print(f"total {time.perf_counter() - t_all:.0f}s")


if __name__ == "__main__":
    main()
