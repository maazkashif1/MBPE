"""Binance USD-M BTCUSDT liquidation collector (Phase 6, forward only): WebSocket stream btcusdt@forceOrder.

Usage: uv run python -m mbpe.sources.liquidations_live   # long-running; restart on exit (deploy/ examples)

Binance has no free liquidation history (the REST endpoint is retired), so this is forward data only. Per
Binance's stream docs, at most the latest liquidation per symbol within each 1000 ms is pushed: the stream
UNDERCOUNTS liquidations in busy seconds. Features must treat it as a lower bound.

Endpoint: the /market/ combined-stream path. The legacy /ws/ path still accepts connections but delivers
nothing (checked 2026-10-28), so a silent connection must never count as covered. The stream is combined with
btcusdt@markPrice (pushed every 3 s): coverage is proven by data flowing, and a connection with no message for
STALE seconds is dropped and reopened.

Writes (append-only, one line per record, so a crash loses at most the line being written):
- data/raw/liquidations/<YYYY-MM-DD>.jsonl: one forceOrder event per line (day of received_at, UTC)
- data/raw/liquidations/coverage.jsonl: {conn, kind: start|alive|end, t}. start = first message on a
  connection, alive = at least every HEARTBEAT seconds while messages flow, end = the last message. A
  window is covered only between a connection's start and its last alive/end record. No liquidation events
  during covered time = none pushed; outside it = unknown.
"""

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
STALE = 30  # seconds without any message (mark price arrives every 3 s) -> the connection is dead
HEARTBEAT = 60  # seconds between "alive" coverage records while data flows
MAX_BACKOFF = 60
UTC_MS = pl.Datetime("ms", "UTC")


def parse(event: dict, received_at: datetime) -> dict:
    """forceOrder event -> flat record. side SELL = a long position liquidated; BUY = a short liquidated."""
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
    """Listen forever (or for `max_connections` connections, for tests), reconnecting with backoff.

    `connect()` returns an object with recv() (raising a timeout after STALE seconds of silence) and close().
    """
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
                if not msg:  # closed by the server
                    break
                now = clock()
                payload = json.loads(msg)
                data = payload.get("data", payload)  # combined streams wrap events in {"stream", "data"}
                if data.get("e") == "forceOrder":
                    _append(root / f"{now:%Y-%m-%d}.jsonl", parse(data, now))
                if last_alive is None:
                    _append(cov, {"conn": conn, "kind": "start", "t": now.isoformat()})
                    last_alive, backoff = now, 1
                elif (now - last_alive).total_seconds() >= HEARTBEAT:
                    _append(cov, {"conn": conn, "kind": "alive", "t": now.isoformat()})
                    last_alive = now
                last_msg = now
        except Exception as e:  # dropped connection (Binance also closes every 24 h): reconnect
            log.warning("connection %s ended: %s", conn, e)
        finally:
            if last_msg is not None:  # covered up to the last message actually received
                _append(cov, {"conn": conn, "kind": "end", "t": last_msg.isoformat()})
            try:
                ws.close()
            except Exception:
                pass
            if last_msg is None:  # connected but never received anything: back off like a failed connect
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
    """Covered intervals: per connection, [start, last alive/end record]."""
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
