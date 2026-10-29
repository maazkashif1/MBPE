from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from mbpe.data import reddit as rl

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


class FakeReddit:
    def __init__(self, posts):
        self.posts = posts

    def subreddit(self, name):
        items = [
            SimpleNamespace(id=i, title=t, created_utc=c.timestamp(), score=99, num_comments=7)
            for i, t, c in self.posts[name]
        ]
        return SimpleNamespace(new=lambda limit: items[:limit])


def test_collect_keeps_only_static_fields_and_first_sighting(tmp_path):
    posts = {"Bitcoin": [("a", "BTC up", T0 - timedelta(minutes=5))], "CryptoCurrency": []}
    rl.collect(FakeReddit(posts), root=tmp_path, now=T0)
    posts["CryptoCurrency"] = [("b", "ETH news", T0 + timedelta(minutes=3))]
    meta = rl.collect(FakeReddit(posts), root=tmp_path, now=T0 + timedelta(minutes=15))
    df = rl.load_posts(tmp_path)
    assert df.columns == list(rl.SCHEMA)
    assert sorted(df["post_id"]) == ["a", "b"]
    sightings = pl.concat([pl.read_parquet(f) for f in sorted(tmp_path.glob("*/*.parquet"))])
    a = sightings.filter(pl.col("post_id") == "a")["fetched_at"]
    assert len(a) == 2 and df.filter(pl.col("post_id") == "a")["fetched_at"][0] == a.min()
    assert meta["previous_run"] == T0.isoformat()
    assert not any(m["possible_gap"] for m in meta["subreddits"].values())


def test_full_page_newer_than_previous_run_flags_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "LIMIT", 2)
    rl.collect(FakeReddit({"Bitcoin": [], "CryptoCurrency": []}), root=tmp_path, now=T0)
    late = T0 + timedelta(minutes=10)
    posts = {"Bitcoin": [("x", "a", late), ("y", "b", late)], "CryptoCurrency": []}
    meta = rl.collect(FakeReddit(posts), root=tmp_path, now=T0 + timedelta(minutes=15))
    assert meta["subreddits"]["Bitcoin"]["possible_gap"]
    assert not meta["subreddits"]["CryptoCurrency"]["possible_gap"]


def test_missing_credentials_raise_without_leaking(monkeypatch):
    for k in rl.ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="REDDIT_CLIENT_ID"):
        rl.client()
