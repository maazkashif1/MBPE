import sys
import time
from datetime import UTC, date, datetime

import polars as pl

from mbpe.data.binance_archive import DATASETS, fetch_funding_fallback, fetch_history
from mbpe.data.clean import build_clean, gap_report, null_runs


def main() -> None:
    args = sys.argv[1:]
    only = [args.pop(args.index("--only") + 1)] if "--only" in args else list(DATASETS)
    args = [a for a in args if a != "--only"]
    start = date.fromisoformat(args[0]) if args else date(2020, 9, 1)
    end = date.fromisoformat(args[1]) if len(args) > 1 else datetime.now(UTC).date()
    t_all = time.perf_counter()
    for name in only:
        t0 = time.perf_counter()
        files = fetch_history(name, start, end)
        if name == "funding":
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
