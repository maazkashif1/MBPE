import numpy as np
import polars as pl

from mbpe.data.binance_rest import load_live_data
from mbpe.features.market import build_features, load_feature_config
from mbpe.features.news import build_news_features, load_inputs
from mbpe.features.news_dedup import load_news_config
from mbpe.live.news_update import LIVE_DEDUP


def live_features(t_ms: int, data: dict | None = None, news_inputs=None) -> pl.DataFrame:
    at = np.array([t_ms], dtype=np.int64)
    market = build_features(data or load_live_data(), load_feature_config(), at=at)
    cfg = load_news_config()
    inp = news_inputs or load_inputs(cfg, require_scores=False, dedup_path=LIVE_DEDUP)
    news = build_news_features(inp.prep, inp.coverage, inp.events, cfg, at=at)
    return market.join(news, on="decision_time", how="left")
