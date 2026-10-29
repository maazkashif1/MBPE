import sys
from pathlib import Path

import polars as pl

from mbpe.data.binance_archive import DATASETS, raw_files
from mbpe.data.clean import clean_path, gap_report
from mbpe.features.market import MARKET_PATH, REGISTRY
from mbpe.features.news import NEWS_PATH, NEWS_REGISTRY

OUT = Path("reports/phase6")
NEW_DATASETS = ("mark_price_1m", "premium_index_1m")
NEW_GROUPS = ("mark_premium", "regulatory")
EVAL = (pl.datetime(2021, 1, 1, time_zone="UTC"), pl.datetime(2026, 4, 26, time_zone="UTC"))


def data_scan() -> pl.DataFrame:
    rows = []
    for name in NEW_DATASETS:
        df = pl.read_parquet(clean_path(name))
        ts = df[DATASETS[name].ts_col]
        g = gap_report(name, df)
        rows.append(
            {
                "dataset": name,
                "raw_files": len(raw_files(name)),
                "rows": df.height,
                "first": str(ts.min()),
                "last": str(ts.max()),
                "gaps": g.height,
                "missing_rows": int(g["missing"].sum()) if g.height else 0,
                "longest_gap_min": int(g["length"].max().total_seconds() // 60) if g.height else 0,
                "longest_gap_start": str(g.sort("length")["start"][-1]) if g.height else "",
            }
        )
    return pl.DataFrame(rows)


def feature_missing() -> pl.DataFrame:
    market = pl.read_parquet(MARKET_PATH)
    news = pl.read_parquet(NEWS_PATH)
    rows = []
    for reg, df in ((REGISTRY, market), (NEWS_REGISTRY, news)):
        w = df.filter(pl.col("decision_time").is_between(*EVAL, closed="left"))
        for n, f in reg.items():
            if f.group in NEW_GROUPS:
                rows.append(
                    {
                        "feature": n,
                        "group": f.group,
                        "hours": w.height,
                        "missing_share": w[n].is_null().mean(),
                    }
                )
    return pl.DataFrame(rows)


def rebuild_identity(old_market: Path, old_news: Path) -> pl.DataFrame:
    rows = []
    for label, old_p, new_p in (("market", old_market, MARKET_PATH), ("news", old_news, NEWS_PATH)):
        old, new = pl.read_parquet(old_p), pl.read_parquet(new_p)
        added = [c for c in new.columns if c not in old.columns]
        missing = [c for c in old.columns if c not in new.columns]
        rows.append(
            {
                "file": label,
                "rows_before": old.height,
                "rows_after": new.height,
                "columns_before": old.width,
                "columns_added": len(added),
                "columns_removed": len(missing),
                "previous_columns_identical": not missing and new.select(old.columns).equals(old),
            }
        )
    return pl.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data_scan().write_csv(OUT / "data_scan.csv")
    feature_missing().write_csv(OUT / "feature_missing.csv")
    rebuild_identity(Path(sys.argv[1]), Path(sys.argv[2])).write_csv(OUT / "rebuild_identity.csv")
    with pl.Config(tbl_cols=12, tbl_width_chars=200):
        for f in ("data_scan", "feature_missing", "rebuild_identity"):
            print(pl.read_csv(OUT / f"{f}.csv"))


if __name__ == "__main__":
    main()
