"""Live dedup store: appending new rows reproduces a full run."""

from datetime import datetime, timedelta

import numpy as np
import polars as pl

from mbpe.features.dedup import dedup
from mbpe.live.news import COLS, update_dedup
from tests.test_news import CFG, fake_embed, rows


def test_update_dedup_matches_a_full_run(tmp_path):
    rng = np.random.default_rng(5)
    start = datetime(2026, 10, 1)
    items = [
        (
            int(f"{start + timedelta(minutes=15 * i):%Y%m%d%H%M%S}"),
            f"story {(i // 20 + rng.integers(0, 4)) % 15} now~",
        )
        for i in range(4 * 96)
    ]
    df = rows(items)
    full = dedup(df, fake_embed, CFG).sort("DATE", "DocumentIdentifier")
    store = tmp_path / "news_dedup.parquet"
    cut = int(f"{start + timedelta(days=2, hours=7):%Y%m%d%H%M%S}")
    dedup(df.filter(pl.col("DATE") < cut), fake_embed, CFG).select(COLS).write_parquet(store)
    added = update_dedup(CFG, fake_embed, store, rows=df)
    assert added == df.filter(pl.col("DATE") >= cut).height
    assert update_dedup(CFG, fake_embed, store, rows=df) == 0  # nothing new
    inc = pl.read_parquet(store).sort("DATE", "DocumentIdentifier")
    for c in ("cluster_id", "cluster_DATE", "match_sim"):
        assert inc[c].equals(full[c]), c
