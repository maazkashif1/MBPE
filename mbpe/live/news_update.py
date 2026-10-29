import logging
import shutil
from pathlib import Path

import polars as pl

from mbpe.data.gdelt_live import load_news_rows
from mbpe.features.news import DEDUP_PATH
from mbpe.features.news_dedup import NewsConfig, dedup, embedder, load_news_config
from mbpe.features.sentiment import score_all

log = logging.getLogger(__name__)
LIVE_DEDUP = Path("data/live/news_dedup.parquet")
KEY = ["DATE", "DocumentIdentifier"]
COLS = [*KEY, "cluster_id", "cluster_DATE", "match_sim"]
CONTEXT_HOURS = 48


def _shift_date(date: int, hours: int) -> int:
    from datetime import datetime, timedelta

    return int(f"{datetime.strptime(str(date), '%Y%m%d%H%M%S') - timedelta(hours=hours):%Y%m%d%H%M%S}")


def update_dedup(cfg: NewsConfig | None = None, embed=None, store_path: Path = LIVE_DEDUP, rows=None) -> int:
    cfg = cfg or load_news_config()
    if not store_path.exists():
        store_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEDUP_PATH, store_path)
    store = pl.read_parquet(store_path)
    rows = load_news_rows() if rows is None else rows
    new = rows.join(store, on=KEY, how="anti")
    if not new.height:
        return 0
    lo = _shift_date(int(new["DATE"].min()), CONTEXT_HOURS)
    context = rows.filter(pl.col("DATE") >= lo).join(store.filter(pl.col("DATE") >= lo), on=KEY, how="semi")
    out = dedup(
        pl.concat([context, new]),
        embed or embedder(cfg),
        cfg,
        fixed=store.filter(pl.col("DATE") >= lo),
        next_cluster_id=int(store["cluster_id"].max()) + 1,
    )
    added = out.join(store, on=KEY, how="anti").select(COLS)
    tmp = store_path.with_name(store_path.name + ".part")
    pl.concat([store, added.cast(store.schema)]).write_parquet(tmp)
    tmp.replace(store_path)
    log.info("dedup: %d new rows, %d new clusters", added.height, added["match_sim"].is_null().sum())
    return added.height


def update_scores(cfg: NewsConfig | None = None) -> None:
    cfg = cfg or load_news_config()
    score_all(cfg, models=[cfg.default_sentiment])
