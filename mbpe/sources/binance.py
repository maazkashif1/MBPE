"""Binance public-archive adapter (data.binance.vision), BTCUSDT only.

fetch_history() mirrors raw zip files under data/raw/binance/<same path as the archive>.
It is resumable (files already stored are skipped) and verifies each zip against its
.CHECKSUM file when Binance publishes one. Raw files are written once and never modified.

read_raw() / parse_csv() turn one raw file into a typed polars DataFrame with UTC timestamps.
"""

import hashlib
import http.client
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

BASE = "https://data.binance.vision/data"
RAW_ROOT = Path("data/raw/binance")
UA = {"User-Agent": "mbpe/0.1"}
UTC_MS = pl.Datetime("ms", "UTC")


@dataclass(frozen=True)
class Dataset:
    path: str  # archive path relative to BASE; {p} = day (YYYY-MM-DD) or month (YYYY-MM)
    monthly: bool
    ts_col: str
    step: timedelta  # nominal spacing between rows
    columns: dict  # raw CSV columns -> polars dtype, in file order


KLINE_COLUMNS = {
    "open_time": pl.Int64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "close_time": pl.Int64,
    "quote_volume": pl.Float64,
    "count": pl.Int64,
    "taker_buy_volume": pl.Float64,
    "taker_buy_quote_volume": pl.Float64,
    "ignore": pl.String,
}

DATASETS = {
    "futures_klines_1m": Dataset(
        "futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{p}.zip",
        False,
        "open_time",
        timedelta(minutes=1),
        KLINE_COLUMNS,
    ),
    "spot_klines_1m": Dataset(
        "spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{p}.zip",
        False,
        "open_time",
        timedelta(minutes=1),
        KLINE_COLUMNS,
    ),
    "funding": Dataset(
        "futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{p}.zip",
        True,
        "calc_time",
        timedelta(hours=8),
        {"calc_time": pl.Int64, "funding_interval_hours": pl.Int64, "last_funding_rate": pl.Float64},
    ),
    "metrics": Dataset(
        "futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{p}.zip",
        False,
        "create_time",
        timedelta(minutes=5),
        {
            "create_time": pl.String,
            "symbol": pl.String,
            "sum_open_interest": pl.Float64,
            "sum_open_interest_value": pl.Float64,
            "count_toptrader_long_short_ratio": pl.Float64,
            "sum_toptrader_long_short_ratio": pl.Float64,
            "count_long_short_ratio": pl.Float64,
            "sum_taker_long_short_vol_ratio": pl.Float64,
        },
    ),
    # order book depth snapshots (~every 30 s): 12 rows per snapshot, one per band (+-0.2, 1..5 % from mid);
    # pivoted to one row per snapshot in the clean layer
    "book_depth": Dataset(
        "futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-{p}.zip",
        False,
        "timestamp",
        timedelta(seconds=30),
        {"timestamp": pl.String, "percentage": pl.Float64, "depth": pl.Float64, "notional": pl.Float64},
    ),
}
DROP_COLUMNS = ["ignore", "symbol"]  # constant / unused


def periods(name: str, start: date, end: date) -> list[str]:
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    if DATASETS[name].monthly:
        return sorted({d.strftime("%Y-%m") for d in days})
    return [d.isoformat() for d in days]


def raw_path(name: str, period: str, root: Path = RAW_ROOT) -> Path:
    return root / DATASETS[name].path.format(p=period)


def raw_files(name: str, root: Path = RAW_ROOT) -> list[Path]:
    return sorted(root.glob(DATASETS[name].path.format(p="*")))


# ---------- download ----------


def _get(url: str) -> bytes | None:
    """GET with retries on transient errors; None on 404 (not published, or not published yet)."""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException):
            if attempt == 3:
                raise
        time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def _fetch_one(name: str, period: str, root: Path) -> Path | None:
    dest = raw_path(name, period, root)
    if dest.exists():  # written only after verification, so existing == complete
        return dest
    url = f"{BASE}/{DATASETS[name].path.format(p=period)}"
    for attempt in range(3):
        blob = _get(url)
        if blob is None:
            return None
        checksum = _get(url + ".CHECKSUM")
        if checksum is None or hashlib.sha256(blob).hexdigest() == checksum.split()[0].decode():
            break
        if attempt == 2:
            raise ValueError(f"checksum mismatch after 3 downloads: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if checksum is not None:
        _write_atomic(dest.with_name(dest.name + ".CHECKSUM"), checksum)
    _write_atomic(dest, blob)  # zip last: its presence marks a complete, verified download
    return dest


def fetch_history(name: str, start: date, end: date, root: Path = RAW_ROOT, workers: int = 16) -> list[Path]:
    """Download every file of `name` between start and end (inclusive) not already stored.

    Returns the stored raw files for that range. Periods Binance hasn't published are skipped.
    """
    with ThreadPoolExecutor(workers) as ex:
        paths = ex.map(lambda p: _fetch_one(name, p, root), periods(name, start, end))
        return [p for p in paths if p is not None]


# ---------- funding fallback: REST API for months not yet in the archive ----------

REST_FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"
REST_FUNDING_INFO = "https://fapi.binance.com/fapi/v1/fundingInfo"
REST_ROOT = Path("data/raw/binance_rest/funding")
DEFAULT_FUNDING_INTERVAL_H = 8  # BTCUSDT; fundingInfo lists only symbols with a non-default interval


def _month_bounds(month: str) -> tuple[int, int]:
    y, m = map(int, month.split("-"))
    start = datetime(y, m, 1, tzinfo=UTC)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def fetch_funding_rest(month: str, root: Path = REST_ROOT) -> Path:
    """Fetch one month of BTCUSDT funding settlements from the public REST API and save the raw response.

    Used for months whose archive file isn't published yet. The file for a month still in progress is
    rewritten on each fetch (the only raw file that changes); once the archive month exists, build_clean
    compares the two and the archive is used.
    """
    start, end = _month_bounds(month)
    rows, cursor = [], start
    while cursor < end:
        page = json.loads(
            _get(f"{REST_FUNDING}?symbol=BTCUSDT&startTime={cursor}&endTime={end - 1}&limit=1000")
        )
        rows += page
        if len(page) < 1000:
            break
        cursor = page[-1]["fundingTime"] + 1
    info = [x for x in json.loads(_get(REST_FUNDING_INFO)) if x["symbol"] == "BTCUSDT"]
    interval = info[0]["fundingIntervalHours"] if info else DEFAULT_FUNDING_INTERVAL_H
    out = root / f"BTCUSDT-fundingRate-{month}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "month": month,
        "fetched_at": datetime.now(UTC).isoformat(),
        "interval_hours": interval,
        "rows": rows,
    }
    _write_atomic(out, json.dumps(record).encode())
    return out


def fetch_funding_fallback(
    start: date, end: date, root: Path = RAW_ROOT, rest_root: Path = REST_ROOT
) -> list[Path]:
    """REST-fetch every funding month in [start, end] that has no archive file."""
    return [
        fetch_funding_rest(m, rest_root)
        for m in periods("funding", start, end)
        if not raw_path("funding", m, root).exists()
    ]


def read_funding_rest(path: Path) -> pl.DataFrame:
    rec = json.loads(path.read_text())
    return pl.DataFrame(
        {
            "calc_time": [r["fundingTime"] for r in rec["rows"]],
            "funding_interval_hours": [rec["interval_hours"]] * len(rec["rows"]),
            "last_funding_rate": [float(r["fundingRate"]) for r in rec["rows"]],
        },
        schema={"calc_time": pl.Int64, "funding_interval_hours": pl.Int64, "last_funding_rate": pl.Float64},
    ).with_columns(pl.col("calc_time").cast(UTC_MS))


# ---------- parse ----------


def _epoch_ms(col: str) -> pl.Expr:
    c = pl.col(col)
    # spot klines switched from millisecond to microsecond timestamps on 2025-01-01
    return pl.when(c > 10**14).then(c // 1000).otherwise(c).cast(UTC_MS)


def parse_csv(name: str, data: bytes) -> pl.DataFrame:
    ds = DATASETS[name]
    if data[:1] and not data[:1].isdigit():  # header row present (newer files) -> skip it
        data = data.split(b"\n", 1)[1] if b"\n" in data else b""
    if not data.strip():
        df = pl.DataFrame(schema=ds.columns)
    else:
        df = pl.read_csv(io.BytesIO(data), has_header=False, schema=ds.columns)
    if ds.columns[ds.ts_col] == pl.String:  # metrics, book_depth: "YYYY-mm-dd HH:MM:SS" (UTC)
        df = df.with_columns(
            pl.col(ds.ts_col).str.strptime(pl.Datetime("ms"), "%Y-%m-%d %H:%M:%S").dt.replace_time_zone("UTC")
        )
    else:
        times = [c for c in ("open_time", "close_time", "calc_time") if c in df.columns]
        df = df.with_columns(_epoch_ms(c) for c in times)
    return df.drop([c for c in DROP_COLUMNS if c in df.columns])


def read_raw(name: str, path: Path) -> pl.DataFrame:
    with zipfile.ZipFile(path) as z:
        return parse_csv(name, z.read(z.namelist()[0]))
