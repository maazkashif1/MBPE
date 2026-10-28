"""Capped LightGBM tuning (pre-registered in SCOPE.md, Phase 3 step 2).

Per rung and test fold: an Optuna TPE study of `n_trials` trials over the pre-registered space, fitted on the
training slice and scored on the validation slice only. Test-fold rows are never used.
Objective: validation mean net R under the threshold rule (trade-weighted mean over sides with
>= min_val_trades validation trades; -10 if none). One parameter set per fold and rung, both sides.

Usage: uv run python -m mbpe.validation.tuning --rung 1   (one process per rung)
Results: reports/phase3_step2/tuning_r<rung>.csv and best_params_r<rung>.json
"""

import json
import sys
from pathlib import Path

import optuna
import polars as pl

from mbpe.validation.harness import (
    SIDES,
    fit_predict,
    load_dataset,
    prepare_arrays,
    rung_features,
    threshold_stats,
)
from mbpe.validation.walk_forward import EvalConfig, load_eval_config, make_folds, split

OUT = Path("reports/phase3_step2")
N_TRIALS = 30
NO_TRADE_SCORE = -10.0


def space(trial: optuna.Trial) -> dict:
    return {
        "num_leaves": trial.suggest_int("num_leaves", 7, 63),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 100, 1000),
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
        "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 0.9),
        "lambda_l2": trial.suggest_float("lambda_l2", 0.0, 10.0),
    }


def tune(
    cfg: EvalConfig,
    rung: int,
    label_set: str = "A",
    scenario: str = "taker",
    n_trials: int = N_TRIALS,
    folds=None,
    df: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict[int, dict]]:
    """(all trials, best params per fold index) for one rung."""
    df = load_dataset(label_set, cfg) if df is None else df
    decision, arr, weights, eligible = prepare_arrays(df, cfg)
    names = rung_features(rung)
    X = df.select(names).cast(pl.Float64).to_numpy()
    rows, best = [], {}
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    for fold in folds or make_folds(cfg):
        masks = {s: split(decision, arr[s]["exit"], fold, cfg) for s in SIDES}

        def objective(trial: optuna.Trial, masks=masks) -> float:
            params = space(trial)
            stats = []
            for side in SIDES:
                m = masks[side]
                data = {
                    "X_train": X[m["train"]],
                    "y_train": arr[side]["y"][m["train"]],
                    "w_train": weights[side][m["train"]],
                    "X_val": X[m["val"]],
                    "y_val": arr[side]["y"][m["val"]],
                    "w_val": weights[side][m["val"]],
                    "X_test": X[:0],  # never the test fold
                }
                p_val, _, info = fit_predict("lgbm", names, data, cfg, params)
                v = m["val"]
                thr, mean, n = threshold_stats(
                    p_val,
                    decision[v],
                    arr[side]["exit"][v],
                    {scenario: arr[side]["net"][scenario][v]},
                    eligible[v],
                    cfg,
                )[scenario]
                trial.set_user_attr(f"{side}_val_trades", n)
                trial.set_user_attr(f"{side}_best_iteration", info["best_iteration"])
                if thr is not None:
                    stats.append((mean, n))
            if not stats:
                return NO_TRADE_SCORE
            return float(sum(m * n for m, n in stats) / sum(n for _, n in stats))

        study = optuna.create_study(
            direction="maximize", sampler=optuna.samplers.TPESampler(seed=1000 * rung + fold.index)
        )
        study.optimize(objective, n_trials=n_trials)
        best[fold.index] = study.best_params
        for t in study.trials:
            rows.append({"rung": rung, "fold": fold.index, "trial": t.number, "value": t.value, **t.params,
                         **t.user_attrs})  # fmt: skip
        print(f"rung {rung} fold {fold.index}: best validation mean R {study.best_value:+.4f}", flush=True)
    return pl.DataFrame(rows), best


def load_best(rungs=(1, 2, 3), root: Path = OUT) -> dict[tuple[int, int], dict]:
    out = {}
    for r in rungs:
        for fold, params in json.loads((root / f"best_params_r{r}.json").read_text()).items():
            out[(r, int(fold))] = params
    return out


def main() -> None:
    rung = int(sys.argv[sys.argv.index("--rung") + 1])
    cfg = load_eval_config()
    trials, best = tune(cfg, rung)
    OUT.mkdir(parents=True, exist_ok=True)
    trials.write_csv(OUT / f"tuning_r{rung}.csv")
    (OUT / f"best_params_r{rung}.json").write_text(json.dumps(best, indent=2))
    print(f"rung {rung}: {trials.height} trials")


if __name__ == "__main__":
    main()
