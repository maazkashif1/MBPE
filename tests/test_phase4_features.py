from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from mbpe.data.clean import (
    BOOK_BANDS,
    BOOK_COLUMNS,
    ConflictingDuplicatesError,
    book_depth_wide,
    clean_path,
)
from mbpe.features.market import HOUR, MIN, UTC_MS
from mbpe.features.nfp_and_orderbook import (
    BOOK_STALE,
    PHASE4_REGISTRY,
    P4Feature,
    build_phase4_features,
    load_book,
)

START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
SEC = 1000


def synthetic_book(days: int = 20, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    t = START + np.cumsum(rng.integers(28, 33, days * 2880)) * SEC
    keep = ~((t >= START + 5 * 86_400_000) & (t < START + 6 * 86_400_000))
    keep &= ~((t > START + 2 * 86_400_000 + 10 * HOUR - 3 * MIN) & (t < START + 2 * 86_400_000 + 10 * HOUR))
    keep &= ~((t > START + 3 * 86_400_000 + 7 * HOUR - 90 * SEC) & (t < START + 3 * 86_400_000 + 7 * HOUR))
    t = t[keep]
    cols = {}
    for side in ("bid", "ask"):
        level = np.exp(np.cumsum(rng.normal(0, 0.01, len(t))))
        for b in BOOK_BANDS:
            depth = 1000 * b * level * rng.uniform(0.9, 1.1, len(t))
            cols[f"{side}_depth_{round(b * 100)}bp"] = depth
            cols[f"{side}_notional_{round(b * 100)}bp"] = depth * 60_000
    df = pl.DataFrame({"timestamp": t, **cols}).with_columns(pl.col("timestamp").cast(UTC_MS))
    null_rows = rng.choice(len(t), 50, replace=False)
    return df.with_columns(
        pl.when(pl.int_range(pl.len()).is_in(null_rows)).then(None).otherwise(pl.col("ask_depth_100bp"))
    ).select("timestamp", *BOOK_COLUMNS)


NFP = np.array(
    [START + 4 * 86_400_000 + 13 * HOUR + 30 * MIN, START + 32 * 86_400_000 + 13 * HOUR + 30 * MIN]
)


def times(n: int, seed: int, extra=()) -> np.ndarray:
    hours = np.arange(START + 8 * 86_400_000, START + 20 * 86_400_000, HOUR)
    return np.unique(np.r_[np.random.default_rng(seed).choice(hours, n - len(extra), replace=False), extra])


def assert_no_leakage(book: pl.DataFrame, at: np.ndarray) -> None:
    full = build_phase4_features(book, NFP, at)
    bad: dict[str, list] = {}
    for k, t in enumerate(at):
        cut = build_phase4_features(
            book.filter(pl.col("timestamp").dt.epoch("ms") < int(t)), NFP, np.array([t])
        )
        for n in PHASE4_REGISTRY:
            a, b = full[n][k], cut[n][0]
            if not (a == b or (a is None and b is None)):
                bad.setdefault(n, []).append(f"{datetime.fromtimestamp(t / 1000, UTC)}: {a} vs {b}")
    if bad:
        pytest.fail(f"LEAKAGE in {len(bad)} feature(s): " + "; ".join(f"{n}: {v[0]}" for n, v in bad.items()))


def test_every_feature_has_a_phase4_group():
    assert {f.group for f in PHASE4_REGISTRY.values()} == {"nfp", "orderbook"}
    assert sum(f.group == "orderbook" for f in PHASE4_REGISTRY.values()) == 8


def test_no_leakage_synthetic_book():
    gaps = [START + 2 * 86_400_000 + 10 * HOUR, START + 3 * 86_400_000 + 7 * HOUR, START + 6 * 86_400_000]
    gaps += [g + HOUR for g in gaps]
    assert_no_leakage(
        synthetic_book(), times(200, seed=1, extra=[g for g in gaps if g >= START + 8 * 86_400_000])
    )
    assert_no_leakage(synthetic_book(), np.array(gaps))


def test_staleness_limit_and_strictly_before_t():
    t = START + 3 * HOUR
    book = synthetic_book(days=1).filter(pl.col("timestamp").dt.epoch("ms") < START + HOUR)
    exact = book.tail(1).with_columns(timestamp=pl.lit(t - BOOK_STALE).cast(UTC_MS))
    at_t = book.tail(1).with_columns(timestamp=pl.lit(t).cast(UTC_MS), bid_depth_20bp=pl.lit(1e9))
    f = build_phase4_features(pl.concat([book, exact, at_t]), NFP, np.array([t]))
    assert f["book_imbalance_20bp"][0] is not None and f["book_imbalance_20bp"][0] < 0.99
    late = exact.with_columns(timestamp=pl.lit(t - BOOK_STALE - 1).cast(UTC_MS))
    g = build_phase4_features(pl.concat([book, late]), NFP, np.array([t]))
    assert all(g[n][0] is None for n, ft in PHASE4_REGISTRY.items() if ft.group == "orderbook")


def test_values_on_a_hand_built_book():
    hours = np.arange(START - 170 * HOUR, START + HOUR, HOUR)
    snaps = hours - 30 * SEC
    d = {c: np.ones(len(snaps)) for c in BOOK_COLUMNS}
    d["bid_depth_20bp"], d["ask_depth_20bp"] = np.full(len(snaps), 3.0), np.full(len(snaps), 1.0)
    d["bid_depth_500bp"] = np.arange(len(snaps), dtype=float) + 1
    book = pl.DataFrame({"timestamp": snaps, **d}).with_columns(pl.col("timestamp").cast(UTC_MS))
    f = build_phase4_features(book, NFP, np.array([START])).row(0, named=True)
    n = len(snaps)
    assert f["book_imbalance_20bp"] == pytest.approx(0.5)
    assert f["book_imbalance_100bp"] == pytest.approx(0.0)
    assert f["book_depth_chg_1h"] == pytest.approx(np.log((n + 1) / n))
    assert f["book_depth_vs_7d_median"] == pytest.approx(
        np.log((n + 1) / np.median(np.arange(n - 167, n + 1)))
    )


def test_nfp_features():
    at = np.array([NFP[0] - 3 * HOUR - 30 * MIN, NFP[0] + 30 * MIN])
    a, b = build_phase4_features(synthetic_book(days=1), NFP, at).iter_rows(named=True)
    assert a["nfp_hours_to_next"] == 3.5 and a["nfp_next_4h"] == 1.0
    assert b["nfp_hours_since_last"] == 0.5 and b["nfp_next_4h"] == 0.0


def test_leakage_check_catches_a_peeking_feature():
    PHASE4_REGISTRY["leaky_book"] = P4Feature(
        "leaky_book", "orderbook", lambda ctx: ctx.at("bid_depth_20bp", ctx.grid + 5 * MIN)
    )
    try:
        with pytest.raises(pytest.fail.Exception, match="leaky_book"):
            assert_no_leakage(synthetic_book(), times(20, seed=2))
    finally:
        del PHASE4_REGISTRY["leaky_book"]


@pytest.mark.skipif(not clean_path("book_depth").exists(), reason="clean bookDepth not built")
def test_no_leakage_real_book_slice():
    lo, hi = (
        datetime(2024, 4, 10, tzinfo=UTC),
        datetime(2024, 4, 26, tzinfo=UTC),
    )
    book = load_book().filter(pl.col("timestamp").is_between(lo, hi, closed="left"))
    hours = np.arange(int(lo.timestamp() * 1000) + 8 * 86_400_000, int(hi.timestamp() * 1000), HOUR)
    gap = int(datetime(2024, 4, 18, tzinfo=UTC).timestamp() * 1000)
    extra = [gap + h * HOUR for h in (0, 1, 23, 24, 25)]
    at = np.unique(np.r_[np.random.default_rng(4).choice(hours, 150, replace=False), extra])
    assert_no_leakage(book, at)


def test_book_depth_pivot():
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    rows = [(ts, -b, 1.0 + b, 10.0) for b in BOOK_BANDS] + [(ts, b, 2.0, 20.0) for b in BOOK_BANDS[:-1]]
    long = pl.DataFrame(rows, schema=["timestamp", "percentage", "depth", "notional"], orient="row")
    long = long.with_columns(pl.col("timestamp").cast(UTC_MS))
    w = book_depth_wide(pl.concat([long, long.head(2)]))
    assert w.columns == ["timestamp", *BOOK_COLUMNS] and w.height == 1
    assert w["bid_depth_20bp"][0] == 1.2 and w["ask_depth_500bp"][0] is None
    clash = long.head(1).with_columns(depth=pl.lit(9.0))
    with pytest.raises(ConflictingDuplicatesError):
        book_depth_wide(pl.concat([long, clash]))
