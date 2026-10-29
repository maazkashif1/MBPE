"""Headline sentiment: hand-labeling sample, model scoring, one title per dedup cluster.

Usage:
  uv run python -m mbpe.features.sentiment sample         # labeling/sample.csv (+ private key in data/)
  uv run python -m mbpe.features.sentiment score-sample   # both models on the 200 sample titles + throughput
  uv run python -m mbpe.features.sentiment score-all      # every unique title, every model (incremental)

Per title: p_pos, p_neg, p_neu and score = p_pos - p_neg. Models load safetensors weights only.
Labels are mapped by name: positive/bullish -> pos, negative/bearish -> neg, neutral -> neu.
Empty titles are never scored (they count toward news volume only).
"""

import csv
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.features.dedup import NewsConfig, clean_title, load_news_config, normalize_title
from mbpe.sources.gdelt_bq import CRYPTO_KEYWORDS, OUT

SAMPLE_CSV = Path("labeling/sample.csv")
SAMPLE_KEY = Path(
    "data/labeling/sample_key.parquet"
)  # dates/sources/keywords: kept out of the labeler's view
SAMPLE_SCORES = Path("data/labeling/sample_scores.parquet")  # also hidden until labeling is done
SAMPLE_SIZE, SEED, FIRST_YEAR = 200, 20261027, 2021
LABEL_MAP = {"positive": "pos", "bullish": "pos", "negative": "neg", "bearish": "neg", "neutral": "neu"}


# ---------- labeling sample ----------


def make_sample(rows: pl.DataFrame) -> pl.DataFrame:
    """200 distinct headlines, stratified by year (2021+) x keyword group (crypto / macro), fixed seed."""
    df = (
        rows.with_columns(
            year=pl.col("DATE") // 10**10,
            group=pl.when(pl.col("keywords").list.eval(pl.element().is_in(list(CRYPTO_KEYWORDS))).list.any())
            .then(pl.lit("crypto"))
            .otherwise(pl.lit("macro")),
            clean=pl.col("title").map_elements(clean_title, return_dtype=pl.String),
        )
        .filter((pl.col("year") >= FIRST_YEAR) & (pl.col("clean") != ""))
        .with_columns(norm=pl.col("clean").str.to_lowercase())
        .sort("DATE", "DocumentIdentifier")
        .unique("norm", keep="first", maintain_order=True)  # no repeated headline in the sample
    )
    strata = df.select("year", "group").unique().sort("year", "group").rows()
    base, extra = divmod(SAMPLE_SIZE, len(strata))
    picks = [
        df.filter((pl.col("year") == y) & (pl.col("group") == g)).sample(base + (i < extra), seed=SEED + i)
        for i, (y, g) in enumerate(strata)
    ]
    sample = pl.concat(picks).sample(fraction=1.0, shuffle=True, seed=SEED)  # mix strata in the CSV
    return sample.with_columns(
        id=pl.format("h{}", pl.int_range(1, pl.len() + 1).cast(pl.String).str.zfill(3))
    )


def write_sample() -> None:
    rows = pl.concat([pl.read_parquet(f) for f in sorted(OUT.glob("[0-9]*.parquet"))])
    s = make_sample(rows)
    SAMPLE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SAMPLE_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "label"])
        w.writerows((r["id"], r["clean"], "") for r in s.iter_rows(named=True))
    SAMPLE_KEY.parent.mkdir(parents=True, exist_ok=True)
    s.select(
        "id", "DATE", "DocumentIdentifier", "SourceCommonName", "year", "group", "keywords", "title"
    ).write_parquet(SAMPLE_KEY)
    print(s.group_by("year", "group").len().sort("year", "group"))
    print(f"wrote {SAMPLE_CSV} ({s.height} rows) and {SAMPLE_KEY}")


# ---------- scoring ----------


class Scorer:
    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 64):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch, self.device, self.batch_size = torch, device, batch_size
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name, use_safetensors=True)
        self.model.to(device).eval()
        labels = {i: LABEL_MAP[name.lower()] for i, name in self.model.config.id2label.items()}
        self.order = [next(i for i, v in labels.items() if v == k) for k in ("pos", "neg", "neu")]

    def score(self, titles: list[str]) -> pl.DataFrame:
        probs = []
        with self.torch.inference_mode():
            for i in range(0, len(titles), self.batch_size):
                enc = self.tokenizer(
                    titles[i : i + self.batch_size],
                    padding=True,
                    truncation=True,
                    max_length=64,
                    return_tensors="pt",
                ).to(self.device)
                p = self.torch.softmax(self.model(**enc).logits.float(), dim=-1).cpu().numpy()
                probs.append(p[:, self.order])
        p = np.concatenate(probs) if probs else np.zeros((0, 3), np.float32)
        return pl.DataFrame({"p_pos": p[:, 0], "p_neg": p[:, 1], "p_neu": p[:, 2]}).with_columns(
            score=pl.col("p_pos") - pl.col("p_neg")
        )


def score_titles(rows: pl.DataFrame, scorer: Scorer) -> pl.DataFrame:
    """Score every exact-unique normalized title once and map the scores back to all its rows.

    The text scored is the clean_title of the title's earliest row. Empty titles get no score (volume only).
    """
    keyed = rows.with_columns(norm=pl.col("title").map_elements(normalize_title, return_dtype=pl.String))
    texts = (
        keyed.filter(pl.col("norm") != "")
        .sort("DATE", "DocumentIdentifier")
        .group_by("norm", maintain_order=True)
        .agg(pl.col("title").first())
        .with_columns(text=pl.col("title").map_elements(clean_title, return_dtype=pl.String))
    )
    scores = pl.concat([texts.select("norm"), scorer.score(texts["text"].to_list())], how="horizontal")
    return keyed.join(scores, on="norm", how="left")


SCORES_DIR = Path("data/features")
CHUNK = 50_000


def scores_path(model: str) -> Path:
    return SCORES_DIR / f"sentiment_{model}.parquet"


def unique_titles(rows: pl.DataFrame) -> pl.DataFrame:
    """One row per exact-unique non-empty normalized title: norm, and the clean title of its earliest row."""
    return (
        rows.with_columns(norm=pl.col("title").map_elements(normalize_title, return_dtype=pl.String))
        .filter(pl.col("norm") != "")
        .sort("DATE", "DocumentIdentifier")
        .group_by("norm", maintain_order=True)
        .agg(pl.col("title").first())
        .select("norm", text=pl.col("title").map_elements(clean_title, return_dtype=pl.String))
    )


def score_all(cfg: NewsConfig, models: list[str] | None = None) -> None:
    """Score every exact-unique normalized title with every configured model (data/features/sentiment_*).

    Incremental and resumable: titles already in a model's file are skipped, progress is saved every CHUNK
    titles. Keyed by `norm`.
    """
    from mbpe.sources.gdelt_live import load_news_rows

    titles = unique_titles(load_news_rows())
    print(f"{titles.height:,} unique titles", flush=True)
    for name, model_name in cfg.sentiment_models.items():
        if models is not None and name not in models:  # live: only the scorer the features use
            continue
        out = scores_path(name)
        done = pl.read_parquet(out) if out.exists() else None
        todo = titles if done is None else titles.join(done.select("norm"), on="norm", how="anti")
        print(f"{name}: {0 if done is None else done.height:,} scored, {todo.height:,} to go", flush=True)
        if not todo.height:
            continue
        scorer = Scorer(model_name, cfg.device, cfg.batch_size)
        t0 = time.perf_counter()
        for i in range(0, todo.height, CHUNK):
            part = todo.slice(i, CHUNK)
            scored = pl.concat([part.select("norm"), scorer.score(part["text"].to_list())], how="horizontal")
            done = scored if done is None else pl.concat([done, scored])
            tmp = out.with_name(out.name + ".part")
            done.write_parquet(tmp)
            tmp.replace(out)
            rate = (i + part.height) / (time.perf_counter() - t0)
            print(f"{name}: {i + part.height:,}/{todo.height:,} ({rate:.0f} titles/s)", flush=True)


def score_sample(cfg: NewsConfig) -> None:
    titles = pl.read_csv(SAMPLE_CSV)
    results, timing = [], []
    for name, model_name in cfg.sentiment_models.items():
        scorer = Scorer(model_name, cfg.device, cfg.batch_size)
        scorer.score(titles["title"].head(8).to_list())  # warm-up (device kernels), not timed
        t0 = time.perf_counter()
        s = scorer.score(titles["title"].to_list())
        dt = time.perf_counter() - t0
        timing.append({"model": name, "device": cfg.device, "titles": titles.height, "seconds": dt,
                       "titles_per_s": titles.height / dt})  # fmt: skip
        results.append(pl.concat([titles.select("id"), s], how="horizontal").with_columns(model=pl.lit(name)))
    SAMPLE_SCORES.parent.mkdir(parents=True, exist_ok=True)
    pl.concat(results).write_parquet(SAMPLE_SCORES)
    print(pl.DataFrame(timing))


if __name__ == "__main__":
    {
        "sample": write_sample,
        "score-sample": lambda: score_sample(load_news_config()),
        "score-all": lambda: score_all(load_news_config()),
    }[sys.argv[1]]()


__all__ = ["Scorer", "make_sample", "normalize_title", "score_titles"]
