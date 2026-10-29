from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from mbpe.data import rss as rl

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>SEC approves spot ETF</title><link>https://a.com/1</link><guid>a-1</guid>
<pubDate>Wed, 28 Oct 2026 11:50:00 +0000</pubDate><description>body not stored</description></item>
<item><title>  </title><link>https://a.com/2</link></item>
<item><title>No guid here</title><link>https://a.com/3</link><pubDate>garbage</pubDate></item>
</channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Bitcoin hits a high</title><link href="https://b.com/9"/><id>tag:b,9</id>
<updated>2026-10-28T11:55:00Z</updated></entry></feed>"""


def test_parse_rss_and_atom():
    r = rl.parse_feed(RSS, "a", T0)
    assert r["title"].to_list() == ["SEC approves spot ETF", "No guid here"]
    assert r["item_id"].to_list() == ["a-1", "https://a.com/3"]
    assert r["published"][0] == datetime(2026, 10, 28, 11, 50, tzinfo=UTC) and r["published"][1] is None
    assert "description" not in r.columns
    a = rl.parse_feed(ATOM, "b", T0)
    assert a.row(0)[1:5] == (
        "tag:b,9",
        "Bitcoin hits a high",
        "https://b.com/9",
        datetime(2026, 10, 28, 11, 55, tzinfo=UTC),
    )


def test_collect_first_sighting_broken_feed_and_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "FEEDS", {"a": "u-a", "b": "u-b"})
    bodies = {"u-a": RSS, "u-b": ATOM}

    def get(url):
        if url == "u-b" and bodies.get("broken"):
            raise TimeoutError("feed down")
        return bodies[url]

    rl.collect(get, root=tmp_path, now=T0)
    bodies["broken"] = True
    meta = rl.collect(get, root=tmp_path, now=T0 + timedelta(minutes=15))
    assert meta["feeds"]["b"]["ok"] is False and "feed down" in meta["feeds"]["b"]["error"]
    assert meta["feeds"]["a"]["possible_gap"] is False
    items = rl.load_items(tmp_path)
    assert items.height == 3
    seen = pl.concat([pl.read_parquet(f) for f in sorted(tmp_path.glob("*/*.parquet"))])
    a1 = seen.filter(pl.col("item_id") == "a-1")["fetched_at"]
    assert len(a1) == 2 and items.filter(pl.col("item_id") == "a-1")["fetched_at"][0] == a1.min()


def test_gap_flag_when_feed_scrolled_past_previous_run(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "FEEDS", {"a": "u-a"})
    rl.collect(lambda u: RSS, root=tmp_path, now=T0 - timedelta(hours=2))
    meta = rl.collect(
        lambda u: RSS.replace(b"garbage", b"Wed, 28 Oct 2026 11:59:00 +0000"), root=tmp_path, now=T0
    )
    assert meta["feeds"]["a"]["possible_gap"] is True


@pytest.mark.parametrize("text", [None, "", "not a date"])
def test_bad_timestamps_are_none(text):
    assert rl._time(text) is None
