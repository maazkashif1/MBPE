"""Backfill/live parity: one full day through the live parser vs the BigQuery backfill rows.

Usage: uv run python -m mbpe.sources.gdelt_parity [YYYY-MM-DD]
       (default: the most recent backfilled day where all 96 batches are present)

- downloads the day's 96 English GKG files (cached in data/raw/gdelt_files/), parses them with
  gdelt_live.parse_gkg, and compares with data/raw/gdelt_bq/<year>.parquet for the same DATE range:
  the matched article set (DocumentIdentifier + DATE), titles, tone, source and matched keywords
- runs the generated SQL keyword rule in BigQuery on sample titles (a literal-only query, 0 bytes
  scanned) and compares it with the Python matcher
- writes every difference to data/parity/<day>/ and prints a summary
"""

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from mbpe.sources.gdelt_bq import NORM_SQL, OUT, _client, dry_run_bytes, keywords_sql, match_keywords
from mbpe.sources.gdelt_live import STEP, batch_url, http_get, parse_gkg, unzip_verified

CACHE = Path("data/raw/gdelt_files")
PARITY = Path("data/parity")
FIELDS = ["SourceCommonName", "V2Tone", "title", "keywords"]
SAMPLE_TITLES = [
    ("Bitcoin falls below $80,000", "https://x.com/a"),
    (None, "https://x.com/markets/bitcoin-price-today"),
    ("SEC approves spot bitcoin ETFs", "https://x.com/b"),
    ("SEC fines bank over disclosures", "https://x.com/c"),
    ("Tether mints $1bn of USDT stablecoins", ""),
    ("Federal Reserve holds rates; FOMC statement", "https://x.com/fed"),
    ("US CPI beats forecasts as inflation cools", "https://x.com/d"),
    ("Interest-rate decision looms", "https://x.com/e"),
    ("Cryptography conference opens", "https://x.com/f"),
    ("Untethered: a memoir", "https://x.com/g"),
    ("BTC, crypto and cryptocurrencies rally", "https://x.com/binance-coinbase"),
    ("Le Bitcoin à 100 000 $ — l'été des cryptos", "https://x.fr/éco/bitcoin"),
    ("Weather today", "https://x.com/sec-filings"),
]


def pick_day() -> date:
    cov = pl.read_parquet(OUT / "coverage.parquet").with_columns(day=(pl.col("DATE") // 1_000_000))
    end = max(
        date.fromisoformat(json.loads(p.read_text())["date_range"][1]) for p in OUT.glob("[0-9]*.run.json")
    )
    full = cov.group_by("day").len().filter(pl.col("len") == 96)["day"].sort(descending=True)
    return next(d for d in (datetime.strptime(str(x), "%Y%m%d").date() for x in full) if d < end)


def live_rows(day: date) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    frames, missing, n_rows = [], [], 0
    ts = datetime(day.year, day.month, day.day, tzinfo=UTC)
    for _ in range(96):
        path = CACHE / batch_url(ts).rsplit("/", 1)[1]
        if not path.exists():
            got = http_get(batch_url(ts))
            if got is None:
                missing.append(ts.isoformat())
                ts += STEP
                continue
            path.write_bytes(got[0])
        df, n = parse_gkg(unzip_verified(path.read_bytes()))
        frames.append(df)
        n_rows += n
        ts += STEP
    return pl.concat(frames), {"files": 96 - len(missing), "missing_files": missing, "english_rows": n_rows}


def compare(live: pl.DataFrame, bq: pl.DataFrame) -> dict:
    key = ["DocumentIdentifier", "DATE"]
    out = {
        "live_rows": live.height,
        "bq_rows": bq.height,
        "live_duplicate_keys": live.height - live.select(key).unique().height,
        "bq_duplicate_keys": bq.height - bq.select(key).unique().height,
    }
    only_live = live.join(bq.select(key), on=key, how="anti")
    only_bq = bq.join(live.select(key), on=key, how="anti")
    # rows present on both sides: compare each field (row order within a key made deterministic)
    lv = live.sort(key + FIELDS).with_columns(i=pl.int_range(pl.len()).over(key))
    bv = bq.sort(key + FIELDS).with_columns(i=pl.int_range(pl.len()).over(key))
    both = lv.join(bv, on=key + ["i"], suffix="_bq")
    diffs = both.filter(pl.any_horizontal([pl.col(f).ne_missing(pl.col(f"{f}_bq")) for f in FIELDS]))
    out |= {
        "only_live": only_live.height,
        "only_bq": only_bq.height,
        "matched_keys": both.height,
        "field_diffs": {f: both.filter(pl.col(f).ne_missing(pl.col(f"{f}_bq"))).height for f in FIELDS},
    }
    return out, only_live, only_bq, diffs


def sql_literal_check() -> dict:
    """Run the generated SQL rule on SAMPLE_TITLES in BigQuery (no table: 0 bytes) vs match_keywords."""
    bigquery, client = _client()
    # Python repr() is valid BigQuery string-literal syntax for these samples (quotes, unicode)
    structs = ",\n  ".join(
        f"STRUCT({'NULL' if t is None else repr(t)} AS title, {u!r} AS DocumentIdentifier)"
        for t, u in SAMPLE_TITLES
    )
    sql = f"""WITH s AS (SELECT * FROM UNNEST([
  {structs}
]) WITH OFFSET AS pos), normed AS (SELECT *, {NORM_SQL} AS t FROM s)
SELECT pos, {keywords_sql()} AS keywords FROM normed ORDER BY pos"""
    scanned = dry_run_bytes(client, bigquery, sql)
    if scanned:
        raise SystemExit(f"literal check would scan {scanned} bytes; expected 0")
    got = [list(r["keywords"]) for r in client.query(sql).result()]
    want = [match_keywords(t, u) for t, u in SAMPLE_TITLES]
    mismatches = [(SAMPLE_TITLES[i], g, w) for i, (g, w) in enumerate(zip(got, want, strict=True)) if g != w]
    return {"samples": len(want), "identical": got == want, "bytes": scanned, "mismatches": mismatches}


def main() -> None:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else pick_day()
    lo, hi = int(f"{day:%Y%m%d}000000"), int(f"{day + timedelta(days=1):%Y%m%d}000000")
    bq = pl.read_parquet(OUT / f"{day.year}.parquet").filter(pl.col("DATE").is_between(lo, hi, closed="left"))
    live, files = live_rows(day)
    result, only_live, only_bq, diffs = compare(live, bq)
    out = PARITY / str(day)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in (("only_live", only_live), ("only_bq", only_bq), ("field_diffs", diffs)):
        df.write_parquet(out / f"{name}.parquet")
    summary = {"day": str(day), **files, **result, "sql_literals": sql_literal_check()}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
