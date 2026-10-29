import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)
LIVE = Path("data/raw/reddit_live")
SUBREDDITS = ("Bitcoin", "CryptoCurrency")
LIMIT = 100
ENV = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT")
SCHEMA = {
    "post_id": pl.String,
    "subreddit": pl.String,
    "title": pl.String,
    "created_utc": pl.Datetime("ms", "UTC"),
    "fetched_at": pl.Datetime("ms", "UTC"),
}


def client():
    import praw

    missing = [k for k in ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"set {', '.join(missing)} in the environment (never in the repo)")
    r = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ["REDDIT_USER_AGENT"],
        check_for_updates=False,
    )
    r.read_only = True
    return r


def fetch(reddit, subreddit: str, limit: int = LIMIT) -> pl.DataFrame:
    fetched_at = datetime.now(UTC)
    rows = [
        {
            "post_id": s.id,
            "subreddit": subreddit,
            "title": s.title,
            "created_utc": datetime.fromtimestamp(s.created_utc, UTC),
            "fetched_at": fetched_at,
        }
        for s in reddit.subreddit(subreddit).new(limit=limit)
    ]
    return pl.DataFrame(rows, schema=SCHEMA)


def last_run(root: Path) -> datetime | None:
    metas = sorted(root.glob("*/*.json"))
    return datetime.fromisoformat(json.loads(metas[-1].read_text())["run_at"]) if metas else None


def collect(reddit=None, root: Path = LIVE, now: datetime | None = None) -> dict:
    reddit = reddit or client()
    now = now or datetime.now(UTC)
    prev = last_run(root)
    frames, per_sub = [], {}
    for sub in SUBREDDITS:
        df = fetch(reddit, sub)
        oldest = df["created_utc"].min()
        per_sub[sub] = {
            "posts": df.height,
            "oldest_created": oldest.isoformat() if oldest else None,
            "possible_gap": bool(prev and df.height >= LIMIT and oldest > prev),
        }
        frames.append(df)
    out = root / f"{now:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(out / f"{now:%H%M}.parquet")
    meta = {
        "run_at": now.isoformat(),
        "previous_run": prev.isoformat() if prev else None,
        "subreddits": per_sub,
    }
    (out / f"{now:%H%M}.json").write_text(json.dumps(meta, indent=1))
    for sub, m in per_sub.items():
        if m["possible_gap"]:
            log.warning("r/%s: full page newer than the previous run; posts may be missing", sub)
    return meta


def load_posts(root: Path = LIVE) -> pl.DataFrame:
    files = [p.with_suffix(".parquet") for p in sorted(root.glob("*/*.json"))]
    if not files:
        return pl.DataFrame(schema=SCHEMA)
    return (
        pl.concat([pl.read_parquet(f) for f in files])
        .sort("fetched_at")
        .unique("post_id", keep="first", maintain_order=True)
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(collect(), indent=1))
