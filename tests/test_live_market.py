"""Live market top-up without network access: canned REST responses over a tiny archive clean layer."""

from datetime import UTC, datetime

import polars as pl

from mbpe.live import market as lm
from mbpe.storage.clean import validate

MIN = 60_000
T0 = int(datetime(2026, 10, 25, 23, 57, tzinfo=UTC).timestamp() * 1000)  # archive's last minute bar
NOW = T0 + 6 * MIN + 30_000  # 2026-10-26 00:03:30


def kline(t: int, close: float, vol: str = "1.0") -> list:
    return [
        t,
        str(close),
        str(close + 1),
        str(close - 1),
        str(close),
        vol,
        t + MIN - 1,
        "100.0",
        5,
        "0.5",
        "50.0",
        "0",
    ]


def archive(tmp_path):
    root = tmp_path / "clean"
    root.mkdir()
    kl = lm.klines_frame([kline(T0 - k * MIN, 100.0) for k in range(3, -1, -1)], NOW)
    for ds in ("futures_klines_1m", "spot_klines_1m", "mark_price_1m", "premium_index_1m"):
        validate(ds, kl).write_parquet(root / f"{ds}.parquet")
    m = lm.metrics_frame(  # REST timestamp T0 + 3 min = archive create_time T0 - 2 min
        [{"timestamp": T0 + 3 * MIN, "sumOpenInterest": "9.0", "sumOpenInterestValue": "900.0"}], NOW
    )
    validate("metrics", m).write_parquet(root / "metrics.parquet")
    f = pl.DataFrame(
        {"calc_time": [T0 - 8 * 60 * MIN], "funding_interval_hours": [8], "last_funding_rate": [1e-4],
         "source": ["archive"]},
        schema=lm.FUNDING_SCHEMA,
    ).with_columns(pl.col("calc_time").cast(lm.UTC_MS))  # fmt: skip
    validate("funding", f).write_parquet(root / "funding.parquet")
    return root


def fake_get(url: str):
    if "openInterestHist" in url:
        return [{"timestamp": T0 + k * 5 * MIN, "sumOpenInterest": "10.0", "sumOpenInterestValue": "1000.0"}
                for k in range(0, 2)]  # fmt: skip
    if "fundingRate" in url:
        return [{"fundingTime": T0 + 3 * MIN + 2, "fundingRate": "0.0002"}]
    if "fundingInfo" in url:
        return [{"symbol": "ETHUSDT", "fundingIntervalHours": 4}]  # BTCUSDT absent -> default 8 h
    # klines: everything from the archive's last bar on, including the still-forming minute
    return [kline(T0 + k * MIN, 101.0 + k) for k in range(0, 8)]


def test_top_up_keeps_closed_new_rows_and_merges_after_the_archive(tmp_path):
    clean, live = archive(tmp_path), tmp_path / "live"
    rep = lm.top_up(datetime.fromtimestamp(NOW / 1000, UTC), clean, live, fake_get)
    fut = pl.read_parquet(live / "futures_klines_1m.parquet")
    # archive ends at T0; REST also returned T0 (dropped: not newer) and the forming 00:03 bar (dropped: open)
    assert fut["open_time"].dt.epoch("ms").to_list() == [T0 + k * MIN for k in range(1, 6)]
    assert rep["futures_klines_1m"]["live_rows"] == 5
    met = pl.read_parquet(live / "metrics.parquet")
    # REST timestamps T0 and T0 + 5 min are the archive's T0 - 5 min (already archived) and T0
    assert (
        met["create_time"].dt.epoch("ms").to_list() == [T0] and met["count_long_short_ratio"].is_null().all()
    )
    fund = pl.read_parquet(live / "funding.parquet")
    assert fund["funding_interval_hours"].to_list() == [8] and fund["source"].to_list() == ["rest"]
    data = lm.load_live_data(clean, live)
    f = data["futures_klines_1m"]
    assert f.height == 4 + 5 and f["open_time"].is_sorted() and f["open_time"].n_unique() == f.height
    assert f.filter(pl.col("open_time").dt.epoch("ms") == T0)["close"][0] == 100.0  # archive row kept


def test_archive_catching_up_removes_the_live_file(tmp_path):
    clean, live = archive(tmp_path), tmp_path / "live"
    lm.top_up(datetime.fromtimestamp(NOW / 1000, UTC), clean, live, fake_get)
    assert (live / "spot_klines_1m.parquet").exists()
    lm.top_up(
        datetime.fromtimestamp(NOW / 1000, UTC),
        clean,
        live,
        lambda url: [] if "fundingInfo" not in url else [],
    )
    assert not (live / "spot_klines_1m.parquet").exists()
    assert lm.load_live_data(clean, live)["spot_klines_1m"].height == 4


def test_metrics_windows_cover_the_whole_gap_even_if_the_api_returns_the_latest_rows():
    import re

    def latest_500(url):  # like Binance: rows in [startTime, endTime], at most 500, the most recent ones
        a, b = (int(x) for x in re.search(r"startTime=(\d+)&endTime=(\d+)", url).groups())
        ts = list(range(-(-a // (5 * MIN)) * 5 * MIN, b + 1, 5 * MIN))[-500:]
        return [{"timestamp": t, "sumOpenInterest": "1", "sumOpenInterestValue": "1"} for t in ts]

    start, end = T0, T0 + 3 * 86_400_000  # 3 days = 864 five-minute rows
    m = lm.fetch("metrics", start, end, latest_500)
    ts = m["create_time"].dt.epoch("ms").to_numpy()
    # every REST row in [start, end), relabeled 5 min earlier (archive convention)
    assert len(ts) == 864 and (ts[1:] - ts[:-1] == 5 * MIN).all()
    first_rest = -(-start // (5 * MIN)) * 5 * MIN  # first 5-min REST timestamp >= start
    last_rest = (end - 1) // (5 * MIN) * 5 * MIN
    assert ts[0] == first_rest - 5 * MIN and ts[-1] == last_rest - 5 * MIN
