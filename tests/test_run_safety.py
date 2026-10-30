import http.client
import json

import pytest

from mbpe.data import binance_rest
from mbpe.live import hourly_run as lr


def test_cut_off_download_is_retried(monkeypatch):
    calls = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            calls.append(1)
            if len(calls) == 1:
                raise http.client.IncompleteRead(b"x", 10)
            return b'{"ok": 1}'

    monkeypatch.setattr(binance_rest.urllib.request, "urlopen", lambda *a, **k: Resp())
    monkeypatch.setattr(binance_rest.time, "sleep", lambda s: None)
    assert binance_rest.get_json("https://example.test/x") == {"ok": 1} and len(calls) == 2


def test_cut_off_download_fails_after_retries(monkeypatch):
    def boom(*a, **k):
        raise http.client.IncompleteRead(b"x", 10)

    monkeypatch.setattr(binance_rest.urllib.request, "urlopen", boom)
    monkeypatch.setattr(binance_rest.time, "sleep", lambda s: None)
    with pytest.raises(http.client.IncompleteRead):
        binance_rest.get_json("https://example.test/x", tries=2)


def test_watchdog_stops_a_run_past_its_limit(tmp_path):
    now, stopped = [0.0], []

    def sleep(s):
        now[0] += s

    th = lr.watchdog(limit_s=50 * 60, root=tmp_path, clock=lambda: now[0], sleep=sleep, stop=stopped.append)
    th.join(timeout=5)
    assert stopped == [3] and now[0] >= 50 * 60
    alert = json.loads((tmp_path / "alerts.jsonl").read_text().splitlines()[-1])
    assert alert["kind"] == "watchdog"
