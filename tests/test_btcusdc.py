from datetime import UTC, datetime

import polars as pl

from mbpe.data import btcusdc
from mbpe.live import paper_trading as paper
from mbpe.live import paper_trading_b as paper_b

MIN = 60_000
NOW = datetime(2026, 10, 30, 12, 0, 30, tzinfo=UTC)


def fake_get(urls):
    def get(url):
        urls.append(url)
        if "fundingInfo" in url:
            return [{"symbol": "BTCUSDC", "fundingIntervalHours": 8}]
        start = int(url.split("startTime=")[1].split("&")[0])
        end = int(url.split("endTime=")[1].split("&")[0]) + 1
        if "fundingRate" in url:
            t0 = -(-start // (8 * 3_600_000)) * 8 * 3_600_000
            return [{"fundingTime": t, "fundingRate": "0.0001"} for t in range(t0, end, 8 * 3_600_000)]
        t0 = -(-start // MIN) * MIN
        return [
            [t, "100", "101", "99", "100.5", "1", t + MIN - 1, "100", 1, "0.5", "50", "0"]
            for t in range(t0, min(end, t0 + 1500 * MIN), MIN)
        ]

    return get


def test_update_is_incremental_and_uses_btcusdc(tmp_path):
    urls = []
    first = btcusdc.update(NOW, tmp_path, fake_get(urls))
    assert urls and all("symbol=BTCUSDC" in u for u in urls if "symbol=" in u)
    assert first["futures_klines_1m"]["rows"] == btcusdc.HISTORY_DAYS * 1440 - 1
    later = datetime(2026, 10, 30, 12, 10, 30, tzinfo=UTC)
    second = btcusdc.update(later, tmp_path, fake_get(urls))
    k = btcusdc.load(tmp_path)["futures_klines_1m"]
    assert second["futures_klines_1m"]["rows"] == first["futures_klines_1m"]["rows"] + 10
    assert k["open_time"].is_unique().all() and k["open_time"].is_sorted()
    assert btcusdc.load(tmp_path)["funding"].height == 6


def test_open_uses_atr_candles_and_records_fees(monkeypatch):
    seen = {}

    def fake_atr(c, cfg):
        seen["rows"] = c.height
        t = int(c["open_time"].max().timestamp() * 1000) + MIN
        return pl.DataFrame({"decision_time": [t], "atr": [2.0]}).with_columns(
            pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
        )

    monkeypatch.setattr(paper, "decision_atr", fake_atr)
    t = int(NOW.replace(second=0).timestamp() * 1000)
    usdc = pl.DataFrame({"open_time": [t + MIN], "open": [100.0]}).with_columns(
        pl.col("open_time").cast(pl.Datetime("ms", "UTC"))
    )
    usdt = pl.DataFrame({"open_time": [t - 2 * MIN, t - MIN]}).with_columns(
        pl.col("open_time").cast(pl.Datetime("ms", "UTC"))
    )
    cfg = paper.load_config(paper.LABEL_CONFIG)
    ev = paper._open({"decision": "long", "decision_ms": t}, usdc, cfg, "v", usdt)
    assert seen["rows"] == 2 and ev["entry_price"] == 100.0
    assert (ev["tp"], ev["sl"]) == (104.0, 98.0)
    assert ev["fees"] == {"taker": 0.0004, "maker": 0.0, "slippage_bps": 2.0}
    assert paper_b.LABEL_CONFIG == paper.LABEL_CONFIG
