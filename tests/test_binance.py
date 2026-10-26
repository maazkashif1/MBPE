from datetime import UTC, datetime, timedelta

import pandera.polars as pa
import polars as pl
import pytest

from mbpe.sources.binance import UTC_MS, parse_csv
from mbpe.storage.clean import ConflictingDuplicatesError, dedupe, gap_report, null_runs, validate

T0 = datetime(2024, 1, 1, tzinfo=UTC)
KLINE_HEADER = (
    b"open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    b"taker_buy_volume,taker_buy_quote_volume,ignore\n"
)


def kline_rows(n: int, start_ms: int = 1704067200000, scale: int = 1) -> bytes:
    lines = []
    for i in range(n):
        t = start_ms + i * 60_000
        lines.append(f"{t * scale},100.0,101.0,99.0,100.5,2.0,{(t + 59_999) * scale},201.0,7,1.0,100.5,0")
    return ("\n".join(lines) + "\n").encode()


def klines(minutes: list[int]) -> pl.DataFrame:
    df = parse_csv("futures_klines_1m", kline_rows(max(minutes) + 1))
    return df.filter(pl.int_range(pl.len()).is_in(minutes))


def test_identical_duplicates_dropped():
    df = klines([0, 1, 2])
    out = dedupe(pl.concat([df, df.slice(1, 1)]), "open_time")
    assert out.equals(df)


def test_conflicting_duplicates_raise():
    df = klines([0, 1, 2])
    conflict = df.slice(1, 1).with_columns(pl.col("close") + 1)
    with pytest.raises(ConflictingDuplicatesError):
        dedupe(pl.concat([df, conflict]), "open_time")


def test_header_and_no_header_klines_parse_identically():
    body = kline_rows(5)
    with_header = parse_csv("futures_klines_1m", KLINE_HEADER + body)
    without = parse_csv("futures_klines_1m", body)
    assert with_header.equals(without)
    assert without.schema["open_time"] == UTC_MS
    assert without["open_time"][0] == T0


def test_spot_microsecond_timestamps_match_milliseconds():
    assert parse_csv("spot_klines_1m", kline_rows(5, scale=1000)).equals(
        parse_csv("spot_klines_1m", kline_rows(5))
    )


@pytest.mark.parametrize(
    "bad", [lambda df: df.reverse(), lambda df: pl.concat([df, df.tail(1)])], ids=["unsorted", "duplicate"]
)
def test_schema_rejects_unsorted_or_duplicate_timestamps(bad):
    df = klines([0, 1, 2])
    validate("futures_klines_1m", df)  # sanity: the good frame passes
    with pytest.raises(pa.errors.SchemaErrors):
        validate("futures_klines_1m", bad(df))


def test_gap_report_finds_synthetic_gap():
    df = klines([0, 1, 2, 7, 8])  # minutes 3..6 missing
    gaps = gap_report("futures_klines_1m", df)
    assert gaps.height == 1
    row = gaps.row(0, named=True)
    assert row["start"] == T0 + timedelta(minutes=2)
    assert row["end"] == T0 + timedelta(minutes=7)
    assert row["length"] == timedelta(minutes=5)
    assert row["missing"] == 4


def test_gap_report_empty_when_continuous():
    assert gap_report("futures_klines_1m", klines([0, 1, 2, 3])).height == 0


def test_null_runs_finds_blank_stretches():
    df = klines([0, 1, 2, 3, 4]).with_columns(
        close=pl.when(pl.int_range(pl.len()).is_in([1, 2, 4])).then(None).otherwise(pl.col("close"))
    )
    runs = null_runs(df, "open_time", ["close", "open"])
    assert runs.select("start", "end", "rows").rows() == [
        (T0 + timedelta(minutes=1), T0 + timedelta(minutes=2), 2),
        (T0 + timedelta(minutes=4), T0 + timedelta(minutes=4), 1),
    ]
