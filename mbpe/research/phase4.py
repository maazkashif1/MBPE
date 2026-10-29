from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.features.nfp_and_orderbook import PHASE4_PATH
from mbpe.research.experiment import ALL_RUNGS, LABEL_SETS, run_label_set, summarize
from mbpe.research.walk_forward import load_eval_config

OUT = Path("reports/phase4")
TRADES = Path("data/features/phase4_trades.parquet")
PHASE3_OVERALL = Path("reports/phase3_step2/overall_metrics.csv")
RUNS = {"phase3": (3, 41), "phase3_nfp_blackout": (3, 42), "orderbook": (3, 43)}
DECISIONS = {
    "4a MACD/EMA": (("phase3", 41), ("phase3", 3)),
    "4b NFP": (("phase3_nfp_blackout", 42), ("phase3", 3)),
    "4c order book": (("orderbook", 43), ("orderbook", 3)),
}
REPORTED = {"rung 3 + NFP blackout only (not a decision)": (("phase3_nfp_blackout", 3), ("phase3", 3))}
MIN_GAIN_BPS = 2.0


def configs() -> dict:
    base = load_eval_config().model_copy(update={"phase4_features": PHASE4_PATH})
    return {
        "phase3": base,
        "phase3_nfp_blackout": base.model_copy(update={"blackout_events": ("CPI", "FOMC", "NFP")}),
        "orderbook": base.model_copy(
            update={
                "eval_start": datetime(2023, 1, 1, tzinfo=UTC),
                "first_test_fold": datetime(2024, 1, 1, tzinfo=UTC),
            }
        ),
    }


def run() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    cfgs = configs()
    trades, thr, pf, ov = [], [], [], []
    for variant, rungs in RUNS.items():
        cfg, parts = cfgs[variant], []
        for ls in LABEL_SETS:
            tr, th = run_label_set(ls, cfg, rungs={r: ALL_RUNGS[r] for r in rungs})
            parts.append(tr)
            thr.append(th.with_columns(variant=pl.lit(variant)))
        for r in rungs:
            tr, th = run_label_set(
                "A", cfg, models=("lgbm",), rungs={r: ALL_RUNGS[r]}, shuffle_labels=True, baselines=False
            )
            parts.append(tr)
            thr.append(th.with_columns(variant=pl.lit(variant), model=pl.lit("lgbm_shuffled")))
        t = pl.concat(parts, how="diagonal_relaxed")
        f, o = summarize(t, cfg)
        trades.append(t.with_columns(variant=pl.lit(variant)))
        pf.append(f.with_columns(variant=pl.lit(variant)))
        ov.append(o.with_columns(variant=pl.lit(variant)))
        print(f"{variant}: {t.height:,} trade rows", flush=True)

    def cat(x):
        return pl.concat(x, how="diagonal_relaxed")

    return cat(trades), cat(thr), cat(pf), cat(ov)


def _one(df: pl.DataFrame, variant: str, rung: int, strategy: str) -> pl.DataFrame:
    return df.filter(
        (pl.col("variant") == variant)
        & (pl.col("rung") == rung)
        & (pl.col("strategy") == strategy)
        & (pl.col("label_set") == "A")
        & (pl.col("scenario") == "taker")
        & (pl.col("seed") == -1)
    )


def advantage(df: pl.DataFrame, variant: str, rung: int) -> pl.DataFrame:
    keys = ["fold"] if "fold" in df.columns else []
    m = _one(df, variant, rung, "lgbm").select(
        *keys, "gross_bps_per_trade", "trades", "mean_R", "ci_lo", "ci_hi"
    )
    s = _one(df, variant, rung, "lgbm_shuffled").select(*keys, shuffled="gross_bps_per_trade")
    out = m.join(s, on=keys, how="left") if keys else pl.concat([m, s], how="horizontal")
    return out.with_columns(adv=pl.col("gross_bps_per_trade") - pl.col("shuffled"))


def kill_table(pf: pl.DataFrame, ov: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for name, ((ve, re), (vb, rb)) in {**DECISIONS, **REPORTED}.items():
        e, b = advantage(ov, ve, re).row(0, named=True), advantage(ov, vb, rb).row(0, named=True)
        fe, fb = advantage(pf, ve, re), advantage(pf, vb, rb)
        j = fe.join(fb, on="fold", suffix="_base")
        improved = int(
            j.filter(pl.col("adv").is_not_null() & pl.col("adv_base").is_not_null())
            .select((pl.col("adv") > pl.col("adv_base")).sum())
            .item()
        )
        gain = e["adv"] - b["adv"] if e["adv"] is not None and b["adv"] is not None else np.nan
        decision = name in DECISIONS
        rows.append(
            {
                "extension": name,
                "decides": decision,
                "variant": ve,
                "rung": re,
                "base_variant": vb,
                "base_rung": rb,
                "trades": e["trades"],
                "gross_bps": e["gross_bps_per_trade"],
                "shuffled_gross_bps": e["shuffled"],
                "advantage_bps": e["adv"],
                "base_trades": b["trades"],
                "base_advantage_bps": b["adv"],
                "gain_bps": gain,
                "folds_improved": improved,
                "folds": fe.height,
                "mean_R": e["mean_R"],
                "ci_lo": e["ci_lo"],
                "ci_hi": e["ci_hi"],
                "pass_bps": bool(gain >= MIN_GAIN_BPS),
                "pass_net_R": bool(e["mean_R"] is not None and e["mean_R"] > 0),
                "pass": bool(
                    decision and gain >= MIN_GAIN_BPS and e["mean_R"] is not None and e["mean_R"] > 0
                ),
            }
        )
    return pl.DataFrame(rows)


def reproduction(ov: pl.DataFrame) -> pl.DataFrame:
    p3 = pl.read_csv(PHASE3_OVERALL).filter(
        (pl.col("rung").is_in([0, 3])) & ~pl.col("strategy").str.contains("tuned")
    )
    p4 = ov.filter((pl.col("variant") == "phase3") & pl.col("rung").is_in([0, 3])).drop("variant")
    keys = ["label_set", "scenario", "strategy", "rung", "model", "seed"]
    j = p3.join(p4, on=keys, suffix="_p4")
    return j.select(
        *keys,
        "trades",
        "trades_p4",
        diff_mean_R=(pl.col("mean_R") - pl.col("mean_R_p4")).abs(),
        diff_gross_bps=(pl.col("gross_bps_per_trade") - pl.col("gross_bps_per_trade_p4")).abs(),
    )


def main() -> None:
    trades, thr, pf, ov = run()
    OUT.mkdir(parents=True, exist_ok=True)
    pf.write_csv(OUT / "fold_metrics.csv")
    ov.write_csv(OUT / "overall_metrics.csv")
    thr.write_csv(OUT / "thresholds.csv")
    trades.write_parquet(TRADES)
    k = kill_table(pf, ov)
    k.write_csv(OUT / "kill.csv")
    rep = reproduction(ov)
    rep.write_csv(OUT / "reproduction.csv")
    with pl.Config(tbl_cols=30, tbl_width_chars=250):
        print(k)
        print(
            rep.select(
                pl.len(),
                pl.col("diff_mean_R").max(),
                pl.col("diff_gross_bps").max(),
                (pl.col("trades") != pl.col("trades_p4")).sum(),
            )
        )


if __name__ == "__main__":
    main()
