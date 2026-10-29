import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.labels.triple_barrier import load_config, scenario_net_r
from mbpe.research.backtest import metrics, simulate
from mbpe.research.experiment import RUNGS, load_dataset, run_label_set, summarize
from mbpe.research.walk_forward import guard_holdout, load_eval_config, make_folds, to_ms

LABEL_CONFIG = Path("config/labels_P5.yaml")
OUT = Path("reports/phase5")
SCENARIOS = ("taker", "maker_entry_tp")
TRADES = Path("data/features/phase5_trades.parquet")
KLINES = Path("data/clean/binance/futures_klines_1m.parquet")
N_PERMUTATIONS = 20
HEADLINE = {"strategy": "lgbm", "rung": 3, "scenario": "taker"}


def load_labels(cfg) -> pl.DataFrame:
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
    wide = (
        lab.pivot(
            on="side",
            index="decision_time",
            values=["exit_time", "gross_R", "net_R_taker", "net_R_maker_entry_tp", "bps_per_R"],
        )
        .drop_nulls()
        .sort("decision_time")
    )

    def ms(c):
        return wide[c].dt.epoch("ms").to_numpy()

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


def cfg_p5():
    return load_eval_config().model_copy(
        update={
            "embargo_hours": 48,
            "blackout_events": (),
            "entry_rule": "top_quantile",
            "entry_quantile": 0.75,
            "volfilter_column": "atr_1d_pct",
            "random_long_only": True,
        }
    )


def dataset(cfg) -> pl.DataFrame:
    df = load_dataset("P5", cfg)
    atr = pl.read_parquet(load_config(LABEL_CONFIG).output).filter(pl.col("side") == "long")
    close = pl.read_parquet(KLINES, columns=["open_time", "close"]).select(
        decision_time=pl.col("open_time") + timedelta(minutes=1), close_t="close"
    )
    return (
        df.join(atr.select("decision_time", "atr"), on="decision_time", how="left")
        .join(close, on="decision_time", how="left")
        .with_columns(atr_1d_pct=pl.col("atr") / pl.col("close_t"))
        .drop("atr", "close_t")
    )


def run_step2(cfg, df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    tr, th = run_label_set("P5", cfg, df=df)
    parts = [tr]
    for perm in range(N_PERMUTATIONS):
        for r in RUNGS:
            sh, _ = run_label_set(
                "P5",
                cfg,
                models=("lgbm",),
                rungs={r: RUNGS[r]},
                shuffle_labels=True,
                df=df,
                baselines=False,
                shuffle_seed=perm,
            )
            parts.append(sh.filter(pl.col("strategy") == "lgbm_shuffled").with_columns(seed=pl.lit(perm)))
        print(f"permutation {perm + 1}/{N_PERMUTATIONS} done", flush=True)
    return pl.concat(parts, how="diagonal_relaxed"), th


def long_share(trades: pl.DataFrame) -> pl.DataFrame:
    keys = ["scenario", "strategy", "rung", "model", "seed"]
    return trades.group_by(keys).agg(trades=pl.len(), long_share=(pl.col("side") == 1).mean()).sort(keys)


def fold_long_share(trades: pl.DataFrame, cfg) -> pl.DataFrame:
    h = trades.filter(
        (pl.col("strategy") == HEADLINE["strategy"])
        & (pl.col("rung") == HEADLINE["rung"])
        & (pl.col("scenario") == HEADLINE["scenario"])
        & (pl.col("seed") == -1)
    )
    folds = pl.DataFrame(
        {"fold": [f.index for f in make_folds(cfg)], "test_start": [f.test_start for f in make_folds(cfg)]}
    )
    agg = h.group_by("fold").agg(trades=pl.len(), long_share=(pl.col("side") == 1).mean())
    return folds.join(agg, on="fold", how="left").with_columns(pl.col("trades").fill_null(0)).sort("fold")


def verdict(ov: pl.DataFrame, ls: pl.DataFrame) -> dict:
    def pick(strategy, rung=HEADLINE["rung"], scenario="taker", seed=-1):
        return ov.filter(
            (pl.col("strategy") == strategy)
            & (pl.col("rung") == rung)
            & (pl.col("scenario") == scenario)
            & (pl.col("seed") == seed)
        )

    h = pick("lgbm").row(0, named=True)
    perms = ov.filter(
        (pl.col("strategy") == "lgbm_shuffled") & (pl.col("rung") == 3) & (pl.col("scenario") == "taker")
    )["mean_R"]
    always = pick("always_long", rung=0).row(0, named=True)
    rlong = ov.filter(
        (pl.col("strategy") == "randomlong_vs_lgbm") & (pl.col("rung") == 3) & (pl.col("scenario") == "taker")
    )["mean_R"]
    step1 = pl.read_csv(OUT / "always_overall.csv").filter(
        (pl.col("strategy") == "always_long") & (pl.col("scenario") == "taker")
    )["mean_R"][0]
    c1, c2, c3 = h["mean_R"] > 0, h["ci_lo"] > 0, bool((h["mean_R"] > perms).all())
    passed = bool(c1 and c2 and c3)
    beats_always = h["mean_R"] > always["mean_R"]
    hl = ls.filter(
        (pl.col("strategy") == "lgbm")
        & (pl.col("rung") == 3)
        & (pl.col("scenario") == "taker")
        & (pl.col("seed") == -1)
    )["long_share"][0]
    return {
        "headline": "rung 3 LightGBM, taker, pooled over all test trades",
        "trades": h["trades"],
        "mean_R": h["mean_R"],
        "ci_lo": h["ci_lo"],
        "ci_hi": h["ci_hi"],
        "c1_mean_R_gt_0": bool(c1),
        "c2_ci_lo_gt_0": bool(c2),
        "permutations": len(perms),
        "perm_mean_R_min": perms.min(),
        "perm_mean_R_median": perms.median(),
        "perm_mean_R_max": perms.max(),
        "perms_beaten": int((h["mean_R"] > perms).sum()),
        "c3_beats_all_permutations": c3,
        "pass": passed,
        "always_long_mean_R": always["mean_R"],
        "always_long_ci_lo": always["ci_lo"],
        "always_long_ci_hi": always["ci_hi"],
        "always_long_trades": always["trades"],
        "always_long_matches_step1": bool(abs(always["mean_R"] - step1) < 1e-12),
        "random_long_only_mean_R_median": rlong.median(),
        "random_long_only_mean_R_p05": rlong.quantile(0.05),
        "random_long_only_mean_R_p95": rlong.quantile(0.95),
        "headline_long_share": hl,
        "headline_beats_always_long": bool(beats_always),
        "interpretation": (
            "not applicable (criterion not passed)"
            if not passed
            else "predictive edge candidate (beats always-long)"
            if beats_always
            else "drift exposure, not predictive edge"
        ),
    }


def step2() -> None:
    cfg = cfg_p5()
    df = dataset(cfg)
    trades, th = run_step2(cfg, df)
    pf, ov = summarize(trades, cfg)
    ls = long_share(trades)
    OUT.mkdir(parents=True, exist_ok=True)
    pf.write_csv(OUT / "fold_metrics.csv")
    ov.write_csv(OUT / "overall_metrics.csv")
    th.write_csv(OUT / "thresholds.csv")
    ls.write_csv(OUT / "long_share.csv")
    fold_long_share(trades, cfg).write_csv(OUT / "fold_long_share.csv")
    trades.write_parquet(TRADES)
    v = verdict(ov, ls)
    (OUT / "verdict.json").write_text(json.dumps(v, indent=1))
    print(json.dumps(v, indent=1))


if __name__ == "__main__":
    {"step1": step1, "step2": step2}[sys.argv[1]]()
