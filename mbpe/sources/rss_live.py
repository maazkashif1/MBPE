"""RSS news collector (Phase 6, forward only): headlines from crypto news feeds.

Usage: uv run python -m mbpe.sources.rss_live   # one collection run; schedule it every 15 minutes
       (example in deploy/rss-live.cron)

Each run fetches every feed in FEEDS and writes data/raw/rss_live/<YYYY-MM-DD>/<HHMM>.parquet with source,
item_id (guid, else link), title, link, published (the feed's own timestamp, UTC; may be missing or
revised) and fetched_at (our clock, UTC). Headlines only: no article text.
An item seen in several runs is stored each time; load_items() keeps its first sighting, whose fetched_at
is when the headline became available to us (the feed's `published` is reported, never used as availability).
<HHMM>.json holds per-feed run metadata. `possible_gap` is set when a feed's oldest item is newer than
the previous run, i.e. items may have scrolled off the feed in between.
"""

import json
import logging
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)
LIVE = Path("data/raw/rss_live")
FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss",
    "cointelegraph": "https://cointelegraph.com/rss",
    "theblock": "https://www.theblock.co/rss.xml",
    "decrypt": "https://decrypt.co/feed",
    "bitcoinmagazine": "https://bitcoinmagazine.com/feed",
}
UA = {"User-Agent": "mbpe-research/0.1 (headline collector)"}
ATOM = "{http://www.w3.org/2005/Atom}"
SCHEMA = {
    "source": pl.String,
    "item_id": pl.String,
    "title": pl.String,
    "link": pl.String,
    "published": pl.Datetime("ms", "UTC"),
    "fetched_at": pl.Datetime("ms", "UTC"),
}


def http_get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read()


def _time(text: str | None) -> datetime | None:
    """RFC 822 (RSS pubDate) or ISO 8601 (Atom) -> UTC; None if missing or unparseable."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_feed(raw: bytes, source: str, fetched_at: datetime) -> pl.DataFrame:
    """RSS 2.0 <item>s or Atom <entry>s -> rows in SCHEMA (items without a title are skipped)."""
    root = ET.fromstring(raw)
    rows = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or "").strip() or link
        if title and guid:
            rows.append((source, guid, title, link, _time(it.findtext("pubDate")), fetched_at))
    for it in root.iter(f"{ATOM}entry"):
        title = (it.findtext(f"{ATOM}title") or "").strip()
        el = it.find(f"{ATOM}link")
        link = (el.get("href", "") if el is not None else "").strip()
        guid = (it.findtext(f"{ATOM}id") or "").strip() or link
        when = it.findtext(f"{ATOM}published") or it.findtext(f"{ATOM}updated")
        if title and guid:
            rows.append((source, guid, title, link, _time(when), fetched_at))
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def last_run(root: Path) -> datetime | None:
    metas = sorted(root.glob("*/*.json"))
    return datetime.fromisoformat(json.loads(metas[-1].read_text())["run_at"]) if metas else None


def collect(get=http_get, root: Path = LIVE, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    prev = last_run(root)
    frames, per_feed = [], {}
    for source, url in FEEDS.items():
        try:
            df = parse_feed(get(url), source, datetime.now(UTC))
        except Exception as e:  # one broken feed must not stop the others; recorded in the run metadata
            per_feed[source] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
            log.warning("%s: %s", source, per_feed[source]["error"])
            continue
        oldest = df["published"].min()
        per_feed[source] = {
            "ok": True,
            "items": df.height,
            "oldest_published": oldest.isoformat() if oldest else None,
            "possible_gap": bool(prev and oldest and oldest > prev),
        }
        frames.append(df)
    out = root / f"{now:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    data = pl.concat(frames) if frames else pl.DataFrame(schema=SCHEMA)
    data.write_parquet(out / f"{now:%H%M}.parquet")
    meta = {"run_at": now.isoformat(), "previous_run": prev.isoformat() if prev else None, "feeds": per_feed}
    (out / f"{now:%H%M}.json").write_text(json.dumps(meta, indent=1))  # written last: marks a complete run
    for source, m in per_feed.items():
        if m.get("possible_gap"):
            log.warning("%s: oldest item newer than the previous run; items may be missing", source)
    return meta


def load_items(root: Path = LIVE) -> pl.DataFrame:
    """One row per (source, item), at its first sighting (fetched_at = when it became available to us)."""
    files = [p.with_suffix(".parquet") for p in sorted(root.glob("*/*.json"))]
    if not files:
        return pl.DataFrame(schema=SCHEMA)
    return (
        pl.concat([pl.read_parquet(f) for f in files])
        .sort("fetched_at")
        .unique(["source", "item_id"], keep="first", maintain_order=True)
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(collect(), indent=1))
