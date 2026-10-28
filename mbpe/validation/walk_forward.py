"""Walk-forward splitter, uniqueness weights and the holdout guard (protocol in SCOPE.md).

- Test folds: consecutive `fold_months` blocks from `first_test_fold` to the holdout start.
- Training: an expanding window from `eval_start` to the fold start.
- Purge: drop training rows whose label window (decision time -> exit time) overlaps the test fold.
- Embargo: `embargo_hours` after each test fold. All training precedes the fold, so it never binds; it's
  applied anyway so the splitter stays correct if folds ever move.
- Validation: the last `val_fraction` of the training window, purged the same way from the rows before it.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict

EVAL_CONFIG = Path("config/eval.yaml")
HOLDOUT_LOG = Path("data/logs/holdout_access.jsonl")
MIN = 60_000


class EvalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eval_start: datetime
    holdout_start: datetime
    first_test_fold: datetime
    fold_months: int
    embargo_hours: int
    val_fraction: float
    min_val_trades: int
    threshold_candidates: int
    blackout_before_minutes: int
    blackout_horizon_minutes: int
    latency_minutes: int
    risk_per_trade: float
    random_seeds: int
    bootstrap_block: int
    bootstrap_samples: int
    lgbm: dict
    lgbm_max_rounds: int
    lgbm_early_stopping: int
    logreg_C: float
    news_features: Path = Path("data/features/news.parquet")  # which news config the harness evaluates
    phase4_features: Path | None = None  # Phase 4: NFP / order-book features joined when set
    blackout_events: tuple[str, ...] = ("CPI", "FOMC")  # Phase 4 rung 4b adds "NFP"


def load_eval_config(path: Path = EVAL_CONFIG) -> EvalConfig:
    return EvalConfig(**yaml.safe_load(path.read_text()))


def to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# ---------- holdout guard ----------


class HoldoutError(RuntimeError):
    pass


def guard_holdout(
    df: pl.DataFrame, cfg: EvalConfig, allow_holdout: bool = False, reason: str = "", log: Path = HOLDOUT_LOG
) -> pl.DataFrame:
    """Raise if `df` reaches the holdout (decision or exit time) unless allowed; log every allowed access."""
    h = to_ms(cfg.holdout_start)
    cols = [c for c in df.columns if c == "decision_time" or c.endswith("exit_time")]
    touches = df.select(pl.any_horizontal([pl.col(c).dt.epoch("ms") >= h for c in cols]).sum()).item()
    if touches and not allow_holdout:
        raise HoldoutError(
            f"{touches} rows reach the holdout (>= {cfg.holdout_start}); pass allow_holdout=True"
        )
    if touches:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(
                json.dumps({"at": datetime.now(UTC).isoformat(), "rows": touches, "reason": reason}) + "\n"
            )
    return df


# ---------- folds ----------


@dataclass(frozen=True)
class Fold:
    index: int
    test_start: int  # epoch ms
    test_end: int  # exclusive


def make_folds(cfg: EvalConfig) -> list[Fold]:
    folds, start, k = [], cfg.first_test_fold, 0
    while start < cfg.holdout_start:
        m = start.month - 1 + cfg.fold_months
        end = min(start.replace(year=start.year + m // 12, month=m % 12 + 1), cfg.holdout_start)
        folds.append(Fold(k, to_ms(start), to_ms(end)))
        start, k = end, k + 1
    return folds


def split(decision: np.ndarray, exit_: np.ndarray, fold: Fold, cfg: EvalConfig) -> dict[str, np.ndarray]:
    """Boolean masks train / val / test over rows with decision and exit times (epoch ms)."""
    start, end = fold.test_start, fold.test_end
    test = (decision >= start) & (decision < end)
    overlaps_test = (decision < end) & (exit_ >= start)  # label window [decision, exit] meets [start, end)
    embargo = (decision >= end) & (decision < end + cfg.embargo_hours * 3_600_000)
    pool = (decision >= to_ms(cfg.eval_start)) & (decision < start) & ~overlaps_test & ~embargo
    val_start = int(to_ms(cfg.eval_start) + (1 - cfg.val_fraction) * (start - to_ms(cfg.eval_start)))
    val = pool & (decision >= val_start)
    train = pool & (decision < val_start) & (exit_ < val_start)  # purged against the validation slice
    return {"train": train, "val": val, "test": test}


# ---------- sample weights ----------


def uniqueness(start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """Average uniqueness of each label over its window [start, end) on a 1-minute grid (epoch ms in).

    At each minute u, uniqueness = 1 / (number of labels whose window contains u); a label's weight is the
    mean over its window's minutes.
    """
    s = (start // MIN).astype(np.int64)
    e = np.maximum((end // MIN).astype(np.int64), s + 1)
    base = s.min()
    s, e = s - base, e - base
    diff = np.zeros(e.max() + 1, np.int64)
    np.add.at(diff, s, 1)
    np.add.at(diff, e, -1)
    conc = np.cumsum(diff)[:-1]
    inv = np.where(conc > 0, 1.0 / np.maximum(conc, 1), 0.0)
    p = np.concatenate([[0.0], np.cumsum(inv)])
    return (p[e] - p[s]) / (e - s)
