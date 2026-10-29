import json
import os
import time
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.research.experiment import (
    LABEL_SETS,
    RUNGS,
    TRACKING_URI,
    markdown_report,
    run_label_set,
    summarize,
)
from mbpe.research.tuning import N_TRIALS, OUT, load_best
from mbpe.research.walk_forward import load_eval_config

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def tuning_summary() -> tuple[pl.DataFrame, int]:
    trials = pl.concat([pl.read_csv(f) for f in sorted(OUT.glob("tuning_r*.csv"))], how="diagonal_relaxed")
    best = trials.sort("value", descending=True).group_by("rung", "fold").first().sort("rung", "fold")
    return best, trials.height


def main() -> None:
    cfg = load_eval_config()
    t0 = time.perf_counter()
    parts, thr = [], []
    for ls in LABEL_SETS:
        tr, th = run_label_set(ls, cfg)
        parts.append(tr)
        thr.append(th)
        print(f"label set {ls}: {time.perf_counter() - t0:.0f}s", flush=True)
    best = load_best()
    tr, th = run_label_set(
        "A", cfg, models=("lgbm",), scenarios=("taker",), tuned_params=best, baselines=False
    )
    parts.append(tr)
    thr.append(th.with_columns(model=pl.lit("lgbm_tuned")))
    sh, _ = run_label_set(
        "A", cfg, models=("lgbm",), rungs={3: RUNGS[3]}, shuffle_labels=True, baselines=False
    )
    parts.append(sh)
    trades = pl.concat(parts, how="diagonal_relaxed")
    per_fold, overall = summarize(trades, cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    per_fold.write_csv(OUT / "fold_metrics.csv")
    overall.write_csv(OUT / "overall_metrics.csv")
    pl.concat(thr, how="diagonal_relaxed").write_csv(OUT / "thresholds.csv")
    best_trials, n_trials = tuning_summary()
    best_trials.write_csv(OUT / "tuning_summary.csv")
    (OUT / "report.md").write_text(markdown_report(per_fold, overall))
    trades.write_parquet(Path("data/features/phase3_step2_trades.parquet"))

    import mlflow

    Path("mlruns").mkdir(exist_ok=True)
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment("mbpe-phase3-step2")
    for row in overall.filter(pl.col("seed") == -1).iter_rows(named=True):
        name = f"{row['label_set']}-{row['scenario']}-{row['strategy']}-r{row['rung']}"
        with mlflow.start_run(run_name=name):
            mlflow.log_params({k: row[k] for k in ("label_set", "scenario", "strategy", "rung", "model")})
            if row["strategy"] == "lgbm_tuned":
                mlflow.log_params({"trials_per_fold": N_TRIALS, "total_trials_all_rungs": n_trials})
                mlflow.log_text(
                    json.dumps({f"{k[0]}-{k[1]}": v for k, v in best.items()}, indent=1), "best_params.json"
                )
            mlflow.log_metrics(
                {
                    f"overall_{m}": float(row[m])
                    for m in (
                        "trades",
                        "mean_R",
                        "bps_per_trade",
                        "gross_bps_per_trade",
                        "hit_rate",
                        "sharpe",
                    )
                    if row[m] is not None and np.isfinite(row[m])
                }
            )
    print(f"done in {time.perf_counter() - t0:.0f}s; tuning trials recorded: {n_trials}")


if __name__ == "__main__":
    main()
