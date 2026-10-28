"""Phase 3 holdout test, run once (pre-registered in SCOPE.md, "Phase 3 holdout test").

Usage: uv run python -m mbpe.validation.holdout
Outputs: reports/phase3_holdout/{overall_metrics.csv, fold_metrics.csv, thresholds.csv, tuning.csv,
best_params.json}, data/features/phase3_holdout_trades.parquet. Every holdout load is logged by the guard in
data/logs/holdout_access.jsonl.

One fit per configuration on everything before the holdout (fold 18 = the whole holdout), then the trades are
reported in two ~3-month holdout folds (18, 19). Same procedure as Phase 3 step 2 / 2b otherwise.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from mbpe.validation.cryptobert_compare import CRYPTOBERT_NEWS
from mbpe.validation.harness import LABEL_SETS, RUNGS, load_dataset, run_label_set, summarize
from mbpe.validation.tuning import tune
from mbpe.validation.walk_forward import Fold, load_eval_config, to_ms

OUT = Path("reports/phase3_holdout")
TRADES = Path("data/features/phase3_holdout_trades.parquet")
SPLIT = datetime(2026, 7, 26, tzinfo=UTC)  # start of holdout fold 2
HOUR = 3_600_000
SCORERS = {"finbert": None, "cryptobert": CRYPTOBERT_NEWS}


def main() -> None:
    cfg = load_eval_config()
    h = cfg.holdout_start
    data = {
        (scorer, ls): load_dataset(ls, cfg, allow_holdout=True, news_path=news)
        for scorer, news in SCORERS.items()
        for ls in LABEL_SETS
    }
    last = min(
        int(d.filter(pl.col("decision_time") >= h)["decision_time"].dt.epoch("ms").max())
        for d in data.values()
    )
    fit_fold = Fold(18, to_ms(h), last + HOUR)  # fit once on everything before the holdout
    report_folds = [Fold(18, to_ms(h), to_ms(SPLIT)), Fold(19, to_ms(SPLIT), last + HOUR)]
    folds = [fit_fold]

    parts, thr, trials, best_params = [], [], [], {}

    def keep(tr, th, scorer, strategy=None):
        parts.append(tr.with_columns(scorer=pl.lit(scorer)))
        thr.append(
            th.with_columns(scorer=pl.lit(scorer), **({"model": pl.lit(strategy)} if strategy else {}))
        )

    # tuning first: 30 trials on the final validation slice per tuned configuration
    tuned = {}
    for scorer, rungs in (("finbert", (1, 2, 3)), ("cryptobert", (3,))):
        for r in rungs:
            t, b = tune(cfg, r, folds=folds, df=data[(scorer, "A")])
            trials.append(t.with_columns(scorer=pl.lit(scorer)))
            tuned[(scorer, r)] = b[18]
            best_params[f"{scorer}/r{r}"] = b[18]
    for ls in LABEL_SETS:  # FinBERT: every step-1 configuration with all baselines
        keep(*run_label_set(ls, cfg, folds=folds, df=data[("finbert", ls)]), "finbert")
        keep(  # CryptoBERT: LightGBM rung 3
            *run_label_set(
                ls, cfg, models=("lgbm",), rungs={3: RUNGS[3]}, folds=folds, df=data[("cryptobert", ls)],
                baselines=False,
            ),
            "cryptobert",
        )  # fmt: skip
    for scorer, rungs in (("finbert", RUNGS), ("cryptobert", {3: RUNGS[3]})):
        tr, th = run_label_set(
            "A", cfg, models=("lgbm",), rungs=rungs, folds=folds, df=data[(scorer, "A")],
            scenarios=("taker",),
            tuned_params={(r, 18): tuned[(scorer, r)] for r in rungs}, baselines=False,
        )  # fmt: skip
        keep(tr, th, scorer, "lgbm_tuned")
    sh, th = run_label_set(
        "A", cfg, models=("lgbm",), rungs={3: RUNGS[3]}, shuffle_labels=True, folds=folds,
        df=data[("finbert", "A")], baselines=False,
    )  # fmt: skip
    keep(sh, th, "finbert", "lgbm_shuffled")

    trades = pl.concat(parts, how="diagonal_relaxed").with_columns(
        fold=pl.when(pl.col("decision_time") >= to_ms(SPLIT)).then(19).otherwise(18)
    )
    pf, ov = [], []
    for scorer in SCORERS:
        f, o = summarize(trades.filter(pl.col("scorer") == scorer).drop("scorer"), cfg, report_folds)
        pf.append(f.with_columns(scorer=pl.lit(scorer)))
        ov.append(o.with_columns(scorer=pl.lit(scorer)))
    OUT.mkdir(parents=True, exist_ok=True)
    pl.concat(pf).write_csv(OUT / "fold_metrics.csv")
    pl.concat(ov).write_csv(OUT / "overall_metrics.csv")
    pl.concat(thr, how="diagonal_relaxed").write_csv(OUT / "thresholds.csv")
    pl.concat(trials, how="diagonal_relaxed").write_csv(OUT / "tuning.csv")
    (OUT / "best_params.json").write_text(
        json.dumps({"folds_ms": [[f.test_start, f.test_end] for f in report_folds], **best_params}, indent=2)
    )
    trades.write_parquet(TRADES)
    print(f"holdout done: {trades.height:,} trade rows")


if __name__ == "__main__":
    main()
