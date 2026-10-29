import json
from datetime import UTC, datetime, timedelta

from websocket import WebSocketConnectionClosedException, WebSocketTimeoutException

from mbpe.data import liquidations as ll

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def liq(side: str, qty: str, ms: int) -> str:
    o = {
        "s": "BTCUSDT",
        "S": side,
        "o": "LIMIT",
        "f": "IOC",
        "q": qty,
        "p": "80000.0",
        "ap": "80010.5",
        "X": "FILLED",
        "l": qty,
        "z": qty,
        "T": ms,
    }
    return json.dumps({"stream": "btcusdt@forceOrder", "data": {"e": "forceOrder", "E": ms + 3, "o": o}})


MARK = json.dumps({"stream": "btcusdt@markPrice", "data": {"e": "markPriceUpdate", "p": "80000"}})


class FakeWS:
    def __init__(self, script):
        self.script, self.closed = list(script), False

    def recv(self):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


def kinds(root):
    return [json.loads(x)["kind"] for x in (root / "coverage.jsonl").read_text().splitlines()]


def test_events_coverage_and_reconnect(tmp_path, monkeypatch):
    monkeypatch.setattr(ll.time, "sleep", lambda s: None)
    ticks = iter(T0 + timedelta(seconds=10 * k) for k in range(100))
    sockets = [
        FakeWS(
            [MARK, liq("SELL", "0.5", 1), MARK, liq("BUY", "1.25", 2), WebSocketConnectionClosedException()]
        ),
        FakeWS([MARK, MARK, ""]),
    ]
    fails = [ConnectionError("dns")]

    def connect():
        if len(sockets) == 1 and fails:
            raise fails.pop()
        return sockets.pop(0)

    ll.run(connect, root=tmp_path, clock=lambda: next(ticks), max_connections=3)
    ev = ll.load_events(tmp_path)
    assert ev["side"].to_list() == ["SELL", "BUY"] and ev["qty"].to_list() == [
        0.5,
        1.25,
    ]
    assert ev["event_time"][0] == datetime.fromtimestamp(0.004, UTC)
    assert kinds(tmp_path) == ["start", "end", "start", "end"]
    cov = ll.load_coverage(tmp_path)
    assert cov.height == 2 and (cov["end"] > cov["start"]).all()


def test_silent_connection_is_never_covered(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(ll.time, "sleep", slept.append)
    ws = FakeWS([WebSocketTimeoutException()])
    ll.run(lambda: ws, root=tmp_path, max_connections=1)
    assert not (tmp_path / "coverage.jsonl").exists() and ws.closed and slept == [1]


def test_silence_after_data_ends_coverage_at_the_last_message(tmp_path):
    ticks = iter(T0 + timedelta(seconds=3 * k) for k in range(100))
    ws = FakeWS([MARK, MARK, MARK, WebSocketTimeoutException()])
    ll.run(lambda: ws, root=tmp_path, clock=lambda: next(ticks), max_connections=1)
    cov = ll.load_coverage(tmp_path).row(0, named=True)
    assert cov["start"] == T0 and cov["end"] == T0 + timedelta(seconds=6)


def test_heartbeat_while_data_flows(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "HEARTBEAT", 60)
    ticks = iter(T0 + timedelta(seconds=25 * k) for k in range(100))
    ws = FakeWS([MARK] * 7 + [""])
    ll.run(lambda: ws, root=tmp_path, clock=lambda: next(ticks), max_connections=1)
    k = kinds(tmp_path)
    assert k[0] == "start" and k[-1] == "end" and k.count("alive") == 2


def test_crash_without_end_record_still_counts_until_last_alive(tmp_path):
    lines = [
        {"conn": "c1", "kind": "start", "t": T0.isoformat()},
        {"conn": "c1", "kind": "alive", "t": (T0 + timedelta(minutes=5)).isoformat()},
    ]
    (tmp_path / "coverage.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    cov = ll.load_coverage(tmp_path).row(0, named=True)
    assert cov["start"] == T0 and cov["end"] == T0 + timedelta(minutes=5)


def test_parses_a_real_binance_event():
    real = {
        "e": "forceOrder",
        "E": 1793211536368,
        "o": {
            "s": "XANUSDT",
            "S": "BUY",
            "o": "LIMIT",
            "f": "IOC",
            "q": "2094",
            "p": "0.0113162",
            "ap": "0.0111490",
            "X": "FILLED",
            "l": "2094",
            "z": "2094",
            "T": 1793211535363,
            "ps": "XANUSDT",
            "st": 1,
        },
    }
    r = ll.parse(real, T0)
    assert (r["side"], r["qty"], r["avg_price"], r["status"], r["trade_time"]) == (
        "BUY",
        2094.0,
        0.011149,
        "FILLED",
        1793211535363,
    )
