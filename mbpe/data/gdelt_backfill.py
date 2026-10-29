import json
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
START = date(2020, 9, 1)
OUT = Path("data/raw/gdelt_bq")
BUDGET_BYTES = 900e9
INCLUDE_THEMES: bool | None = False
REQUIRED_COLUMNS = {
    "DATE",
    "SourceCommonName",
    "DocumentIdentifier",
    "V2Tone",
    "Extras",
    "V2Themes",
    "TranslationInfo",
}

_CRYPTO = r" (?:bitcoins?|cryptos?|cryptocurrenc(?:y|ies)) "
KEYWORDS: dict[str, list[str]] = {
    "bitcoin": [r" bitcoins? "],
    "btc": [r" btc "],
    "crypto": [r" cryptos? "],
    "cryptocurrency": [r" cryptocurrenc(?:y|ies) "],
    "stablecoin": [r" stablecoins? "],
    "tether": [r" tether "],
    "binance": [r" binance "],
    "coinbase": [r" coinbase "],
    "spot_etf": [r" spot (?:bitcoin |btc |ether |ethereum |crypto )?etfs? "],
    "federal_reserve": [r" federal reserve "],
    "fomc": [r" fomc "],
    "cpi": [r" cpi "],
    "inflation": [r" inflation "],
    "interest_rate": [r" interest rates? "],
    "sec_crypto": [r" sec ", _CRYPTO],
}
_COMPILED = {k: [re.compile(p) for p in ps] for k, ps in KEYWORDS.items()}
MACRO_KEYWORDS = {"federal_reserve", "fomc", "cpi", "inflation", "interest_rate"}
CRYPTO_KEYWORDS = set(KEYWORDS) - MACRO_KEYWORDS


def normalize(title: str | None, url: str | None) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", f"{title or ''} {url or ''}".lower()).strip() + " "


def match_keywords(title: str | None, url: str | None) -> list[str]:
    t = normalize(title, url)
    return [k for k, ps in _COMPILED.items() if all(p.search(t) for p in ps)]


def year_ranges(today: date) -> list[tuple[int, date, date]]:
    return [
        (y, max(START, date(y, 1, 1)), min(today, date(y + 1, 1, 1)))
        for y in range(START.year, today.year + 1)
        if max(START, date(y, 1, 1)) < min(today, date(y + 1, 1, 1))
    ]


NORM_SQL = """CONCAT(' ', TRIM(REGEXP_REPLACE(
    LOWER(CONCAT(IFNULL(title, ''), ' ', IFNULL(DocumentIdentifier, ''))), r'[^a-z0-9]+', ' ')), ' ')"""


def keywords_sql() -> str:
    conds = ",\n      ".join(
        "IF(" + " AND ".join(f"REGEXP_CONTAINS(t, r'{p}')" for p in ps) + f", '{k}', NULL)"
        for k, ps in KEYWORDS.items()
    )
    return f"""ARRAY(SELECT k FROM UNNEST([
      {conds}
    ]) AS k WHERE k IS NOT NULL)"""


BACKFILL_SCHEMA = pl.Schema(
    {
        "DATE": pl.Int64,
        "SourceCommonName": pl.String,
        "DocumentIdentifier": pl.String,
        "V2Tone": pl.String,
        "title": pl.String,
        "keywords": pl.List(pl.String),
    }
)


AVAILABILITY_LAG = timedelta(minutes=15)


def batch_time() -> pl.Expr:
    return pl.col("DATE").cast(pl.String).str.strptime(pl.Datetime("ms", "UTC"), "%Y%m%d%H%M%S")


def available_at() -> pl.Expr:
    return (batch_time() + AVAILABILITY_LAG).alias("available_at")


def news_as_of(df: pl.DataFrame, t: datetime) -> pl.DataFrame:
    return df.filter(available_at() <= t)


def build_sql(start: date, end: date, include_themes: bool) -> str:
    themes = ", V2Themes" if include_themes else ""
    return f"""WITH base AS (
  SELECT DATE, SourceCommonName, DocumentIdentifier, V2Tone{themes},
    REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>') AS title
  FROM `{TABLE}`
  WHERE _PARTITIONTIME >= TIMESTAMP('{start - timedelta(days=1)}')
    AND _PARTITIONTIME < TIMESTAMP('{end + timedelta(days=1)}')
    AND DATE >= {start:%Y%m%d}000000 AND DATE < {end:%Y%m%d}000000
    AND (TranslationInfo IS NULL OR TranslationInfo = '')
), normed AS (
  SELECT *, {NORM_SQL} AS t
  FROM base
), tagged AS (
  SELECT * EXCEPT (t), {keywords_sql()} AS keywords
  FROM normed
)
SELECT * FROM tagged WHERE ARRAY_LENGTH(keywords) > 0"""


def _client():
    from google.cloud import bigquery

    return bigquery, bigquery.Client()


def dry_run_bytes(client, bigquery, sql: str) -> int:
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    return client.query(sql, job_config=cfg).total_bytes_processed


def check_schema() -> None:
    _, client = _client()
    cols = {f.name: f.field_type for f in client.get_table(TABLE).schema}
    missing = REQUIRED_COLUMNS - cols.keys()
    print({c: cols.get(c) for c in sorted(REQUIRED_COLUMNS)})
    if missing:
        raise SystemExit(f"columns missing from {TABLE}: {sorted(missing)}")
    print("schema OK")


def dry_run() -> None:
    bigquery, client = _client()
    today = datetime.now(UTC).date()
    for themes in (False, True):
        total = dry_run_bytes(client, bigquery, build_sql(START, today, themes))
        print(f"full range {START} -> {today}, V2Themes={themes}: {total / 1e9:,.1f} GB")
    if INCLUDE_THEMES is not None:
        for year, s, e in year_ranges(today):
            b = dry_run_bytes(client, bigquery, build_sql(s, e, INCLUDE_THEMES))
            print(f"  {year} ({s} -> {e}): {b / 1e9:,.1f} GB")


def _spent() -> int:
    return sum(json.loads(p.read_text())["bytes_processed"] for p in OUT.glob("*.run.json"))


def run() -> None:
    if INCLUDE_THEMES is None:
        raise SystemExit("set INCLUDE_THEMES from the dry run first")
    bigquery, client = _client()
    OUT.mkdir(parents=True, exist_ok=True)
    for year, start, end in year_ranges(datetime.now(UTC).date()):
        record, started, out = OUT / f"{year}.run.json", OUT / f"{year}.started.json", OUT / f"{year}.parquet"
        if record.exists():
            print(f"{year}: done already, skipping")
            continue
        if started.exists():
            meta = json.loads(started.read_text())
            sql = meta["sql"]
            job = client.get_job(meta["job_id"], location=meta["location"])
            print(f"{year}: resuming download of job {job.job_id}")
        else:
            sql = build_sql(start, end, INCLUDE_THEMES)
            estimate = dry_run_bytes(client, bigquery, sql)
            if _spent() + estimate > BUDGET_BYTES:
                raise SystemExit(f"{year}: would exceed the {BUDGET_BYTES / 1e9:.0f} GB budget; stopping")
            cfg = bigquery.QueryJobConfig(maximum_bytes_billed=int(estimate * 1.1) + 10**9)
            job = client.query(sql, job_config=cfg)
            started.write_text(json.dumps({"job_id": job.job_id, "location": job.location, "sql": sql}))
            print(f"{year}: job {job.job_id} submitted, estimate {estimate / 1e9:.1f} GB")
        t0 = time.perf_counter()
        df = pl.from_arrow(job.result().to_arrow(create_bqstorage_client=False))
        tmp = out.with_name(out.name + ".part")
        df.write_parquet(tmp)
        tmp.replace(out)
        record.write_text(
            json.dumps(
                {
                    "year": year,
                    "date_range": [str(start), str(end)],
                    "sql": sql,
                    "job_id": job.job_id,
                    "location": job.location,
                    "job_created": job.created.isoformat(),
                    "job_ended": job.ended.isoformat(),
                    "job_runtime_s": (job.ended - job.started).total_seconds(),
                    "download_s": round(time.perf_counter() - t0, 1),
                    "bytes_processed": job.total_bytes_processed,
                    "bytes_billed": job.total_bytes_billed,
                    "rows": df.height,
                },
                indent=2,
            )
        )
        started.unlink()
        print(f"{year}: {df.height:,} rows, {job.total_bytes_processed / 1e9:.1f} GB processed")
    print(f"total processed: {_spent() / 1e9:.1f} GB")


def report() -> None:
    files = sorted(OUT.glob("[0-9]*.parquet"))
    df = pl.concat([pl.read_parquet(f).with_columns(year=pl.lit(int(f.stem))) for f in files])
    df = df.with_columns(
        ts=pl.col("DATE").cast(pl.String).str.strptime(pl.Datetime("ms", "UTC"), "%Y%m%d%H%M%S")
    )
    with pl.Config(tbl_rows=60, tbl_cols=20, tbl_width_chars=220):
        print(
            df.group_by("year")
            .agg(rows=pl.len(), empty_title=(pl.col("title").fill_null("") == "").mean())
            .sort("year")
        )
        print(
            df.explode("keywords")
            .group_by("year", "keywords")
            .len()
            .pivot("keywords", index="year", values="len")
            .sort("year")
        )
        btc = (
            df.filter(pl.col("keywords").list.contains("bitcoin"))
            .group_by(pl.col("ts").dt.date().alias("day"))
            .len()
        )
        print(
            "bitcoin per day:",
            btc.select(
                max=pl.col("len").max(),
                median=pl.col("len").median(),
                days_over_250=(pl.col("len") > 250).sum(),
            ),
        )
        print("bitcoin busiest day:", btc.sort("len", descending=True).head(1))
        days = pl.date_range(START, df["ts"].max().date(), eager=True).alias("day").to_frame()
        per_day = df.group_by(pl.col("ts").dt.date().alias("day")).agg(
            rows=pl.len(), batches=pl.col("DATE").n_unique()
        )
        cov = days.join(per_day, on="day", how="left").fill_null(0)
        print("days with zero matched rows:", cov.filter(pl.col("rows") == 0)["day"].to_list())
        cov_file = OUT / "coverage.parquet"
        if cov_file.exists():
            to_ts = pl.col("DATE").cast(pl.String).str.strptime(pl.Datetime("ms", "UTC"), "%Y%m%d%H%M%S")
            slots = (
                pl.datetime_range(
                    df["ts"].min().replace(hour=0, minute=15),
                    days["day"].max(),
                    "15m",
                    eager=True,
                    time_unit="ms",
                    time_zone="UTC",
                )
                .alias("ts")
                .to_frame()
            )
            g = (
                slots.join(pl.read_parquet(cov_file).select(ts=to_ts, n_rows="n_rows"), on="ts", how="left")
                .join(df.group_by("ts").agg(matched=pl.len()), on="ts", how="left")
                .with_columns(pl.col("n_rows", "matched").fill_null(0), year=pl.col("ts").dt.year())
            )
            print(
                g.group_by("year")
                .agg(
                    slots=pl.len(),
                    outage=(pl.col("n_rows") == 0).mean(),
                    present_no_match=((pl.col("n_rows") > 0) & (pl.col("matched") == 0)).mean(),
                )
                .sort("year")
            )
            runs = (
                g.with_columns(miss=pl.col("n_rows") == 0)
                .with_columns(run=pl.col("miss").rle_id())
                .filter("miss")
                .group_by("run")
                .agg(start=pl.col("ts").min(), end=pl.col("ts").max(), batches=pl.len())
            )
            long = runs.filter(pl.col("batches") >= 8).sort("batches", descending=True)
            print(f"outage runs: {runs.height}, of which >= 2h: {long.height}")
            print(long.select("start", "end", "batches").head(10))
    print(f"total processed: {_spent() / 1e9:.1f} GB")


def coverage_sql(start: date, end: date) -> str:
    return f"""SELECT DATE, COUNT(*) AS n_rows
FROM `{TABLE}`
WHERE _PARTITIONTIME >= TIMESTAMP('{start - timedelta(days=1)}')
  AND _PARTITIONTIME < TIMESTAMP('{end + timedelta(days=1)}')
  AND DATE >= {start:%Y%m%d}000000 AND DATE < {end:%Y%m%d}000000
GROUP BY DATE"""


def coverage(max_bytes: float = 10e9) -> None:
    bigquery, client = _client()
    record, out = OUT / "coverage.run.json", OUT / "coverage.parquet"
    if record.exists():
        raise SystemExit("coverage already run; not re-running")
    end = max(
        date.fromisoformat(json.loads(p.read_text())["date_range"][1]) for p in OUT.glob("[0-9]*.run.json")
    )
    sql = coverage_sql(START, end)
    estimate = dry_run_bytes(client, bigquery, sql)
    print(f"coverage dry run: {estimate / 1e9:.2f} GB")
    if estimate > max_bytes or _spent() + estimate > BUDGET_BYTES:
        raise SystemExit("coverage query too large; not running")
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=int(estimate * 1.1) + 10**9)
    job = client.query(sql, job_config=cfg)
    df = pl.from_arrow(job.result().to_arrow(create_bqstorage_client=False)).sort("DATE")
    df.write_parquet(out)
    meta = {
        "what": "coverage",
        "date_range": [str(START), str(end)],
        "sql": sql,
        "job_id": job.job_id,
        "location": job.location,
        "job_created": job.created.isoformat(),
        "job_ended": job.ended.isoformat(),
        "bytes_processed": job.total_bytes_processed,
        "bytes_billed": job.total_bytes_billed,
        "rows": df.height,
    }
    record.write_text(json.dumps(meta, indent=2))
    print(f"coverage: {df.height:,} batches, {job.total_bytes_processed / 1e9:.2f} GB processed")


if __name__ == "__main__":
    {"schema": check_schema, "dry-run": dry_run, "run": run, "report": report, "coverage": coverage}[
        sys.argv[1]
    ]()
