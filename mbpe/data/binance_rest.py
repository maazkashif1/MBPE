import http.client
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from mbpe.data.binance_archive import DEFAULT_FUNDING_INTERVAL_H, KLINE_COLUMNS, UTC_MS
from mbpe.data.clean import CLEAN_ROOT, METRICS_RATIOS, clean_path, dedupe, validate
from mbpe.features.market import DATASETS, TS_COL

log = logging.getLogger(__name__)
LIVE_ROOT = Path("data/live/binance")
FAPI, SPOT_API = "https://fapi.binance.com", "https://api.binance.com"
UA = {"User-Agent": "mbpe/0.1 (research, paper trading only)"}
MIN, DAY = 60_000, 86_400_000
FUNDING_SCHEMA = {
    "calc_time": pl.Int64,
    "funding_interval_hours": pl.Int64,
    "last_funding_rate": pl.Float64,
    "source": pl.String,
}
MAX_TOP_UP_DAYS = 7
KLINE_URL = {
    "futures_klines_1m": (f"{FAPI}/fapi/v1/klines", 1500),
    "spot_klines_1m": (f"{SPOT_API}/api/v3/klines", 1000),
    "mark_price_1m": (f"{FAPI}/fapi/v1/markPriceKlines", 1500),
    "premium_index_1m": (f"{FAPI}/fapi/v1/premiumIndexKlines", 1500),
}


def get_json(url: str, tries: int = 4):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead, ConnectionError) as e:
            if attempt == tries - 1:
                raise
            log.warning("GET %s failed (%s); retrying", url.split("?")[0], e)
            time.sleep(2 * (attempt + 1))


def klines_frame(rows: list[list], fetched_ms: int) -> pl.DataFrame:
    df = pl.DataFrame(rows, schema=list(KLINE_COLUMNS), orient="row").cast(
        {c: t for c, t in KLINE_COLUMNS.items() if c != "ignore"}, strict=True
    )
    df = df.filter(pl.col("open_time") + MIN <= fetched_ms).drop("ignore")
    return df.with_columns(pl.col("open_time", "close_time").cast(UTC_MS))


OI_REST_OFFSET = 5 * MIN


def metrics_frame(rows: list[dict], fetched_ms: int) -> pl.DataFrame:
    df = pl.DataFrame(
        {
            "create_time": [r["timestamp"] for r in rows],
            "sum_open_interest": [float(r["sumOpenInterest"]) for r in rows],
            "sum_open_interest_value": [float(r["sumOpenInterestValue"]) for r in rows],
        },
        schema={
            "create_time": pl.Int64,
            "sum_open_interest": pl.Float64,
            "sum_open_interest_value": pl.Float64,
        },
    ).filter(pl.col("create_time") <= fetched_ms)
    return df.with_columns(
        (pl.col("create_time") - OI_REST_OFFSET).cast(UTC_MS),
        *[pl.lit(None, pl.Float64).alias(c) for c in METRICS_RATIOS],
    )


def fetch(dataset: str, start_ms: int, end_ms: int, get=get_json, symbol: str = "BTCUSDT") -> pl.DataFrame:
    if dataset in KLINE_URL:
        url, limit = KLINE_URL[dataset]
        rows, cursor = [], start_ms
        while cursor < end_ms:
            page = get(
                f"{url}?symbol={symbol}&interval=1m&startTime={cursor}&endTime={end_ms - 1}&limit={limit}"
            )
            rows += page
            if len(page) < limit:
                break
            cursor = page[-1][0] + MIN
        return klines_frame(rows, end_ms) if rows else klines_frame([], end_ms)
    if dataset == "metrics":
        step, rows = 500 * 5 * MIN, []
        for ws in range(start_ms, end_ms, step):
            we = min(ws + step, end_ms)
            rows += get(
                f"{FAPI}/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&startTime={ws}"
                f"&endTime={we - 1}&limit=500"
            )
        return metrics_frame(rows, end_ms)
    if dataset == "funding":
        rows = get(
            f"{FAPI}/fapi/v1/fundingRate?symbol={symbol}&startTime={start_ms}&endTime={end_ms - 1}&limit=1000"
        )
        info = [x for x in get(f"{FAPI}/fapi/v1/fundingInfo") if x["symbol"] == symbol]
        interval = info[0]["fundingIntervalHours"] if info else DEFAULT_FUNDING_INTERVAL_H
        return pl.DataFrame(
            {
                "calc_time": [r["fundingTime"] for r in rows],
                "funding_interval_hours": [interval] * len(rows),
                "last_funding_rate": [float(r["fundingRate"]) for r in rows],
                "source": ["rest"] * len(rows),
            },
            schema=FUNDING_SCHEMA,
        ).with_columns(pl.col("calc_time").cast(UTC_MS))
    raise ValueError(f"no live source for {dataset}")


def archive_end(dataset: str, clean_root: Path = CLEAN_ROOT) -> int:
    ts = TS_COL[dataset]
    return int(pl.read_parquet(clean_path(dataset, clean_root), columns=[ts])[ts].max().timestamp() * 1000)


def top_up(
    now: datetime | None = None, clean_root: Path = CLEAN_ROOT, live_root: Path = LIVE_ROOT, get=get_json
) -> dict:
    now_ms = int((now or datetime.now(UTC)).timestamp() * 1000)
    live_root.mkdir(parents=True, exist_ok=True)
    report = {}
    for ds in DATASETS:
        arch_end = archive_end(ds, clean_root)
        start = arch_end + 1
        if now_ms - start > MAX_TOP_UP_DAYS * DAY:
            log.warning(
                "%s: archive is %.1f days behind; topping up only the last %d days (run mbpe.data.ingest)",
                ds,
                (now_ms - start) / DAY,
                MAX_TOP_UP_DAYS,
            )
            start = now_ms - MAX_TOP_UP_DAYS * DAY
        df = fetch(ds, start, now_ms, get) if start < now_ms else pl.DataFrame()
        if df.height:
            df = df.filter(pl.col(TS_COL[ds]).dt.epoch("ms") > arch_end)
            df = validate(ds, dedupe(df, TS_COL[ds]))
        out = live_root / f"{ds}.parquet"
        if df.height:
            tmp = out.with_name(out.name + ".part")
            df.write_parquet(tmp)
            tmp.replace(out)
        elif out.exists():
            out.unlink()
        last = df[TS_COL[ds]].max() if df.height else None
        report[ds] = {
            "live_rows": df.height,
            "last": last.isoformat() if last else None,
            "archive_end": datetime.fromtimestamp(arch_end / 1000, UTC).isoformat(),
        }
    return report


def load_live_data(clean_root: Path = CLEAN_ROOT, live_root: Path = LIVE_ROOT) -> dict[str, pl.DataFrame]:
    data = {}
    for ds in DATASETS:
        arch = pl.read_parquet(clean_path(ds, clean_root))
        live_p = live_root / f"{ds}.parquet"
        if live_p.exists():
            ts = TS_COL[ds]
            live = pl.read_parquet(live_p).filter(pl.col(ts) > arch[ts].max())
            arch = pl.concat([arch, live.select(arch.columns)], how="vertical_relaxed")
        data[ds] = arch
    return data


def rest_vs_archive(days: list[str], hours: int = 6, get=get_json) -> pl.DataFrame:
    out = []
    for day in days:
        a = int(datetime.fromisoformat(f"{day}T03:00:00+00:00").timestamp() * 1000)
        b = a + hours * 3_600_000
        for ds in DATASETS:
            ts = TS_COL[ds]
            lo = a - 16 * 3_600_000 if ds == "funding" else a
            rest = fetch(ds, lo, b, get)
            arch = pl.read_parquet(clean_path(ds)).filter(pl.col(ts).dt.epoch("ms").is_between(lo, b - 1))
            if ds == "metrics":
                arch = arch.filter(pl.col(ts).is_between(rest[ts].min(), rest[ts].max()))
            if ds == "funding":
                rest, arch = (x.with_columns(pl.col(ts).dt.round("1m")) for x in (rest, arch))
            cols = [
                c for c in arch.columns if c in rest.columns and c != "source" and c not in METRICS_RATIOS
            ]
            j = arch.select(cols).join(rest.select(cols), on=ts, how="full", coalesce=True, suffix="_rest")
            rows_equal = pl.all_horizontal(
                [(pl.col(c) == pl.col(c + "_rest")).fill_null(False) for c in cols if c != ts]
            )
            out.append(
                {
                    "day": day,
                    "dataset": ds,
                    "archive_rows": arch.height,
                    "rest_rows": rest.height,
                    "rows_identical": int(j.select(rows_equal.sum()).item()),
                }
            )
    return pl.DataFrame(out)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "check" in sys.argv:
        rep = rest_vs_archive(["2026-10-05", "2026-10-12", "2026-10-20", "2026-10-24", "2026-10-25"])
        Path("reports/phase6/live").mkdir(parents=True, exist_ok=True)
        rep.write_csv("reports/phase6/live/rest_vs_archive.csv")
        print(rep)
    else:
        print(json.dumps(top_up(), indent=1))
