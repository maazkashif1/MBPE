from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from mbpe.data.binance_rest import DAY, fetch, get_json
from mbpe.data.clean import dedupe, validate

SYMBOL = "BTCUSDC"
ROOT = Path("data/live/btcusdc")
DATASETS = {"futures_klines_1m": "open_time", "funding": "calc_time"}
HISTORY_DAYS = 2


def update(now: datetime | None = None, root: Path = ROOT, get=get_json) -> dict:
    now_ms = int((now or datetime.now(UTC)).timestamp() * 1000)
    root.mkdir(parents=True, exist_ok=True)
    report = {}
    for ds, ts in DATASETS.items():
        p = root / f"{ds}.parquet"
        old = pl.read_parquet(p) if p.exists() else None
        start = (
            int(old[ts].max().timestamp() * 1000) + 1
            if old is not None and old.height
            else now_ms - HISTORY_DAYS * DAY
        )
        new = fetch(ds, start, now_ms, get, symbol=SYMBOL)
        df = new if old is None else pl.concat([old, new.select(old.columns)], how="vertical_relaxed")
        df = validate(ds, dedupe(df, ts))
        tmp = p.with_name(p.name + ".part")
        df.write_parquet(tmp)
        tmp.replace(p)
        report[ds] = {"rows": df.height, "last": df[ts].max().isoformat() if df.height else None}
    return report


def load(root: Path = ROOT) -> dict[str, pl.DataFrame]:
    return {ds: pl.read_parquet(root / f"{ds}.parquet") for ds in DATASETS}
