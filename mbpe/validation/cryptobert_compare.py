"""Rung 3 with CryptoBERT news features, reported next to FinBERT (secondary comparison; SCOPE.md).

FinBERT stays the primary scorer; this is report-only and can't replace it.

Usage:
  uv run python -m mbpe.validation.cryptobert_compare tune       # rung 3, label set A, taker (30 x 18)
  uv run python -m mbpe.validation.cryptobert_compare evaluate   # untuned A/B x both scenarios, tuned A/taker

Same procedure as Phase 3 step 2; only `news_features` differs. FinBERT results are read from step 2's trades.
Outputs: reports/phase3_step2_cryptobert/.
"""

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.validation.harness import LABEL_SETS, RUNGS, run_label_set, summarize
from mbpe.validation.tuning import tune
from mbpe.validation.walk_forward import load_eval_config

OUT = Path("reports/phase3_step2_cryptobert")
CRYPTOBERT_NEWS = Path("data/features/news_cryptobert.parquet")
FINBERT_TRADES = Path("data/features/phase3_step2_trades.parquet")


def cfg_cryptobert():
    return load_eval_config().model_copy(update={"news_features": CRYPTOBERT_NEWS})


def run_tuning() -> None:
    trials, best = tune(cfg_cryptobert(), 3)
    OUT.mkdir(parents=True, exist_ok=True)
    trials.write_csv(OUT / "tuning_r3.csv")
    (OUT / "best_params_r3.json").write_text(json.dumps(best, indent=2))
    print(f"{trials.height} trials")


def tag(trades: pl.DataFrame, scorer: str) -> pl.DataFrame:
    return trades.with_columns(scorer=pl.lit(scorer))


def evaluate() -> None:
    cfg = cfg_cryptobert()
    parts = []
    for ls in LABEL_SETS:
        tr, _ = run_label_set(ls, cfg, models=("lgbm",), rungs={3: RUNGS[3]}, baselines=False)
        parts.append(tag(tr, "cryptobert"))
    best = {(3, int(k)): v for k, v in json.loads((OUT / "best_params_r3.json").read_text()).items()}
    tr, _ = run_label_set(
        "A",
        cfg,
        models=("lgbm",),
        rungs={3: RUNGS[3]},
        scenarios=("taker",),
        tuned_params=best,
        baselines=False,
    )
    parts.append(tag(tr, "cryptobert"))
    fin = pl.read_parquet(FINBERT_TRADES).filter(
        (pl.col("rung") == 3)
        & pl.col("strategy").str.contains("lgbm")
        & ~pl.col("strategy").str.contains("shuffled")
    )
    trades = pl.concat([tag(fin, "finbert"), *parts], how="diagonal_relaxed")
    rows_fold, rows_all = [], []
    for scorer in ("finbert", "cryptobert"):
        pf, ov = summarize(trades.filter(pl.col("scorer") == scorer).drop("scorer"), cfg)
        rows_fold.append(pf.with_columns(scorer=pl.lit(scorer)))
        rows_all.append(ov.with_columns(scorer=pl.lit(scorer)))
    pf, ov = pl.concat(rows_fold), pl.concat(rows_all)
    OUT.mkdir(parents=True, exist_ok=True)
    pf.write_csv(OUT / "fold_metrics.csv")
    ov.write_csv(OUT / "overall_metrics.csv")
    (OUT / "report.md").write_text(report(pf, ov))
    print((OUT / "report.md").read_text())


def _f(x, nd=3):
    return "–" if x is None or not np.isfinite(x) else f"{x:+.{nd}f}"


def report(pf: pl.DataFrame, ov: pl.DataFrame) -> str:
    out = [
        "| label set | scenario | model | scorer | trades | mean net R [95% CI] | net bps | gross bps "
        "| random: net R (median) | vol filter: net R (median) | folds won vs random | vs vol filter |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (ls, scen, strat), _ in ov.filter(pl.col("seed") == -1).group_by(
        "label_set", "scenario", "strategy", maintain_order=True
    ):
        for scorer in ("finbert", "cryptobert"):
            o = ov.filter(
                (pl.col("label_set") == ls) & (pl.col("scenario") == scen) & (pl.col("scorer") == scorer)
            )
            m = o.filter((pl.col("strategy") == strat) & (pl.col("seed") == -1))
            if not m.height:
                continue
            r = m.row(0, named=True)
            won, base = [], []
            for b in ("random", "volfilter"):
                bo = o.filter(pl.col("strategy") == f"{b}_vs_{strat}")
                base.append(_f(bo["mean_R"].median()))
                p = pf.filter(
                    (pl.col("label_set") == ls) & (pl.col("scenario") == scen) & (pl.col("scorer") == scorer)
                )
                mf = p.filter((pl.col("strategy") == strat) & (pl.col("seed") == -1) & (pl.col("trades") > 0))
                bf = (
                    p.filter(pl.col("strategy") == f"{b}_vs_{strat}")
                    .group_by("fold")
                    .agg(med=pl.col("mean_R").median())
                )
                j = mf.join(bf, on="fold")
                won.append(f"{(j['mean_R'] > j['med']).sum()}/18")
            out.append(
                f"| {ls} | {scen} | {strat} r3 | {scorer} | {r['trades']:,} | {_f(r['mean_R'])} "
                f"[{_f(r['ci_lo'])}, {_f(r['ci_hi'])}] | {_f(r['bps_per_trade'], 1)} "
                f"| {_f(r['gross_bps_per_trade'], 1)} | {base[0]} | {base[1]} | {won[0]} | {won[1]} |"
            )
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    {"tune": run_tuning, "evaluate": evaluate}[sys.argv[1]]()
