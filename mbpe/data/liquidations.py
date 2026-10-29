import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)
URL = "wss://fstream.binance.com/market/stream?streams=btcusdt@forceOrder/btcusdt@markPrice"
LIVE = Path("data/raw/liquidations")
STALE = 30
HEARTBEAT = 60
MAX_BACKOFF = 60
UTC_MS = pl.Datetime("ms", "UTC")


def parse(event: dict, received_at: datetime) -> dict:
    o = event["o"]
    return {
        "event_time": int(event["E"]),
        "trade_time": int(o["T"]),
        "symbol": o["s"],
        "side": o["S"],
        "order_type": o["o"],
        "time_in_force": o["f"],
        "qty": float(o["q"]),
        "price": float(o["p"]),
        "avg_price": float(o["ap"]),
        "status": o["X"],
        "last_filled_qty": float(o["l"]),
        "filled_qty": float(o["z"]),
        "received_at": received_at.isoformat(),
    }


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def run(
    connect, root: Path = LIVE, clock=lambda: datetime.now(UTC), max_connections: int | None = None
) -> None:
    from websocket import WebSocketTimeoutException

    backoff, n = 1, 0
    while max_connections is None or n < max_connections:
        n += 1
        conn = uuid.uuid4().hex[:12]
        try:
            ws = connect()
        except Exception as e:
            log.warning("connect failed: %s; retry in %ss", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
            continue
        last_msg = last_alive = None
        cov = root / "coverage.jsonl"
        try:
            while True:
                try:
                    msg = ws.recv()
                except (WebSocketTimeoutException, TimeoutError):
                    log.warning("connection %s silent for %ss: reconnecting", conn, STALE)
                    break
                if not msg:
                    break
                now = clock()
                payload = json.loads(msg)
                data = payload.get("data", payload)
                if data.get("e") == "forceOrder":
                    _append(root / f"{now:%Y-%m-%d}.jsonl", parse(data, now))
                if last_alive is None:
                    _append(cov, {"conn": conn, "kind": "start", "t": now.isoformat()})
                    last_alive, backoff = now, 1
                elif (now - last_alive).total_seconds() >= HEARTBEAT:
                    _append(cov, {"conn": conn, "kind": "alive", "t": now.isoformat()})
                    last_alive = now
                last_msg = now
        except Exception as e:
            log.warning("connection %s ended: %s", conn, e)
        finally:
            if last_msg is not None:
                _append(cov, {"conn": conn, "kind": "end", "t": last_msg.isoformat()})
            try:
                ws.close()
            except Exception:
                pass
            if last_msg is None:
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)


def load_events(root: Path = LIVE) -> pl.DataFrame:
    files = sorted(p for p in root.glob("*.jsonl") if p.name != "coverage.jsonl")
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_ndjson(f) for f in files]).with_columns(
        pl.col("received_at").str.to_datetime(time_zone="UTC").cast(UTC_MS),
        pl.from_epoch("event_time", "ms").dt.replace_time_zone("UTC").alias("event_time"),
    )


def load_coverage(root: Path = LIVE) -> pl.DataFrame:
    path = root / "coverage.jsonl"
    if not path.exists():
        return pl.DataFrame(schema={"conn": pl.String, "start": UTC_MS, "end": UTC_MS})
    c = pl.read_ndjson(path).with_columns(pl.col("t").str.to_datetime(time_zone="UTC").cast(UTC_MS))
    return (
        c.group_by("conn")
        .agg(start=pl.col("t").filter(pl.col("kind") == "start").min(), end=pl.col("t").max())
        .drop_nulls()
        .sort("start")
    )


def main() -> None:
    from websocket import create_connection

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(lambda: create_connection(URL, timeout=STALE))


if __name__ == "__main__":
    main()
