"""Headline deduplication: exact matches, then near-duplicates (MiniLM embeddings), within a rolling window.

The algorithm is online and causal, so the same code works live:
- rows are processed in batch-time order (DATE, then DocumentIdentifier)
- a row joins the cluster of its most similar EARLIER row from the previous `dedup_window_hours`, if the
  cosine similarity is >= `dedup_threshold` and that cluster's first member is at most
  `dedup_cluster_max_hours` older than the row (so later developments start a new cluster). Identical
  normalized titles share one embedding (similarity 1), so exact duplicates always join first. Otherwise
  the row starts a new cluster.
- a row's cluster therefore never depends on later rows. A cluster's first member is its earliest, so
  cluster_DATE (its availability anchor) is the earliest batch time of the story. Later copies add
  volume and never make the story available earlier.
- empty titles (title tag present but empty) are singletons: there is no text to compare.

Clusters are used only for counting and novelty features. Sentiment is scored per exact-unique normalized
title (sentiment.score_titles), not per cluster.

Usage: uv run python -m mbpe.features.dedup   # full backfill -> data/features/news_dedup.parquet + review
"""

import html
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np
import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict, Field

from mbpe.sources.gdelt_bq import AVAILABILITY_LAG, batch_time

NEWS_CONFIG = Path("config/news.yaml")
DEDUP_PATH = Path("data/features/news_dedup.parquet")
REVIEW_PATH = Path("labeling/dedup_review.md")


class NewsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dedup_threshold: float = Field(0.85, gt=0, le=1)
    dedup_window_hours: int = Field(24, gt=0)
    dedup_cluster_max_hours: int = Field(24, gt=0)
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    sentiment_models: dict[str, str] = {"finbert": "ProsusAI/finbert"}
    default_sentiment: str = "finbert"  # the sentiment model the news features use
    strong_negative_threshold: float = -0.5  # a cluster's mean score below this counts as strongly negative
    min_coverage: float = Field(0.8, gt=0, le=1)  # news windows / baselines below this batch coverage -> NaN
    news_output: Path = Path(
        "data/features/news.parquet"
    )  # where mbpe.features.news writes this config's features
    device: str = "cpu"
    batch_size: int = 64


def load_news_config(path: Path = NEWS_CONFIG) -> NewsConfig:
    return NewsConfig(**yaml.safe_load(path.read_text()))


# ---------- title cleaning ----------

_SEP = re.compile(r"\s+[|\-–—]\s+")  # " | ", " - ", " – ", " — "


def clean_title(title: str | None) -> str:
    """Readable title: HTML entities decoded, whitespace collapsed, trailing outlet names stripped.

    A trailing segment after " | " / " - " / " – " / " — " is treated as an outlet name when it has at most
    4 words and at least 3 words remain before it (e.g. "Fed holds rates | Reuters").
    """
    # ponytail: length heuristic, not a list of outlets. It also strips short taglines; review its output in
    # labeling/dedup_review.md and switch to a learned suffix list if it misfires.
    s = html.unescape(html.unescape(title or ""))  # GDELT titles are sometimes double-encoded (&amp;amp;)
    s = re.sub(r"\s+", " ", s).strip()
    while True:
        seps = list(_SEP.finditer(s))
        if not seps:
            return s
        last = seps[-1]
        head, tail = s[: last.start()], s[last.end() :]
        if len(re.findall(r"\w+", tail)) > 4 or len(re.findall(r"\w+", head)) < 3:
            return s
        s = head


def normalize_title(title: str | None) -> str:
    """Comparison key: clean_title, lowercased."""
    return clean_title(title).lower()


# ---------- clustering ----------


def embedder(cfg: NewsConfig) -> Callable[[list[str]], np.ndarray]:
    from sentence_transformers import SentenceTransformer  # heavy import, only when needed

    model = SentenceTransformer(cfg.embed_model, device=cfg.device, model_kwargs={"use_safetensors": True})

    def embed(texts: list[str]) -> np.ndarray:
        return model.encode(
            texts, batch_size=256, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        ).astype(np.float32)

    return embed


def dedup(df: pl.DataFrame, embed: Callable[[list[str]], np.ndarray], cfg: NewsConfig) -> pl.DataFrame:
    """Add cluster_id, cluster_DATE (earliest member's DATE), match_sim (to the joined row; null = new)."""
    df = df.sort("DATE", "DocumentIdentifier")
    t = df.select(batch_time().dt.epoch("s"))["DATE"].to_numpy()
    norm = [normalize_title(x) for x in df["title"].to_list()]
    uniq = sorted({n for n in norm if n})
    pos = {n: i for i, n in enumerate(uniq)}
    emb = embed(uniq) if uniq else np.zeros((0, 1), np.float32)
    inv = np.array([pos.get(n, -1) for n in norm])  # -1 = empty title
    window = cfg.dedup_window_hours * 3600
    max_age = cfg.dedup_cluster_max_hours * 3600

    n = len(norm)
    first_t = np.empty(n, np.int64)  # first member's time, by cluster id (ids are < n)
    cluster = np.empty(n, np.int64)
    sim_out = np.full(n, np.nan)
    lo = np.searchsorted(t, t - window, side="left")  # first row within the window of row i
    next_id = 0
    day = t // 86400
    for d in np.unique(day):  # one similarity block per day: day rows vs their window
        a, b = np.searchsorted(day, d), np.searchsorted(day, d, side="right")
        w = lo[a]
        rows, cols = inv[a:b], inv[w:b]
        sims = emb[np.maximum(rows, 0)] @ emb[np.maximum(cols, 0)].T
        sims[:, cols < 0] = -1.0  # empty-title rows can't be matched
        for i in range(a, b):
            k = None
            if inv[i] >= 0 and i > lo[i]:
                cand = sims[i - a, lo[i] - w : i - w]  # strictly earlier rows inside the window
                young = first_t[cluster[lo[i] : i]] >= t[i] - max_age  # their cluster is still open
                cand = np.where(young, cand, -1.0)
                j = int(cand.argmax())
                if cand[j] >= cfg.dedup_threshold:
                    k = lo[i] + j
                    sim_out[i] = cand[j]
            if k is None:
                cluster[i] = next_id
                first_t[next_id] = t[i]
                next_id += 1
            else:
                cluster[i] = cluster[k]
    out = df.with_columns(cluster_id=pl.Series(cluster), match_sim=pl.Series(sim_out).fill_nan(None))
    first = out.group_by("cluster_id").agg(cluster_DATE=pl.col("DATE").first())  # rows are in time order
    return out.join(first, on="cluster_id", how="left")


def cluster_available_at() -> pl.Expr:
    """When a story (cluster) becomes usable: its earliest member's batch time + 15 min."""
    return (
        pl.col("cluster_DATE").cast(pl.String).str.strptime(pl.Datetime("ms", "UTC"), "%Y%m%d%H%M%S")
        + AVAILABILITY_LAG
    ).alias("cluster_available_at")


# ---------- report ----------


def review(df: pl.DataFrame, cfg: NewsConfig, n: int = 20, seed: int = 7) -> str:
    """Markdown: cluster counts, size distribution, random clusters joined near the threshold, near misses."""
    sizes = df.group_by("cluster_id").agg(
        len=pl.len(), span_h=(batch_time().max() - batch_time().min()).dt.total_minutes() / 60
    )
    bins = sizes.select(
        pl.col("len").cut([1, 2, 5, 10, 50], labels=["1", "2", "3-5", "6-10", "11-50", ">50"]).alias("size")
    )
    dist = bins.group_by("size").len().sort("size")
    thr = cfg.dedup_threshold
    near = df.filter(pl.col("match_sim").is_between(thr, thr + 0.03))["cluster_id"].unique().sort()
    picked = near.sample(min(n, len(near)), seed=seed).sort() if len(near) else near
    lines = [
        f"# Dedup review (threshold {thr}, window {cfg.dedup_window_hours} h, "
        f"clusters capped at {cfg.dedup_cluster_max_hours} h from their first member)",
        "",
        f"- Rows: {df.height:,}; clusters: {sizes.height:,} ({df.height / sizes.height:.2f} per cluster)",
        f"- Largest cluster: {sizes['len'].max()} rows; longest span (first to last member): "
        f"{sizes['span_h'].max():.2f} h; clusters spanning > 12 h: {(sizes['span_h'] > 12).sum():,}",
        "",
        "| cluster size | clusters |",
        "|---|---|",
        *[f"| {r['size']} | {r['len']:,} |" for r in dist.iter_rows(named=True)],
        "",
        f"## {len(picked)} random clusters with a join at similarity {thr}–{thr + 0.03:.2f}",
        "First line = the cluster's earliest headline; indented lines joined it (similarity, batch time).",
        "",
    ]
    for cid in picked:
        members = df.filter(pl.col("cluster_id") == cid)
        lines.append(f"- **{clean_title(members['title'][0])}** ({members['DATE'][0]})")
        for r in members.slice(1).head(6).iter_rows(named=True):
            lines.append(f"  - {r['match_sim']:.3f} · {r['DATE']} · {clean_title(r['title'])}")
    return "\n".join(lines) + "\n"


def main() -> None:
    import time

    cfg = load_news_config()
    from mbpe.sources.gdelt_live import load_news_rows

    rows = load_news_rows()  # backfill + live batches
    t0 = time.perf_counter()
    out = dedup(rows, embedder(cfg), cfg)
    elapsed = time.perf_counter() - t0
    DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.select("DATE", "DocumentIdentifier", "cluster_id", "cluster_DATE", "match_sim").write_parquet(
        DEDUP_PATH
    )
    REVIEW_PATH.write_text(review(out, cfg) + f"\nRuntime: {elapsed / 60:.1f} min on {cfg.device}.\n")
    print(REVIEW_PATH.read_text())


if __name__ == "__main__":
    main()
