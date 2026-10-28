"""Phase 5 (SCOPE.md, "Phase 5 pre-registration"), step 1: labels only, before any model.

Usage: uv run python -m mbpe.validation.phase5 step1
Outputs: reports/phase5/{exit_reasons.csv, exit_reasons_by_year.csv, always_overall.csv, always_folds.csv}

- exit-reason shares (tp / sl / expiry) per side, over the evaluation window (2021-01-01 to the holdout)
  and the test period (2022-01-01 to the holdout), plus per year, with the per-trade dispersion of net R
- always-long / always-short over the Phase 3 test folds: one position at a time, no blackout (Phase 5 has
  none), both cost scenarios, pooled and per fold
Decision times and exits stay before the holdout (guard in walk_forward).
"""

import sys
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.labels.triple_barrier import load_config, scenario_net_r
from mbpe.validation.backtest import metrics, simulate
from mbpe.validation.walk_forward import guard_holdout, load_eval_config, make_folds, to_ms

LABEL_CONFIG = Path("config/labels_P5.yaml")
OUT = Path("reports/phase5")
SCENARIOS = ("taker", "maker_entry_tp")


def load_labels(cfg) -> pl.DataFrame:
    """Phase 5 labels in the evaluation window, decision and exit before the holdout; per-scenario net R."""
    lcfg = load_config(LABEL_CONFIG)
    lab = pl.read_parquet(lcfg.output)
    lab = lab.with_columns(
        net_R_taker=pl.col("net_R"),
        net_R_maker_entry_tp=scenario_net_r(lab, lcfg, "maker_entry_tp"),
        bps_per_R=(pl.col("entry_price") - pl.col("sl")).abs() / pl.col("entry_price") * 1e4,
    )
    h = cfg.holdout_start
    lab = lab.filter(
        (pl.col("decision_time") >= cfg.eval_start)
        & (pl.col("decision_time") < h)
        & (pl.col("exit_time") < h)
    )
    return guard_holdout(lab, cfg)


def exit_reasons(lab: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    return (
        lab.group_by(*by, "side")
        .agg(
            labels=pl.len(),
            tp=(pl.col("exit_reason") == "tp").mean(),
            sl=(pl.col("exit_reason") == "sl").mean(),
            expiry=(pl.col("exit_reason") == "expiry").mean(),
            label_1=pl.col("label").mean(),
            mean_hold_h=(pl.col("exit_time") - pl.col("entry_time")).dt.total_minutes().mean() / 60,
            mean_net_R_taker=pl.col("net_R_taker").mean(),
            sd_net_R_taker=pl.col("net_R_taker").std(),
            mean_gross_R=pl.col("gross_R").mean(),
        )
        .sort(*by, "side")
    )


def always_baselines(lab: pl.DataFrame, cfg) -> tuple[pl.DataFrame, pl.DataFrame]:
    wide = lab.pivot(
        on="side",
        index="decision_time",
        values=["exit_time", "gross_R", "net_R_taker", "net_R_maker_entry_tp", "bps_per_R"],
    ).drop_nulls().sort("decision_time")  # fmt: skip
    ms = lambda c: wide[c].dt.epoch("ms").to_numpy()  # noqa: E731
    decision, exit_ = ms("decision_time"), {s: ms(f"exit_time_{s}") for s in ("long", "short")}
    folds = make_folds(cfg)
    per_fold, pooled = [], {}
    for side in ("long", "short"):
        for scen in SCENARIOS:
            trades = []
            for f in folds:
                m = (decision >= f.test_start) & (decision < f.test_end)
                sig = np.ones(m.sum(), bool)
                off = np.zeros(m.sum(), bool)
                idx, _ = simulate(
                    decision[m],
                    sig if side == "long" else off,
                    sig if side == "short" else off,
                    exit_["long"][m],
                    exit_["short"][m],
                )
                rows = np.flatnonzero(m)[idx]
                t = pl.DataFrame(
                    {
                        "exit_time": exit_[side][rows],
                        "net_R": wide[f"net_R_{scen}_{side}"].to_numpy()[rows],
                        "gross_R": wide[f"gross_R_{side}"].to_numpy()[rows],
                        "bps_per_R": wide[f"bps_per_R_{side}"].to_numpy()[rows],
                    }
                )
                trades.append(t)
                per_fold.append(
                    {
                        "strategy": f"always_{side}",
                        "scenario": scen,
                        "fold": f.index,
                        **metrics(
                            t, f.test_start, f.test_end, cfg.risk_per_trade, cfg.bootstrap_block, 0, False
                        ),
                    }
                )
            pooled[(side, scen)] = metrics(
                pl.concat(trades),
                folds[0].test_start,
                to_ms(cfg.holdout_start),
                cfg.risk_per_trade,
                cfg.bootstrap_block,
                cfg.bootstrap_samples,
            )
    overall = pl.DataFrame(
        [{"strategy": f"always_{side}", "scenario": scen, **m} for (side, scen), m in pooled.items()]
    )
    return overall, pl.DataFrame(per_fold)


def step1() -> None:
    cfg = load_eval_config()
    lab = load_labels(cfg)
    test = lab.filter(pl.col("decision_time") >= cfg.first_test_fold)
    OUT.mkdir(parents=True, exist_ok=True)
    pl.concat(
        [
            exit_reasons(
                lab.with_columns(period=pl.lit("evaluation window 2021-01-01 → holdout")), ["period"]
            ),
            exit_reasons(test.with_columns(period=pl.lit("test folds 2022-01-01 → holdout")), ["period"]),
        ]
    ).write_csv(OUT / "exit_reasons.csv")
    exit_reasons(lab.with_columns(year=pl.col("decision_time").dt.year()), ["year"]).write_csv(
        OUT / "exit_reasons_by_year.csv"
    )
    overall, per_fold = always_baselines(lab, cfg)
    overall.write_csv(OUT / "always_overall.csv")
    per_fold.write_csv(OUT / "always_folds.csv")
    with pl.Config(tbl_cols=20, tbl_width_chars=220, float_precision=3):
        print(pl.read_csv(OUT / "exit_reasons.csv"))
        print(overall)


if __name__ == "__main__":
    {"step1": step1}[sys.argv[1]]()
