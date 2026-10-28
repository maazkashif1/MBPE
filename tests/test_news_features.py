"""Leakage and coverage tests for every news and calendar feature."""

import hashlib
from datetime import UTC, datetime, timedelta
from functools import cache

import numpy as np
import polars as pl
import pytest

from mbpe.features.dedup import NewsConfig, dedup, normalize_title
from mbpe.features.news import (
    DEDUP_PATH,
    NEWS_REGISTRY,
    build_news_features,
    load_calendar,
    prepare,
)
from mbpe.sources.gdelt_bq import BACKFILL_SCHEMA

CFG = NewsConfig()  # defaults: threshold 0.85, 24 h window/cap, strong negative < -0.5, coverage >= 0.8
START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = 3_600_000
EVENTS = np.array(
    [int((START + timedelta(days=d, hours=13, minutes=30)).timestamp() * 1000) for d in (3, 9, 11, 14, 25)]
)


@cache
def _vec(text: str) -> np.ndarray:
    base = text.split("~")[0]
    v = np.random.default_rng(int(hashlib.md5(base.encode()).hexdigest()[:8], 16)).normal(size=32)
    if "~" in text:
        v = v + 0.3 * np.random.default_rng(len(text)).normal(size=32)
    return v / np.linalg.norm(v)


def fake_embed(texts):
    return np.array([_vec(t) for t in texts], dtype=np.float32)


def label(ts: datetime) -> int:
    return int(f"{ts:%Y%m%d%H%M%S}")


def ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def synthetic_news(days: int = 20, outage_day: int = 12, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Rows (BACKFILL_SCHEMA) and coverage (DATE, n_rows) with a full-day outage on `outage_day`."""
    rng = np.random.default_rng(seed)
    rows, cov = [], []
    n_batches = days * 96
    for b in range(1, n_batches + 1):
        ts = START + timedelta(minutes=15 * b)
        if (ts - START).days == outage_day:
            continue  # outage: GDELT published nothing
        cov.append((label(ts), int(rng.integers(500, 3000))))
        for _ in range(rng.poisson(3)):
            story = (b // 24 + rng.integers(0, 6)) % 40  # stories drift over time
            kind = rng.choice(["crypto", "macro", "both"], p=[0.5, 0.35, 0.15])
            kw = {"crypto": ["bitcoin"], "macro": ["inflation"], "both": ["bitcoin", "inflation"]}[kind]
            title = (
                ""
                if rng.uniform() < 0.03
                else f"story {story} on {kind} markets" + "~" * int(rng.integers(0, 3))
            )
            rows.append(
                (
                    label(ts),
                    "x.com",
                    f"https://x.com/{b}-{len(rows)}",
                    f"{rng.normal(0, 3):.3f},1,1",
                    title,
                    kw,
                )
            )
    return (
        pl.DataFrame(rows, schema=BACKFILL_SCHEMA, orient="row"),
        pl.DataFrame(cov, schema={"DATE": pl.Int64, "n_rows": pl.Int64}, orient="row"),
    )


def fake_scores(rows: pl.DataFrame) -> pl.DataFrame:
    norms = sorted({normalize_title(t) for t in rows["title"].to_list()} - {""})
    score = [int(hashlib.md5(n.encode()).hexdigest()[:6], 16) / 0xFFFFFF * 2 - 1 for n in norms]
    return pl.DataFrame({"norm": norms, "score": score})


def features(rows: pl.DataFrame, cov: pl.DataFrame, at: list[int]) -> pl.DataFrame:
    """Full pipeline on the given data: dedup (causal) -> prepare -> features at `at`."""
    clusters = dedup(rows, fake_embed, CFG).select("DATE", "DocumentIdentifier", "cluster_id", "match_sim")
    prep = prepare(rows, clusters, fake_scores(rows))
    return build_news_features(prep, cov, EVENTS, CFG, at=np.array(at, dtype=np.int64))


def times(n: int, seed: int, first_day: int = 8, last_day: int = 20, extra: list[datetime] = ()) -> list[int]:
    hours = [ms(START + timedelta(days=first_day, hours=h)) for h in range((last_day - first_day) * 24)]
    picks = np.random.default_rng(seed).choice(hours, n, replace=False).tolist()
    return sorted(set(picks + [ms(e) for e in extra]))


def assert_equal_at(a: pl.DataFrame, b: pl.DataFrame, where: str) -> None:
    bad = [n for n in NEWS_REGISTRY if not (a[n][0] == b[n][0] or (a[n][0] is None and b[n][0] is None))]
    if bad:
        pytest.fail(
            f"LEAKAGE at {where} in {len(bad)} feature(s): "
            + ", ".join(f"{n} ({a[n][0]} vs {b[n][0]})" for n in bad)
        )


def test_every_feature_has_metadata():
    assert {f.group for f in NEWS_REGISTRY.values()} == {"news", "calendar"}
    assert all(f.inputs for f in NEWS_REGISTRY.values())


def test_no_leakage_truncated_at_t():
    rows, cov = synthetic_news()
    outage = START + timedelta(days=12)
    at = times(200, seed=1, extra=[outage + timedelta(hours=h) for h in (-1, 0, 6, 24, 25, 30, 48)])
    full = features(rows, cov, at)
    for k, t in enumerate(at):
        cut_rows = rows.filter(pl.col("DATE") < label(datetime.fromtimestamp(t / 1000, UTC)))
        cut_cov = cov.filter(pl.col("DATE") < label(datetime.fromtimestamp(t / 1000, UTC)))
        assert_equal_at(
            full.slice(k, 1), features(cut_rows, cut_cov, [t]), datetime.fromtimestamp(t / 1000, UTC)
        )


def test_articles_labeled_after_t_change_nothing():
    rows, cov = synthetic_news(seed=2)
    for t in times(25, seed=3):
        tt = datetime.fromtimestamp(t / 1000, UTC)
        late = [label(tt), label(tt + timedelta(minutes=15))]  # available at t + 15 min and later
        extra = pl.DataFrame(
            [
                (d, "x.com", f"https://late.com/{d}-{i}", "-9,1,1", title, kw)
                for d in late
                for i, (title, kw) in enumerate(
                    [
                        ("story 3 on crypto markets~~", ["bitcoin"]),
                        ("brand new crisis story", ["bitcoin", "inflation"]),
                    ]
                )
            ],
            schema=BACKFILL_SCHEMA,
            orient="row",
        )
        more_cov = pl.DataFrame({"DATE": late, "n_rows": [999, 999]})
        before = features(rows, cov, [t])
        after = features(pl.concat([rows, extra]), pl.concat([cov, more_cov]).unique("DATE"), [t])
        assert_equal_at(before, after, tt)


def test_outage_gives_nan_not_zero():
    rows, cov = synthetic_news()
    outage = START + timedelta(days=12)
    df = features(rows, cov, [ms(outage + timedelta(hours=12)), ms(outage + timedelta(hours=30))])
    mid, after = df.row(0, named=True), df.row(1, named=True)
    news = [n for n, f in NEWS_REGISTRY.items() if f.group == "news" and not n.startswith("news_cov")]
    assert [n for n in news if mid[n] is not None] == []  # every news feature missing inside the outage
    assert (
        mid["news_cov_1h"] == 0.0 and mid["news_cov_24h"] == 0.5
    )  # half the 24 h window precedes the outage
    assert mid["cal_hours_to_next_event"] is not None  # calendar unaffected
    # 6 h after the outage: 1h/4h windows are back, the 24 h window is 25% covered -> still NaN
    assert after["news_crypto_articles_1h"] is not None and after["news_crypto_articles_24h"] is None
    # a 1-day outage leaves ~140 of 168 hourly baseline values valid (>= 135 = 80%): z is defined
    assert after["news_crypto_count_z_4h"] is not None and mid["news_crypto_count_z_4h"] is None


def test_sentiment_counts_each_cluster_once():
    t0 = START + timedelta(days=1)
    good = [
        (label(t0), "x.com", f"https://g.com/{i}", "1,1,1", "Bitcoin rallies to record", ["bitcoin"])
        for i in range(10)
    ]
    bad = [(label(t0), "x.com", "https://b.com/1", "1,1,1", "Exchange hacked, funds lost", ["bitcoin"])]
    rows = pl.DataFrame(good + bad, schema=BACKFILL_SCHEMA, orient="row")
    cov = pl.DataFrame(
        {"DATE": [label(t0 + timedelta(minutes=15 * i)) for i in range(-200, 5)], "n_rows": [1] * 205}
    )
    clusters = dedup(rows, fake_embed, CFG).select("DATE", "DocumentIdentifier", "cluster_id", "match_sim")
    scores = pl.DataFrame(
        {"norm": ["bitcoin rallies to record", "exchange hacked, funds lost"], "score": [1.0, -1.0]}
    )
    df = build_news_features(prepare(rows, clusters, scores), cov, EVENTS, CFG, at=np.array([ms(t0) + HOUR]))
    r = df.row(0, named=True)
    assert r["news_crypto_articles_1h"] == 11 and r["news_crypto_new_clusters_1h"] == 2
    assert r["news_crypto_sent_mean_1h"] == 0.0  # (1 + -1) / 2 clusters, not (10 - 1) / 11 articles
    assert r["news_crypto_sent_neg_share_1h"] == 0.5


def test_calendar_features():
    at = [ms(START + timedelta(days=3, hours=10)), ms(START + timedelta(days=3, hours=14))]
    df = features(*synthetic_news(days=4, outage_day=99), at)
    a, b = df.row(0, named=True), df.row(1, named=True)
    assert a["cal_hours_to_next_event"] == 3.5 and a["cal_event_next_4h"] == 1.0
    assert b["cal_hours_since_last_event"] == 0.5 and b["cal_event_next_4h"] == 0.0


def test_calendar_file_is_sorted_and_utc():
    ev = load_calendar()
    assert len(ev) > 100 and (np.diff(ev) > 0).all()
    cpi_jul_2025 = ms(datetime(2025, 7, 15, 12, 30, tzinfo=UTC))  # 08:30 EDT
    fomc_dec_2025 = ms(datetime(2025, 12, 10, 19, 0, tzinfo=UTC))  # 14:00 EST
    assert cpi_jul_2025 in ev and fomc_dec_2025 in ev


@pytest.mark.skipif(not DEDUP_PATH.exists(), reason="news dedup not built")
def test_real_mid_2025_outage_is_nan():
    """GDELT published nothing 2025-06-14 18:00 -> 2025-07-02 02:00 UTC: news features must be NaN, not 0."""
    from mbpe.features.dedup import load_news_config
    from mbpe.features.news import load_inputs

    inp = load_inputs(load_news_config(), require_scores=False)
    at = [ms(datetime(2025, 6, d, h, tzinfo=UTC)) for d, h in ((16, 0), (20, 12), (25, 6), (30, 23))]
    at.append(ms(datetime(2025, 7, 5, 12, tzinfo=UTC)))  # after: 1h-24h back, 7-day baseline still short
    df = build_news_features(inp.prep, inp.coverage, inp.events, load_news_config(), at=np.array(at))
    news = [n for n, f in NEWS_REGISTRY.items() if f.group == "news" and not n.startswith("news_cov")]
    for r in df.head(4).iter_rows(named=True):
        assert [n for n in news if r[n] is not None] == [], r["decision_time"]
        assert r["news_cov_24h"] == 0.0
    after = df.row(4, named=True)
    assert after["news_crypto_articles_24h"] is not None and after["news_crypto_articles_24h"] > 0
    assert after["news_crypto_count_z_4h"] is None


def test_harness_catches_a_leaky_news_feature():
    from mbpe.features.market import Feature

    NEWS_REGISTRY["leaky_next_hour_count"] = Feature(
        "leaky_next_hour_count",
        "news",
        ("gdelt_rows",),
        lambda ctx: ctx.count(ctx.mask["crypto"], HOUR, ends=ctx.grid + HOUR),
    )
    try:
        rows, cov = synthetic_news(days=10, outage_day=99, seed=5)
        t = ms(START + timedelta(days=8, hours=7))
        cut = rows.filter(pl.col("DATE") < label(datetime.fromtimestamp(t / 1000, UTC)))
        with pytest.raises(pytest.fail.Exception, match="leaky_next_hour_count"):
            assert_equal_at(
                features(rows, cov, [t]),
                features(cut, cov.filter(pl.col("DATE") < label(datetime.fromtimestamp(t / 1000, UTC))), [t]),
                "t",
            )
    finally:
        del NEWS_REGISTRY["leaky_next_hour_count"]


def test_phase3_calendar_ignores_other_events():
    # macro_events.csv also lists NFP (Phase 4); Phase 3 features and blackout use CPI + FOMC only
    ev = pl.read_csv("data/reference/macro_events.csv")
    utc = lambda e: ev.filter((pl.col("event") == e) & pl.col("release_utc").is_not_null())["release_utc"]  # noqa: E731
    cal = load_calendar()
    assert len(cal) == len(utc("CPI")) + len(utc("FOMC"))
    nfp = utc("NFP").str.strptime(pl.Datetime("ms", "UTC"), "%Y-%m-%d %H:%M").dt.epoch("ms").to_numpy()
    assert len(nfp) and not np.isin(nfp, cal).any()
