import hashlib
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from mbpe.features.dedup import NewsConfig, clean_title, dedup, normalize_title

CFG = NewsConfig(dedup_threshold=0.85, dedup_window_hours=24)


def fake_embed(texts: list[str]) -> np.ndarray:
    """Deterministic unit vectors: identical text -> same vector; "~"-suffixed variants are near-copies."""
    out = []
    for t in texts:
        base = t.split("~")[0]
        v = np.random.default_rng(int(hashlib.md5(base.encode()).hexdigest()[:8], 16)).normal(size=64)
        if "~" in t:  # a near-duplicate: same story, slightly perturbed (cosine ~0.95)
            v = v + 0.33 * np.random.default_rng(len(t)).normal(size=64)
        out.append(v / np.linalg.norm(v))
    return np.array(out, dtype=np.float32)


def rows(items: list[tuple[int, str]]) -> pl.DataFrame:
    """(DATE, title) -> backfill-like rows."""
    return pl.DataFrame(
        {
            "DATE": [d for d, _ in items],
            "DocumentIdentifier": [f"https://x.com/{i}" for i in range(len(items))],
            "title": [t for _, t in items],
        }
    )


def test_cluster_availability_is_its_earliest_member():
    rng = np.random.default_rng(0)
    stories = [f"story {i} about bitcoin markets" for i in range(15)]
    items = []
    for _ in range(300):
        day, minute = rng.integers(1, 4), rng.integers(0, 96) * 15
        date = int(f"202610{day:02d}{minute // 60:02d}{minute % 60:02d}00")
        s = stories[rng.integers(len(stories))]
        items.append((date, s + ("~" * rng.integers(0, 3))))
    out = dedup(rows(items), fake_embed, CFG)
    earliest = out.group_by("cluster_id").agg(
        pl.col("DATE").min().alias("min_date"), pl.col("cluster_DATE").first()
    )
    assert (earliest["cluster_DATE"] == earliest["min_date"]).all()
    assert out["cluster_id"].n_unique() < out.height  # copies did merge


def test_adding_later_rows_never_changes_earlier_clusters():
    start = datetime(2026, 10, 1)
    items = [
        (int(f"{start + timedelta(minutes=15 * i):%Y%m%d%H%M%S}"), f"headline {i % 7} on inflation")
        for i in range(40)
    ]
    full = dedup(rows(items), fake_embed, CFG)
    prefix = dedup(rows(items[:25]), fake_embed, CFG)
    joined = prefix.join(full, on=["DATE", "DocumentIdentifier"], suffix="_full")
    assert (joined["cluster_id"] == joined["cluster_id_full"]).all()
    assert (joined["cluster_DATE"] == joined["cluster_DATE_full"]).all()


def test_entity_encoded_and_decoded_titles_share_a_cluster():
    out = dedup(
        rows(
            [
                (20261001100000, "Fed &amp; ECB hold rates as inflation cools | Reuters"),
                (20261001101500, "Fed & ECB hold rates as inflation cools"),
                (20261001103000, "Fed &amp;amp; ECB hold rates as inflation cools - Yahoo Finance"),
            ]
        ),
        fake_embed,
        CFG,
    )
    assert out["cluster_id"].n_unique() == 1
    assert out["cluster_DATE"].unique().to_list() == [20261001100000]


def test_matches_only_within_window_and_empty_titles_stay_alone():
    out = dedup(
        rows(
            [
                (20261001100000, "Bitcoin hits a record high today"),
                (20261002100000, "Bitcoin hits a record high today"),  # exactly 24 h later: inside
                (20261003101500, "Bitcoin hits a record high today"),  # 24 h 15 min after the previous one
                (20261003101500, ""),
                (20261003103000, ""),
            ]
        ),
        fake_embed,
        CFG,
    )
    ids = out["cluster_id"].to_list()
    assert ids[0] == ids[1] != ids[2]
    assert len(set(ids[2:])) == 3


def test_clean_title():
    assert clean_title("Fed holds rates | Reuters") == "Fed holds rates"
    assert clean_title("Bitcoin &amp;amp; Ether rally - CoinDesk") == "Bitcoin & Ether rally"
    assert clean_title("Bitcoin - a new high") == "Bitcoin - a new high"  # too little left to strip
    assert clean_title("Fed - ECB - BoE meet") == "Fed - ECB - BoE meet"
    assert normalize_title(None) == ""


def test_scoring_is_deterministic_across_runs():
    pytest.importorskip("transformers")
    from mbpe.features.dedup import load_news_config
    from mbpe.features.sentiment import Scorer

    cfg = load_news_config()
    titles = [
        "Bitcoin climbs to record high",
        "Exchange halts withdrawals after hack",
        "Fed meets Wednesday",
    ] * 30
    for name, model in cfg.sentiment_models.items():
        a = Scorer(model, cfg.device, cfg.batch_size).score(titles)
        b = Scorer(model, cfg.device, cfg.batch_size).score(titles)
        assert a.equals(b), name
        assert a["score"].is_between(-1, 1).all()
        assert ((a["p_pos"] + a["p_neg"] + a["p_neu"]) - 1).abs().max() < 1e-5


def test_clusters_close_24h_after_their_first_member():
    # a near-copy every 6 h: each matches the previous one, but the cluster must not outlive 24 h
    start = datetime(2026, 10, 1)
    items = [
        (int(f"{start + timedelta(hours=6 * k):%Y%m%d%H%M%S}"), "ETF approval story" + "~" * k)
        for k in range(7)
    ]
    out = dedup(rows(items), lambda texts: fake_embed([t.replace("~", "") for t in texts]), CFG)
    ids = out["cluster_id"].to_list()
    assert ids[:5] == [ids[0]] * 5  # 0, 6, 12, 18, 24 h after the first member
    assert ids[5] != ids[0]  # 30 h: new cluster
    assert ids[6] == ids[5]


def test_score_titles_scores_each_exact_unique_title_once():
    from mbpe.features.sentiment import score_titles

    class CountingScorer:
        def __init__(self):
            self.seen = []

        def score(self, titles):
            self.seen += titles
            n = len(titles)
            p = pl.DataFrame({"p_pos": [0.5] * n, "p_neg": [0.25] * n, "p_neu": [0.25] * n})
            return p.with_columns(score=pl.col("p_pos") - pl.col("p_neg"))

    df = rows(
        [
            (20261001100000, "Fed &amp; ECB hold rates | Reuters"),
            (20261001101500, "Fed & ECB hold rates"),  # same normalized title
            (20261001103000, "Fed and ECB hold rates"),  # near-duplicate but a different exact title
            (20261001104500, ""),
        ]
    )
    scorer = CountingScorer()
    out = score_titles(df, scorer)
    assert scorer.seen == ["Fed & ECB hold rates", "Fed and ECB hold rates"]
    assert out["score"].to_list() == [0.25, 0.25, 0.25, None]


def test_incremental_chunks_reproduce_a_full_run():
    """Live use: each new day's rows, with the previous 48 h as fixed context, give the same as a full run."""
    rng = np.random.default_rng(3)
    start = datetime(2026, 10, 1)
    items = []
    for i in range(6 * 96):  # 6 days of 15-min batches
        for _ in range(rng.integers(0, 3)):
            story = (i // 30 + rng.integers(0, 5)) % 25  # stories drift and recur after > 24 h
            when = int(f"{start + timedelta(minutes=15 * i):%Y%m%d%H%M%S}")
            items.append((when, f"story {story} today" + "~" * rng.integers(0, 3)))
    df = rows(items)
    full = dedup(df, fake_embed, CFG).sort("DATE", "DocumentIdentifier")
    store = None
    for day in range(6):
        lo = int(f"{start + timedelta(days=day - 2):%Y%m%d%H%M%S}")
        hi = int(f"{start + timedelta(days=day + 1):%Y%m%d%H%M%S}")
        chunk = df.filter((pl.col("DATE") >= lo) & (pl.col("DATE") < hi))
        nxt = 0 if store is None else int(store["cluster_id"].max()) + 1
        out = dedup(chunk, fake_embed, CFG, fixed=store, next_cluster_id=nxt)
        new = out if store is None else out.join(store, on=["DATE", "DocumentIdentifier"], how="anti")
        cols = ["DATE", "DocumentIdentifier", "cluster_id", "cluster_DATE", "match_sim"]
        store = new.select(cols) if store is None else pl.concat([store, new.select(cols)])
    inc = store.sort("DATE", "DocumentIdentifier")
    assert inc.height == full.height
    for c in ("cluster_id", "cluster_DATE", "match_sim"):
        assert inc[c].equals(full[c]), c
    assert full["cluster_id"].n_unique() < full.height  # merges happened, incl. across chunk boundaries
