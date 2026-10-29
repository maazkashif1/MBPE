"""Generated result tables for PROGRESS.md and reports/FINDINGS.md.

Every results table lives between markers:

    <!-- generated:NAME -->
    ...table...
    <!-- /generated:NAME -->

and is rebuilt from committed files under reports/ by the function registered as NAME, so it needs no data/.

Usage:
  uv run python -m mbpe.reports.progress           # rewrite every generated block in place
  uv run python -m mbpe.reports.progress --check   # exit 1 if any block differs from its source (also a test)
"""

import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

DOCS = (Path("PROGRESS.md"), Path("reports/FINDINGS.md"))
R = Path("reports")
BLOCKS: dict[str, Callable[[], str]] = {}
MARK = re.compile(r"<!-- generated:([\w.-]+) -->\n(.*?)<!-- /generated:\1 -->", re.S)


def block(name: str):
    def deco(fn):
        BLOCKS[name] = fn
        return fn

    return deco


def fmt(x, nd: int = 3, signed: bool = True) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "–"
    return f"{x:+.{nd}f}" if signed else f"{x:.{nd}f}"


def file_block(path: Path, transform: Callable[[str], str] = lambda s: s) -> str:
    return transform(path.read_text()).rstrip("\n") + "\n"


# ---------- Phase 3 step 1 / 2 / 2b: saved reports ----------

BLOCKS["phase3_step1_report"] = lambda: file_block(R / "phase3_step1/report.md")
BLOCKS["phase3_step2_report"] = lambda: file_block(R / "phase3_step2/report.md")
BLOCKS["phase3_step2b_cryptobert"] = lambda: file_block(R / "phase3_step2_cryptobert/report.md")
BLOCKS["phase3_step2b_agreement"] = lambda: file_block(
    R / "phase3_step2_cryptobert/llm_agreement.md",
    lambda s: re.sub(
        r"^Labels: .*",
        "Labels: LLM-judged (data/labeling/llm_labels.csv), 198 benchmark headlines.",
        s,
        count=1,
    ),
)


@block("phase3_step2_bps")
def phase3_step2_bps() -> str:
    """Gross / net bps per trade for every model configuration and its baselines (bps_summary.csv)."""
    d = pl.read_csv(R / "phase3_step2/bps_summary.csv")
    out = [
        "| label set | scenario | model | rung | trades | net bps | gross bps | random: gross bps (median) "
        "| vol filter: gross bps (median) | mean per-fold net-bps advantage vs random / vol filter "
        "| folds with positive advantage vs random / vol filter |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in d.iter_rows(named=True):
        out.append(
            f"| {r['set']} | {r['scen']} | {r['strat']} | {r['rung']} | {r['trades']:,} "
            f"| {fmt(r['net_bps'], 1)} "
            f"| {fmt(r['gross_bps'], 1)} | {fmt(r['random_gross'], 1)} | {fmt(r['volfilter_gross'], 1)} "
            f"| {fmt(r['adv_random_mean'], 1)} / {fmt(r['adv_volfilter_mean'], 1)} "
            f"| {r['adv_random_pos']} / {r['adv_volfilter_pos']} |"
        )
    return "\n".join(out) + "\n"


def _overall(path: Path) -> pl.DataFrame:
    return pl.read_csv(path).filter(pl.col("seed") == -1)


@block("phase3_step2_tuning")
def phase3_step2_tuning() -> str:
    """Untuned vs tuned LightGBM on the test folds (A, taker); validation scores of the tuning trials."""
    ov = _overall(R / "phase3_step2/overall_metrics.csv").filter(
        (pl.col("label_set") == "A") & (pl.col("scenario") == "taker")
    )
    trials = pl.concat(
        [pl.read_csv(R / f"phase3_step2/tuning_r{r}.csv") for r in (1, 2, 3)], how="diagonal_relaxed"
    )
    best = trials.sort("value", descending=True).group_by("rung", "fold").first()
    out = [
        "| rung | untuned LightGBM: test mean net R [95% CI] | tuned: test mean net R [95% CI] "
        "| tuned: trades "
        "| tuning trials | validation, best trial per fold (mean) | folds with best validation > 0 "
        "| validation, median trial |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rung in (1, 2, 3):
        u = ov.filter((pl.col("strategy") == "lgbm") & (pl.col("rung") == rung)).row(0, named=True)
        t = ov.filter((pl.col("strategy") == "lgbm_tuned") & (pl.col("rung") == rung)).row(0, named=True)
        b, tr = best.filter(pl.col("rung") == rung), trials.filter(pl.col("rung") == rung)
        out.append(
            f"| {rung} | {fmt(u['mean_R'])} [{fmt(u['ci_lo'])}, {fmt(u['ci_hi'])}] "
            f"| {fmt(t['mean_R'])} [{fmt(t['ci_lo'])}, {fmt(t['ci_hi'])}] | {t['trades']:,} | {tr.height:,} "
            f"| {fmt(b['value'].mean())} | {(b['value'] > 0).sum()}/{b.height} "
            f"| {fmt(tr['value'].median())} |"
        )
    return "\n".join(out) + "\n"


# ---------- Phase 3 step 3: overfitting ----------


@block("phase3_step3_dsr")
def phase3_step3_dsr() -> str:
    d = json.loads((R / "phase3_step3/dsr.json").read_text())
    rows = [
        ("best configuration (highest daily Sharpe in the test folds)", d["best_config"]),
        ("trading days in the test folds", f"{d['n_days']:,}"),
        ("daily Sharpe (not annualized) / annualized", f"{fmt(d['sr_daily'], 4)} / {fmt(d['sr_annual'], 2)}"),
        ("skewness / kurtosis of daily returns", f"{fmt(d['skew'], 2)} / {fmt(d['kurtosis'], 2, False)}"),
        (
            "trials counted (N)",
            f"{d['n_trials']:,} = {d['n_tuning_trials']:,} tuning trials + {d['n_configs']} untuned "
            "evaluated configurations",
        ),
        ("variance of daily Sharpe across evaluated configurations (V)", f"{d['sr_variance']:.3g}"),
        (
            "expected maximum daily Sharpe under no skill, SR* (annualized)",
            f"{fmt(d['sr_star_daily'], 4)} ({fmt(d['sr_star_annual'], 2)})",
        ),
        ("PSR vs 0: P(true Sharpe > 0)", f"{d['psr_zero']:.4f}"),
        ("**Deflated Sharpe ratio: P(true Sharpe > SR*)**", f"**{d['dsr']:.4f}**"),
    ]
    return "| quantity | value |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows) + "\n"


@block("phase3_step3_pbo")
def phase3_step3_pbo() -> str:
    d = json.loads((R / "phase3_step3/pbo.json").read_text())
    rows = [
        ("configurations (strategies)", f"{d['n_strategies']}"),
        (
            "days / sub-groups S / days per group",
            f"{d['n_days']:,} / {d['n_groups']} / {d['rows_per_group']}",
        ),
        ("CSCV splits C(S, S/2)", f"{d['n_splits']:,}"),
        ("**PBO: P(in-sample best ranks in the bottom half out of sample)**", f"**{d['pbo']:.3f}**"),
        ("median logit of the in-sample winner's out-of-sample rank", fmt(d["logit_median"], 3)),
        (
            "in-sample winner: mean daily Sharpe in sample / out of sample",
            f"{fmt(d['is_sr_mean'], 4)} / {fmt(d['oos_sr_mean'], 4)}",
        ),
        ("share of splits where the in-sample winner loses money out of sample", f"{d['p_oos_loss']:.3f}"),
        (
            "most frequent in-sample winner (share of splits)",
            f"{d['top_winner']} ({d['top_winner_share']:.2f})",
        ),
    ]
    out = "| quantity | value |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows) + "\n"
    out += (
        "\nWithin each label set x cost scenario (same costs, so the configurations are alternatives):\n\n"
        "| group | configurations | PBO | in-sample winner loses money out of sample (share of splits) |\n"
        "|---|---|---|---|\n"
    )
    for g in d["by_group"]:
        out += f"| {g['group']} | {g['n_strategies']} | {g['pbo']:.3f} | {g['p_oos_loss']:.3f} |\n"
    return out


# ---------- Phase 3 holdout ----------

HO = R / "phase3_holdout"
CONFIG_KEYS = ["label_set", "scenario", "strategy", "rung", "scorer"]


def _walk_forward_overall() -> pl.DataFrame:
    fin = pl.read_csv(R / "phase3_step2/overall_metrics.csv").with_columns(scorer=pl.lit("finbert"))
    cb = pl.read_csv(R / "phase3_step2_cryptobert/overall_metrics.csv").filter(
        pl.col("scorer") == "cryptobert"
    )
    return pl.concat([fin, cb.select(fin.columns)])


def _ci(r: dict | None) -> str:
    return "–" if r is None else f"{fmt(r['mean_R'])} [{fmt(r['ci_lo'])}, {fmt(r['ci_hi'])}]"


def _one(df: pl.DataFrame, **k) -> dict | None:
    x = df.filter(*[pl.col(c) == v for c, v in k.items()])
    return x.row(0, named=True) if x.height else None


def _bps(r: dict | None) -> str:
    return "–" if r is None else f"{fmt(r['bps_per_trade'], 1)} / {fmt(r['gross_bps_per_trade'], 1)}"


def _trades(r: dict | None) -> str:
    return "–" if r is None else f"{r['trades']:,}"


@block("holdout_configs")
def holdout_configs() -> str:
    """Every evaluated configuration: walk-forward (2022-01-01 → holdout) next to the holdout."""
    wf, ho = _walk_forward_overall(), pl.read_csv(HO / "overall_metrics.csv")
    pf = pl.read_csv(HO / "fold_metrics.csv")
    configs = (
        ho.filter((pl.col("seed") == -1) & pl.col("strategy").is_in(["logreg", "lgbm", "lgbm_tuned"]))
        .select(CONFIG_KEYS)
        .sort(CONFIG_KEYS)
    )
    out = [
        "| label set | scenario | model | rung | scorer | walk-forward: trades "
        "| walk-forward: mean net R [95% CI] "
        "| walk-forward: net / gross bps | holdout: trades | holdout: mean net R [95% CI] "
        "| holdout: net / gross bps | holdout fold 1 / fold 2: mean net R (trades) "
        "| holdout random: net R (median) | holdout vol filter: net R (median) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in configs.iter_rows(named=True):
        w, h = _one(wf, seed=-1, **c), _one(ho, seed=-1, **c)
        folds = []
        for f in (18, 19):
            x = _one(pf, seed=-1, fold=f, **c)
            folds.append("·" if not x or not x["trades"] else f"{fmt(x['mean_R'], 2)} ({x['trades']})")
        base = []
        for b in ("random", "volfilter"):
            k = {**c, "strategy": f"{b}_vs_{c['strategy']}"}
            base.append(fmt(ho.filter(*[pl.col(n) == v for n, v in k.items()])["mean_R"].median()))
        out.append(
            f"| {c['label_set']} | {c['scenario']} | {c['strategy']} | {c['rung']} | {c['scorer']} "
            f"| {_trades(w)} | {_ci(w)} | {_bps(w)} | {_trades(h)} | {_ci(h)} | {_bps(h)} "
            f"| {' / '.join(folds)} | {base[0]} | {base[1]} |"
        )
    return "\n".join(out) + "\n"


@block("holdout_baselines")
def holdout_baselines() -> str:
    """Always-trade baselines and the shuffled-label control, walk-forward next to holdout."""
    wf, ho = _walk_forward_overall(), pl.read_csv(HO / "overall_metrics.csv")
    rows = (
        ho.filter(
            (pl.col("seed") == -1)
            & pl.col("strategy").is_in(["always_long", "always_short", "lgbm_shuffled"])
        )
        .select(CONFIG_KEYS)
        .sort(CONFIG_KEYS)
    )
    out = [
        "| label set | scenario | baseline | rung | walk-forward: trades "
        "| walk-forward: mean net R [95% CI] "
        "| walk-forward: net / gross bps | holdout: trades | holdout: mean net R [95% CI] "
        "| holdout: net / gross bps |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in rows.iter_rows(named=True):
        w, h = _one(wf, seed=-1, **c), _one(ho, seed=-1, **c)
        out.append(
            f"| {c['label_set']} | {c['scenario']} | {c['strategy']} | {c['rung'] or '–'} "
            f"| {_trades(w)} | {_ci(w)} | {_bps(w)} | {_trades(h)} | {_ci(h)} | {_bps(h)} |"
        )
    return "\n".join(out) + "\n"


def holdout_outcome(r: dict) -> str:
    """The pre-registered interpretation (SCOPE.md, "Phase 3 holdout test")."""
    if not r["mean_R"] > 0:
        return "mean net R ≤ 0: **the negative finding is confirmed**"
    if r["ci_lo"] > 0:
        return (
            "mean net R > 0 with a 95% CI excluding zero: **unexpected, reported but not acted on** "
            "(at most it justifies a forward test on new live data)"
        )
    return "mean net R > 0 with a 95% CI including zero: **inconclusive, consistent with no edge**"


@block("holdout_headline")
def holdout_headline() -> str:
    ho, wf = pl.read_csv(HO / "overall_metrics.csv"), _walk_forward_overall()
    k = {
        "label_set": "A",
        "scenario": "taker",
        "strategy": "lgbm_tuned",
        "rung": 3,
        "scorer": "finbert",
        "seed": -1,
    }
    h, w = _one(ho, **k), _one(wf, **k)
    beat = ho.filter(
        (pl.col("seed") == -1)
        & pl.col("strategy").is_in(["logreg", "lgbm", "lgbm_tuned"])
        & (pl.col("mean_R") > 0)
    )
    beat_ci = beat.filter(pl.col("ci_lo") > 0)
    rows = [
        ("headline configuration", "label set A, taker costs, tuned LightGBM, rung 3 (FinBERT)"),
        (
            "walk-forward (2022-01-01 → 2026-04-26): mean net R [95% CI] (trades)",
            f"{_ci(w)} ({w['trades']:,})",
        ),
        ("**holdout: mean net R [95% CI] (trades)**", f"**{_ci(h)}** ({h['trades']:,})"),
        ("holdout: net / gross bps per trade", _bps(h)),
        ("**pre-registered interpretation**", holdout_outcome(h)),
        ("configurations with holdout mean net R > 0 (of 32)", f"{beat.height}"),
        ("of those, with a 95% CI excluding zero", f"{beat_ci.height}"),
    ]
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"


# ---------- Phase 4 ----------

P4 = R / "phase4"
RUNG_NAME = {0: "–", 3: "3", 41: "4a", 42: "4b", 43: "4c"}
VARIANT_NAME = {
    "phase3": "Phase 3 window",
    "phase3_nfp_blackout": "Phase 3 window, + NFP blackout",
    "orderbook": "4c window (2023+)",
}


@block("phase4_book_depth_scan")
def phase4_book_depth_scan() -> str:
    d = json.loads((P4 / "book_depth_scan.json").read_text())
    late = sorted({v for v in d["band_first_seen"].values()} - {min(d["band_first_seen"].values())})
    rows = [
        ("days in range (first → last file)", f"{d['days']:,} ({d['first_day']} → {d['last_day']})"),
        ("raw files (SHA-256 checked)", f"{d['raw_files']:,}"),
        ("days without a file", ", ".join(d["missing_days"]) or "none"),
        (
            "files under 2 KB (counted as missing days)",
            ", ".join(f"{k} ({v:,} B)" for k, v in d["small_files"].items()) or "none",
        ),
        ("days without any snapshot in the clean layer", f"{len(d['days_without_snapshots'])}"),
        ("snapshots / median spacing", f"{d['snapshots']:,} / {d['median_step_s']:.0f} s"),
        (
            "steps over 45 s / over 2 min (staleness limit)",
            f"{d['steps_over_45s']:,} / {d['gaps_over_2min']:,}",
        ),
        ("time inside gaps over 2 min", f"{d['gap_hours_over_2min']:,.0f} h"),
        (
            "bands present from the first snapshot",
            ", ".join(k for k, v in d["band_first_seen"].items() if v == min(d["band_first_seen"].values())),
        ),
        (
            "bands that appear later",
            "; ".join(
                f"{', '.join(k for k, v in d['band_first_seen'].items() if v == t)} from {t}" for t in late
            )
            or "none",
        ),
        (
            "decision hours 2023-01-01 → holdout with a snapshot ≤ 2 min old",
            f"{d['hours_with_fresh_snapshot']:,} of {d['hours_2023_to_holdout']:,} "
            f"({d['hours_with_fresh_snapshot'] / d['hours_2023_to_holdout']:.1%})",
        ),
    ]
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"


@block("phase4_feature_missing")
def phase4_feature_missing() -> str:
    d = pl.read_csv(P4 / "feature_missing.csv")
    out = ["| feature | group | window from | hours | missing |", "|---|---|---|---|---|"]
    for r in d.iter_rows(named=True):
        out.append(
            f"| {r['feature']} | {r['group']} | {r['window_start']} | {r['hours']:,} "
            f"| {r['missing_share']:.1%} |"
        )
    return "\n".join(out) + "\n"


@block("phase4_kill")
def phase4_kill() -> str:
    d = pl.read_csv(P4 / "kill.csv")
    out = [
        "| extension | vs | trades | gross bps | shuffled control gross bps | advantage | rung 3 advantage "
        "| **gain (≥ +2.0 to pass)** | folds improved | **mean net R [95% CI]** (> 0 to pass) | passes |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in d.iter_rows(named=True):
        verdict = ("**yes**" if r["pass"] else "no") if r["decides"] else "– (reported only)"
        out.append(
            f"| {r['extension']} | rung 3, {VARIANT_NAME[r['base_variant']]} | {r['trades']:,} "
            f"| {fmt(r['gross_bps'], 1)} | {fmt(r['shuffled_gross_bps'], 1)} | {fmt(r['advantage_bps'], 1)} "
            f"| {fmt(r['base_advantage_bps'], 1)} | **{fmt(r['gain_bps'], 1)}** "
            f"| {r['folds_improved']}/{r['folds']} "
            f"| **{fmt(r['mean_R'])}** [{fmt(r['ci_lo'])}, {fmt(r['ci_hi'])}] | {verdict} |"
        )
    return "\n".join(out) + "\n"


@block("phase4_reproduction")
def phase4_reproduction() -> str:
    d = pl.read_csv(P4 / "reproduction.csv")
    rows = [
        ("configurations compared (rung 3 models, random / vol-filter seeds, always-trade, shuffled control)",
         f"{d.height:,}"),
        ("with a different trade count", f"{(d['trades'] != d['trades_p4']).sum()}"),
        ("max |difference| in mean net R", f"{d['diff_mean_R'].max():.2g}"),
        ("max |difference| in gross bps per trade", f"{d['diff_gross_bps'].max():.2g}"),
    ]  # fmt: skip
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"


@block("phase4_window_facts")
def phase4_window_facts() -> str:
    """Windows, the late ±0.2% band vs the training slices, and the shuffled controls' spread."""
    from mbpe.validation.phase4 import configs
    from mbpe.validation.walk_forward import make_folds, to_ms

    def day(ms: int) -> str:
        return str(datetime.fromtimestamp(ms / 1000, UTC).date())

    cfgs, rows = configs(), []
    for v in ("phase3", "orderbook"):
        c, f = cfgs[v], make_folds(cfgs[v])
        rows.append(
            (f"{VARIANT_NAME[v]}: training from / test folds", f"{c.eval_start.date()} / {len(f)} folds, "
             f"{day(f[0].test_start)} → {day(f[-1].test_end)}")
        )  # fmt: skip
    c = cfgs["orderbook"]
    train_end = max(
        int(to_ms(c.eval_start) + (1 - c.val_fraction) * (f.test_start - to_ms(c.eval_start)))
        for f in make_folds(c)
    )
    band = json.loads((P4 / "book_depth_scan.json").read_text())["band_first_seen"]["bid_depth_20bp"]
    band_day = band[:10]
    rows += [
        ("±0.2% band (book_imbalance_20bp) first present", band_day),
        ("latest end of any 4c training slice (validation starts there)", day(train_end)),
        ("±0.2% band present in any training slice", "no" if band_day > day(train_end) else "**yes**"),
    ]
    ov = pl.read_csv(P4 / "overall_metrics.csv").filter(
        (pl.col("variant") != "orderbook") & (pl.col("strategy") == "lgbm_shuffled")
        & (pl.col("label_set") == "A") & (pl.col("scenario") == "taker")
    )  # fmt: skip
    g = ov["gross_bps_per_trade"]
    rows.append(
        (
            f"shuffled-control gross bps, {ov.height} Phase 3-window feature sets (A, taker): min / max",
            f"{fmt(g.min(), 1)} / {fmt(g.max(), 1)} (spread {g.max() - g.min():.1f} bps; threshold 2.0)",
        )
    )
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"


@block("phase4_configs")
def phase4_configs() -> str:
    ov = pl.read_csv(P4 / "overall_metrics.csv")
    pf = pl.read_csv(P4 / "fold_metrics.csv")
    m = ov.filter((pl.col("seed") == -1) & pl.col("strategy").is_in(["logreg", "lgbm", "lgbm_shuffled"]))
    out = [
        "| window | label set | scenario | model | rung | trades | mean net R [95% CI] | net / gross bps "
        "| random: net R (median) | vol filter: net R (median) | folds won vs random / vol filter |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in m.sort("variant", "label_set", "scenario", "strategy", "rung").iter_rows(named=True):
        key = {k: r[k] for k in ("variant", "label_set", "scenario", "rung")}
        base, won = [], []
        for b in ("random", "volfilter"):
            sel = [pl.col(k) == v for k, v in key.items()] + [pl.col("strategy") == f"{b}_vs_{r['strategy']}"]
            base.append(fmt(ov.filter(*sel)["mean_R"].median()))
            bf = pf.filter(*sel).group_by("fold").agg(med=pl.col("mean_R").median())
            mf = pf.filter(*[pl.col(k) == v for k, v in key.items()], pl.col("strategy") == r["strategy"],
                           pl.col("seed") == -1, pl.col("trades") > 0)  # fmt: skip
            j = mf.join(bf, on="fold")
            n_folds = pf.filter(pl.col("variant") == r["variant"])["fold"].n_unique()
            won.append(f"{(j['mean_R'] > j['med']).sum()}/{n_folds}" if bf.height else "–")
        out.append(
            f"| {VARIANT_NAME[r['variant']]} | {r['label_set']} | {r['scenario']} | {r['strategy']} "
            f"| {RUNG_NAME[r['rung']]} | {r['trades']:,} | {_ci(r)} | {_bps(r)} | {base[0]} | {base[1]} "
            f"| {' / '.join(won)} |"
        )
    return "\n".join(out) + "\n"


@block("phase4_baselines")
def phase4_baselines() -> str:
    ov = pl.read_csv(P4 / "overall_metrics.csv").filter(pl.col("strategy").str.starts_with("always"))
    out = [
        "| window | label set | scenario | baseline | trades | mean net R [95% CI] | net / gross bps |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in ov.sort("variant", "label_set", "scenario", "strategy").iter_rows(named=True):
        out.append(
            f"| {VARIANT_NAME[r['variant']]} | {r['label_set']} | {r['scenario']} | {r['strategy']} "
            f"| {r['trades']:,} | {_ci(r)} | {_bps(r)} |"
        )
    return "\n".join(out) + "\n"


# ---------- Phase 5 ----------

P5 = R / "phase5"


def _exit_rows(d: pl.DataFrame, key: str) -> list[str]:
    return [
        f"| {r[key]} | {r['side']} | {r['labels']:,} | {r['tp']:.1%} | {r['sl']:.1%} | {r['expiry']:.1%} "
        f"| {r['label_1']:.1%} | {r['mean_hold_h']:.1f} | {fmt(r['mean_gross_R'])} "
        f"| {fmt(r['mean_net_R_taker'])} | {r['sd_net_R_taker']:.2f} |"
        for r in d.iter_rows(named=True)
    ]


EXIT_HEAD = (
    "| tp | sl | expiry | label = 1 (net R > 0, taker) | mean hold (h) | mean gross R | mean net R (taker) "
    "| sd of net R per label (taker) |"
)


@block("phase5_exit_reasons")
def phase5_exit_reasons() -> str:
    d = pl.read_csv(P5 / "exit_reasons.csv")
    out = ["| period | side | labels " + EXIT_HEAD, "|---|---|---|---|---|---|---|---|---|---|---|"]
    return "\n".join(out + _exit_rows(d, "period")) + "\n"


@block("phase5_exit_reasons_by_year")
def phase5_exit_reasons_by_year() -> str:
    d = pl.read_csv(P5 / "exit_reasons_by_year.csv")
    out = ["| year | side | labels " + EXIT_HEAD, "|---|---|---|---|---|---|---|---|---|---|---|"]
    return "\n".join(out + _exit_rows(d, "year")) + "\n"


@block("phase5_always")
def phase5_always() -> str:
    ov, pf = pl.read_csv(P5 / "always_overall.csv"), pl.read_csv(P5 / "always_folds.csv")
    out = [
        "| baseline | scenario | trades | mean net R [95% CI] | net / gross bps | hit rate | profit factor "
        "| folds with mean net R > 0 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in ov.iter_rows(named=True):
        f = pf.filter((pl.col("strategy") == r["strategy"]) & (pl.col("scenario") == r["scenario"]))
        out.append(
            f"| {r['strategy']} | {r['scenario']} | {r['trades']:,} | {_ci(r)} | {_bps(r)} "
            f"| {r['hit_rate']:.1%} | {r['profit_factor']:.2f} | {(f['mean_R'] > 0).sum()}/{f.height} |"
        )
    return "\n".join(out) + "\n"


def _p5_verdict() -> dict:
    return json.loads((P5 / "verdict.json").read_text())


def _yes(b: bool) -> str:
    return "**yes**" if b else "no"


@block("phase5_verdict")
def phase5_verdict() -> str:
    v = _p5_verdict()
    rows = [
        ("headline", f"{v['headline']}: {v['trades']:,} trades"),
        ("1. mean net R > 0", f"{fmt(v['mean_R'])}: {_yes(v['c1_mean_R_gt_0'])}"),
        (
            "2. 95% CI lower bound > 0",
            f"[{fmt(v['ci_lo'])}, {fmt(v['ci_hi'])}]: {_yes(v['c2_ci_lo_gt_0'])}",
        ),
        (
            f"3. beats all {v['permutations']} shuffled-label runs (p < 0.05)",
            f"beats {v['perms_beaten']} of {v['permutations']} "
            f"(their mean net R: min {fmt(v['perm_mean_R_min'])}, "
            f"median {fmt(v['perm_mean_R_median'])}, max {fmt(v['perm_mean_R_max'])}): "
            f"{_yes(v['c3_beats_all_permutations'])}",
        ),
        ("**historical pass criterion**", "**PASS**" if v["pass"] else "**FAIL**"),
    ]
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"


@block("phase5_interpretation")
def phase5_interpretation() -> str:
    v = _p5_verdict()
    rows = [
        (
            "headline mean net R (taker) [95% CI]",
            f"{fmt(v['mean_R'])} [{fmt(v['ci_lo'])}, {fmt(v['ci_hi'])}]",
        ),
        (
            "always-long mean net R (taker) [95% CI]",
            f"{fmt(v['always_long_mean_R'])} [{fmt(v['always_long_ci_lo'])}, {fmt(v['always_long_ci_hi'])}] "
            f"({v['always_long_trades']:,} trades; "
            f"identical to step 1: {_yes(v['always_long_matches_step1'])})",
        ),
        (
            "random long-only at the headline's frequency, 20 seeds: median [5%, 95%]",
            f"{fmt(v['random_long_only_mean_R_median'])} [{fmt(v['random_long_only_mean_R_p05'])}, "
            f"{fmt(v['random_long_only_mean_R_p95'])}]",
        ),
        ("share of the headline's trades that are long", f"{v['headline_long_share']:.1%}"),
        ("headline mean net R exceeds always-long's", _yes(v["headline_beats_always_long"])),
        ("**interpretation rule outcome**", f"**{v['interpretation']}**"),
    ]
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"


def _p5_long_share(ls: pl.DataFrame, **k) -> str:
    x = ls.filter(*[pl.col(c) == v for c, v in k.items()])
    return f"{x['long_share'][0]:.0%}" if x.height else "–"


@block("phase5_configs")
def phase5_configs() -> str:
    ov, pf = pl.read_csv(P5 / "overall_metrics.csv"), pl.read_csv(P5 / "fold_metrics.csv")
    ls = pl.read_csv(P5 / "long_share.csv")
    m = ov.filter((pl.col("seed") == -1) & pl.col("strategy").is_in(["logreg", "lgbm"]))
    out = [
        "| scenario | model | rung | trades | long share | mean net R [95% CI] | net / gross bps "
        "| random: net R (median) | vol filter: net R (median) | random long-only: net R (median) "
        "| folds won vs random / vol filter / random long-only |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in m.sort("scenario", "strategy", "rung", descending=[True, False, False]).iter_rows(named=True):
        key = {"scenario": r["scenario"], "rung": r["rung"]}
        base, won = [], []
        mf = pf.filter(
            *[pl.col(c) == v for c, v in key.items()],
            pl.col("strategy") == r["strategy"],
            pl.col("seed") == -1,
            pl.col("trades") > 0,
        )
        for b in ("random", "volfilter", "randomlong"):
            sel = [pl.col(c) == v for c, v in key.items()] + [pl.col("strategy") == f"{b}_vs_{r['strategy']}"]
            base.append(fmt(ov.filter(*sel)["mean_R"].median()))
            bf = pf.filter(*sel).group_by("fold").agg(med=pl.col("mean_R").median())
            j = mf.join(bf, on="fold")
            won.append(f"{(j['mean_R'] > j['med']).sum()}/{pf['fold'].n_unique()}")
        lshare = _p5_long_share(ls, scenario=r["scenario"], strategy=r["strategy"], rung=r["rung"], seed=-1)
        out.append(
            f"| {r['scenario']} | {r['strategy']} | {r['rung']} | {r['trades']:,} | {lshare} | {_ci(r)} "
            f"| {_bps(r)} | {base[0]} | {base[1]} | {base[2]} | {' / '.join(won)} |"
        )
    return "\n".join(out) + "\n"


@block("phase5_controls")
def phase5_controls() -> str:
    ov = pl.read_csv(P5 / "overall_metrics.csv")
    out = [
        "| scenario | rung | LightGBM: mean net R | shuffled-label runs: min / median / max mean net R "
        "| runs beaten | shuffled: median gross bps |",
        "|---|---|---|---|---|---|",
    ]
    for scen in ("taker", "maker_entry_tp"):
        for rung in (1, 2, 3):
            k = (pl.col("scenario") == scen) & (pl.col("rung") == rung)
            mdl = ov.filter(k & (pl.col("strategy") == "lgbm") & (pl.col("seed") == -1))["mean_R"][0]
            sh = ov.filter(k & (pl.col("strategy") == "lgbm_shuffled"))
            out.append(
                f"| {scen} | {rung} | {fmt(mdl)} "
                f"| {fmt(sh['mean_R'].min())} / {fmt(sh['mean_R'].median())} / "
                f"{fmt(sh['mean_R'].max())} | {(mdl > sh['mean_R']).sum()}/{sh.height} "
                f"| {fmt(sh['gross_bps_per_trade'].median(), 1)} |"
            )
    al = ov.filter(pl.col("strategy").str.starts_with("always"))
    out += [
        "",
        "| baseline | scenario | trades | mean net R [95% CI] | net / gross bps |",
        "|---|---|---|---|---|",
    ]
    for r in al.sort("strategy", "scenario", descending=[False, True]).iter_rows(named=True):
        out.append(f"| {r['strategy']} | {r['scenario']} | {r['trades']:,} | {_ci(r)} | {_bps(r)} |")
    return "\n".join(out) + "\n"


@block("phase5_folds")
def phase5_folds() -> str:
    """Per fold, taker: the headline (rung 3 LightGBM) next to its controls."""
    pf = pl.read_csv(P5 / "fold_metrics.csv").filter(pl.col("scenario") == "taker")
    trades = pl.read_csv(P5 / "fold_long_share.csv")

    def cell(df):
        return (
            "·" if not df.height or not df["trades"][0] else f"{fmt(df['mean_R'][0], 2)} ({df['trades'][0]})"
        )

    def med(df):
        return fmt(df["mean_R"].median(), 2) if df.height else "–"

    out = [
        "| fold | start | rung 3 LightGBM: mean net R (trades) | long share | always-long "
        "| shuffled (median of 20) "
        "| random (median) | vol filter (median) | random long-only (median) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for f in sorted(pf["fold"].unique().to_list()):
        x = pf.filter(pl.col("fold") == f)
        h = x.filter((pl.col("strategy") == "lgbm") & (pl.col("rung") == 3) & (pl.col("seed") == -1))
        al = x.filter(pl.col("strategy") == "always_long")
        r3 = x.filter(pl.col("rung") == 3)
        lsh = trades.filter(pl.col("fold") == f)["long_share"]
        start = datetime.fromtimestamp(trades.filter(pl.col("fold") == f)["test_start"][0] / 1000, UTC).date()
        out.append(
            f"| {f} | {start} | {cell(h)} | {f'{lsh[0]:.0%}' if lsh.len() and lsh[0] == lsh[0] else '·'} "
            f"| {cell(al)} | {med(r3.filter(pl.col('strategy') == 'lgbm_shuffled'))} "
            f"| {med(r3.filter(pl.col('strategy') == 'random_vs_lgbm'))} "
            f"| {med(r3.filter(pl.col('strategy') == 'volfilter_vs_lgbm'))} "
            f"| {med(r3.filter(pl.col('strategy') == 'randomlong_vs_lgbm'))} |"
        )
    return "\n".join(out) + "\n"


@block("phase5_entry_shares")
def phase5_entry_shares() -> str:
    """Realized shares selected by the top-25% entry rule (ties from isotonic calibration can raise them)."""
    th = pl.read_csv(P5 / "thresholds.csv").filter(pl.col("scenario") == "taker")
    out = [
        "| model | rung | side | validation share selected: median [min, max] | test share selected: median "
        "| fold-sides selecting ≥ 99% of validation (constant scores) |",
        "|---|---|---|---|---|---|",
    ]
    for (model, rung, side), g in th.sort("model", "rung", "side").group_by(
        "model", "rung", "side", maintain_order=True
    ):
        v, t = g["val_share"], g["test_share"]
        out.append(
            f"| {model} | {rung} | {side} | {v.median():.0%} [{v.min():.0%}, {v.max():.0%}] "
            f"| {t.median():.0%} "
            f"| {(v >= 0.99).sum()}/{g.height} |"
        )
    return "\n".join(out) + "\n"


# ---------- Phase 6 ----------

P6 = R / "phase6"


@block("phase6_a_scan")
def phase6_a_scan() -> str:
    d = pl.read_csv(P6 / "data_scan.csv")
    out = [
        "| dataset | raw files (SHA-256 checked) | 1m rows | first → last | gaps | missing rows "
        "| longest gap |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in d.iter_rows(named=True):
        out.append(
            f"| {r['dataset']} | {r['raw_files']:,} | {r['rows']:,} | {r['first'][:10]} → {r['last'][:10]} "
            f"| {r['gaps']} | {r['missing_rows']:,} "
            f"| {r['longest_gap_min']:,} min from {r['longest_gap_start'][:16]} |"
        )
    return "\n".join(out) + "\n"


@block("phase6_a_missing")
def phase6_a_missing() -> str:
    d = pl.read_csv(P6 / "feature_missing.csv")
    out = ["| feature | group | hours (2021-01-01 → holdout) | missing |", "|---|---|---|---|"]
    for r in d.iter_rows(named=True):
        out.append(f"| {r['feature']} | {r['group']} | {r['hours']:,} | {r['missing_share']:.2%} |")
    return "\n".join(out) + "\n"


@block("phase6_a_identity")
def phase6_a_identity() -> str:
    d = pl.read_csv(P6 / "rebuild_identity.csv")
    out = [
        "| feature file | rows before / after | columns before | added | removed "
        "| earlier columns byte-identical |",
        "|---|---|---|---|---|---|",
    ]
    for r in d.iter_rows(named=True):
        out.append(
            f"| {r['file']} | {r['rows_before']:,} / {r['rows_after']:,} | {r['columns_before']} "
            f"| {r['columns_added']} | {r['columns_removed']} | {_yes(r['previous_columns_identical'])} |"
        )
    return "\n".join(out) + "\n"


P6E = P6 / "eval"
P6_NAME = {
    "primary": "primary only",
    "primary_meta060": "primary + meta (P ≥ 0.60)",
    "primary_metaval": "primary + meta (validation threshold)",
}


def _p6_ov() -> pl.DataFrame:
    return pl.read_csv(P6E / "overall_metrics.csv")


def _p6_row(ov: pl.DataFrame, **k) -> dict | None:
    x = ov.filter(*[pl.col(c) == v for c, v in k.items()])
    return x.row(0, named=True) if x.height else None


def _p6_meta_period(ov: pl.DataFrame) -> str:
    return sorted(p for p in ov["period"].unique().to_list() if not p.startswith("folds 0-"))[0]


@block("phase6_c_headline")
def phase6_c_headline() -> str:
    ov = _p6_ov()
    per = _p6_meta_period(ov)
    k = {"period": per, "scenario": "taker"}
    h = _p6_row(ov, **k, model="lgbm", strategy="primary_meta060", seed=-1)
    p = _p6_row(ov, **k, model="lgbm", strategy="primary", seed=-1)
    al = _p6_row(ov, **k, strategy="always_long", seed=-1)
    sh = ov.filter(
        (pl.col("period") == per)
        & (pl.col("scenario") == "taker")
        & (pl.col("strategy") == "shuffled_primary_meta060")
    )["mean_R"].drop_nulls()
    rnd = ov.filter((pl.col("period") == per) & (pl.col("scenario") == "taker") & (pl.col("model") == "lgbm")
                    & (pl.col("strategy") == "random_vs_primary_meta060"))["mean_R"]  # fmt: skip
    vol = ov.filter((pl.col("period") == per) & (pl.col("scenario") == "taker") & (pl.col("model") == "lgbm")
                    & (pl.col("strategy") == "volfilter_vs_primary_meta060"))["mean_R"]  # fmt: skip
    rows = [
        ("headline", f"LightGBM three-class primary + meta-filter (P ≥ 0.60), label set A, taker, {per}"),
        ("**trades / mean net R [95% CI]**", f"{h['trades']:,} / **{_ci(h)}**" if h else "no trades"),
        ("net / gross bps per trade", _bps(h)),
        ("primary only, same folds: trades / mean net R [95% CI]", f"{_trades(p)} / {_ci(p)}"),
        (
            "shuffled-label runs (meta-filtered): runs with trades / min / median / max mean net R",
            f"{sh.len()} / {fmt(sh.min())} / {fmt(sh.median())} / {fmt(sh.max())}"
            if sh.len()
            else "no trades",
        ),
        (
            "headline beats how many shuffled runs",
            f"{(h['mean_R'] > sh).sum()} of {sh.len()}" if h and sh.len() else "–",
        ),
        (
            "random entry / volatility filter at its frequency: median mean net R",
            f"{fmt(rnd.median())} / {fmt(vol.median())}",
        ),
        ("always-long, same folds: mean net R [95% CI]", _ci(al)),
    ]
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows) + "\n"


@block("phase6_c_configs")
def phase6_c_configs() -> str:
    ov = _p6_ov()
    m = ov.filter((pl.col("seed") == -1) & pl.col("model").is_in(["lgbm", "catboost"]))
    out = [
        "| period | model | strategy | scenario | trades | mean net R [95% CI] | net / gross bps "
        "| random: net R (median) | vol filter: net R (median) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in m.sort(
        "period", "model", "strategy", "scenario", descending=[False, True, False, True]
    ).iter_rows(named=True):
        if r["trades"] == 0 and r["strategy"] != "primary" and r["period"].startswith("folds 0-"):
            continue  # meta strategies only exist from the meta folds; shown in that period
        b = {}
        for base in ("random", "volfilter"):
            sel = {k: r[k] for k in ("period", "model", "scenario")} | {
                "strategy": f"{base}_vs_{r['strategy']}"
            }
            b[base] = ov.filter(*[pl.col(k) == v for k, v in sel.items()])["mean_R"].median()
        out.append(
            f"| {r['period']} | {r['model']} | {P6_NAME[r['strategy']]} | {r['scenario']} | {r['trades']:,} "
            f"| {_ci(r)} | {_bps(r)} | {fmt(b['random'])} | {fmt(b['volfilter'])} |"
        )
    al = ov.filter(pl.col("strategy").str.starts_with("always"))
    out += [
        "",
        "| period | baseline | scenario | trades | mean net R [95% CI] | net / gross bps |",
        "|---|---|---|---|---|---|",
    ]
    for r in al.sort("period", "strategy", "scenario", descending=[False, False, True]).iter_rows(named=True):
        out.append(
            f"| {r['period']} | {r['strategy']} | {r['scenario']} | {r['trades']:,} | {_ci(r)} | {_bps(r)} |"
        )
    return "\n".join(out) + "\n"


@block("phase6_c_targets")
def phase6_c_targets() -> str:
    """Scope-doc targets (research objectives, not pass criteria) for every model configuration, taker."""
    ov = _p6_ov()
    d = pl.read_csv(P6E / "directional.csv")
    per = _p6_meta_period(ov)
    out = [
        "| model | strategy | period | trades | hit rate (net R > 0) — target 54–58% "
        "| direction right (gross R > 0) | Sharpe (annualized, 1% risk) — target ≥ 1.8 "
        "| profit factor — target ≥ 1.6 | max drawdown — target < 12% | targets met |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model in ("lgbm", "catboost"):
        for strat in ("primary", "primary_meta060", "primary_metaval"):
            period = (
                [p for p in ov["period"].unique().to_list() if p.startswith("folds 0-")][0]
                if strat == "primary"
                else per
            )
            r = _p6_row(ov, period=period, model=model, strategy=strat, scenario="taker", seed=-1)
            if not r or not r["trades"]:
                continue
            dsel = {"model": model, "strategy": strat, "scenario": "taker", "seed": -1}
            dr = d.filter(*[pl.col(k) == v for k, v in dsel.items()])["direction_right"]
            met = [
                0.54 <= r["hit_rate"],
                (r["sharpe"] or float("-inf")) >= 1.8,
                r["profit_factor"] >= 1.6,
                r["max_drawdown"] < 0.12,
            ]
            out.append(
                f"| {model} | {P6_NAME[strat]} | {period} | {r['trades']:,} | {r['hit_rate']:.1%} "
                f"| {dr[0]:.1%} | {fmt(r['sharpe'], 2)} | {r['profit_factor']:.2f} | {r['max_drawdown']:.1%} "
                f"| {sum(met)} of 4 |"
            )
    return "\n".join(out) + "\n"


@block("phase6_c_shuffled")
def phase6_c_shuffled() -> str:
    ov = _p6_ov()
    out = [
        "| period | strategy (LightGBM, taker) | model mean net R | shuffled runs with trades "
        "| shuffled min / median / max | shuffled runs beaten |",
        "|---|---|---|---|---|---|",
    ]
    for per in sorted(ov["period"].unique().to_list()):
        for strat in ("primary", "primary_meta060", "primary_metaval"):
            r = _p6_row(ov, period=per, model="lgbm", strategy=strat, scenario="taker", seed=-1)
            ssel = {"period": per, "scenario": "taker", "strategy": f"shuffled_{strat}"}
            sh = ov.filter(*[pl.col(k) == v for k, v in ssel.items()])["mean_R"].drop_nulls()
            if not r or not r["trades"]:
                continue
            out.append(
                f"| {per} | {P6_NAME[strat]} | {fmt(r['mean_R'])} | {sh.len()} "
                f"| {fmt(sh.min())} / {fmt(sh.median())} / {fmt(sh.max())} "
                f"| {(r['mean_R'] > sh).sum()} of {sh.len()} |"
            )
    return "\n".join(out) + "\n"


@block("phase6_c_shap")
def phase6_c_shap() -> str:
    imp = pl.read_csv(P6E / "shap_importance.csv")
    stab = pl.read_csv(P6E / "shap_stability.csv")
    out = ["Top 10 features by mean |SHAP| (LightGBM; share of the model's total):", ""]
    for model in ("primary_long", "primary_short", "meta"):
        g = imp.filter((pl.col("kind") == "lgbm") & (pl.col("model") == model)).sort("rank").head(10)
        out.append(
            f"- **{model}:** "
            + ", ".join(f"{r['feature']} ({r['group']}, {r['share']:.1%})" for r in g.iter_rows(named=True))
        )
    groups = ("technical", "derivatives", "news", "calendar", "mark_premium", "regulatory")
    out += ["", "Share of total |SHAP| by feature group:", ""]
    out += ["| model | " + " | ".join(groups) + " |", "|---|" + "---|" * len(groups)]
    for (kind, model), g in imp.filter(pl.col("model") != "meta").group_by(
        "kind", "model", maintain_order=True
    ):
        s = g.group_by("group").agg(pl.col("share").sum())
        cells = [s.filter(pl.col("group") == grp)["share"] for grp in groups]
        out.append(
            f"| {kind} {model} | " + " | ".join(f"{c[0]:.1%}" if c.len() else "–" for c in cells) + " |"
        )
    out += [
        "",
        "Fold-to-fold stability (Spearman rank correlation of feature importances between folds):",
        "",
    ]
    out += ["| model | folds | mean ρ | min ρ |", "|---|---|---|---|"]
    for r in stab.sort("kind", "model").iter_rows(named=True):
        out.append(
            f"| {r['kind']} {r['model']} | {r['folds']} | {r['spearman_mean']:.2f} "
            f"| {r['spearman_min']:.2f} |"
        )
    return "\n".join(out) + "\n"


@block("phase6_c_regimes")
def phase6_c_regimes() -> str:
    reg = pl.read_csv(P6E / "regimes.csv").filter((pl.col("scenario") == "taker") & (pl.col("seed") == -1))
    cols = [
        ("lgbm", "primary_meta060"),
        ("lgbm", "primary"),
        ("catboost", "primary_meta060"),
        ("baseline", "always_long"),
    ]
    head = " | ".join(f"{m} {P6_NAME.get(s, s)}" for m, s in cols)
    out = [f"| regime | {head} |", "|---|" + "---|" * len(cols)]
    for dim, order in (
        ("vol_regime", ["low vol", "mid vol", "high vol"]),
        ("trend_regime", ["30d up", "30d down"]),
    ):
        for g in order:
            cells = []
            for m, s in cols:
                x = reg.filter(
                    (pl.col("dimension") == dim)
                    & (pl.col("regime") == g)
                    & (pl.col("model") == m)
                    & (pl.col("strategy") == s)
                )
                cells.append(f"{fmt(x['mean_R'][0])} ({x['trades'][0]:,})" if x.height else "·")
            out.append(f"| {g} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


@block("phase6_c_failures")
def phase6_c_failures() -> str:
    f = pl.read_csv(P6E / "folds_by_result.csv")
    w = pl.read_csv(P6E / "worst_trades.csv")
    out = ["Headline per fold, worst first (mean net R, taker):", ""]
    out += [
        "| fold | trades | mean net R | long share | stop first | expiry | take-profit |",
        "|---|" + "---|" * 6,
    ]
    for r in f.iter_rows(named=True):
        out.append(
            f"| {r['fold']} | {r['trades']} | {fmt(r['mean_R'])} | {r['long_share']:.0%} "
            f"| {r['share_sl']:.0%} | {r['share_expiry']:.0%} | {r['share_tp']:.0%} |"
        )
    out += ["", "Worst headline trades:", ""]
    out += ["| decision time (UTC) | fold | side | exit | net R | vol regime | trend |", "|---|" + "---|" * 6]
    for r in w.iter_rows(named=True):
        when = datetime.fromtimestamp(r["decision_time"] / 1000, UTC).strftime("%Y-%m-%d %H:%M")
        side = "long" if r["side"] == 1 else "short"
        out.append(
            f"| {when} | {r['fold']} | {side} | {r['exit_class']} | {fmt(r['net_R'], 2)} "
            f"| {r['vol_regime'] or '–'} | {r['trend_regime'] or '–'} |"
        )
    return "\n".join(out) + "\n"


@block("phase6_c_folds")
def phase6_c_folds() -> str:
    pf = pl.read_csv(P6E / "fold_metrics.csv").filter(pl.col("scenario") == "taker")

    def cell(x):
        return "·" if not x.height or not x["trades"][0] else f"{fmt(x['mean_R'][0], 2)} ({x['trades'][0]})"

    out = [
        "| fold | LightGBM primary | LightGBM + meta 0.60 | CatBoost primary | CatBoost + meta 0.60 "
        "| always-long | shuffled + meta (median) |",
        "|---|" + "---|" * 6,
    ]

    def one(x, model, strat):
        return cell(
            x.filter((pl.col("model") == model) & (pl.col("strategy") == strat) & (pl.col("seed") == -1))
        )

    for fold in sorted(pf["fold"].unique().to_list()):
        x = pf.filter(pl.col("fold") == fold)

        sh = x.filter(pl.col("strategy") == "shuffled_primary_meta060").filter(pl.col("trades") > 0)["mean_R"]
        out.append(
            f"| {fold} | {one(x, 'lgbm', 'primary')} | {one(x, 'lgbm', 'primary_meta060')} "
            f"| {one(x, 'catboost', 'primary')} | {one(x, 'catboost', 'primary_meta060')} "
            f"| {one(x, 'baseline', 'always_long')} "
            f"| {fmt(sh.median(), 2) if sh.len() else '·'} |"
        )
    return "\n".join(out) + "\n"


@block("phase6_c_meta_p")
def phase6_c_meta_p() -> str:
    d = pl.read_csv(P6E / "meta_p.csv")
    out = [
        "| primary | candidates scored by the meta-model | actual success rate (net R > 0) "
        "| P(success): median | 90th pct | 99th pct | share ≥ 0.60 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in d.iter_rows(named=True):
        out.append(
            f"| {r['kind']} | {r['candidates']:,} | {r['success_rate']:.1%} | {r['p_median']:.2f} "
            f"| {r['p_q90']:.2f} | {r['p_q99']:.2f} | {r['share_ge_060']:.2%} |"
        )
    return "\n".join(out) + "\n"


@block("phase6_d_rest_archive")
def phase6_d_rest_archive() -> str:
    d = pl.read_csv(P6 / "live" / "rest_vs_archive.csv")
    g = d.group_by("dataset", maintain_order=True).agg(
        windows=pl.len(), archive=pl.col("archive_rows").sum(), rest=pl.col("rest_rows").sum(),
        identical=pl.col("rows_identical").sum(),
    )  # fmt: skip
    out = [
        "| dataset | 6 h windows | archive rows | REST rows | rows identical (every compared column) |",
        "|---|---|---|---|---|",
    ]
    for r in g.iter_rows(named=True):
        out.append(
            f"| {r['dataset']} | {r['windows']} | {r['archive']:,} | {r['rest']:,} | {r['identical']:,} |"
        )
    return "\n".join(out) + "\n"


# ---------- FINDINGS.md headline numbers ----------


@block("findings_headline")
def findings_headline() -> str:
    """Key numbers for FINDINGS.md, all derived from committed report files."""
    bps = pl.read_csv(R / "phase3_step2/bps_summary.csv").filter(
        (pl.col("set") == "A") & (pl.col("scen") == "taker")
    )
    ov2 = _overall(R / "phase3_step2/overall_metrics.csv")
    a_taker = ov2.filter((pl.col("label_set") == "A") & (pl.col("scenario") == "taker"))

    def g(strat, rung, col="gross_bps"):
        return bps.filter((pl.col("strat") == strat) & (pl.col("rung") == rung))[col][0]

    best_gross = max(
        bps.filter(pl.col("strat") != "lgbm_shuffled").iter_rows(named=True), key=lambda r: r["gross_bps"]
    )
    shuffled = g("lgbm_shuffled", 3)
    cost = best_gross["gross_bps"] - best_gross["net_bps"]
    models = ov2.filter(~pl.col("strategy").str.contains("random|volfilter|always|shuffled"))
    best_r = models.sort("mean_R", descending=True).row(0, named=True)
    always = a_taker.filter(pl.col("strategy").str.starts_with("always"))["mean_R"].mean()
    rung = {
        r: a_taker.filter((pl.col("strategy") == "lgbm") & (pl.col("rung") == r))["mean_R"][0]
        for r in (1, 2, 3)
    }
    rung_t = {
        r: a_taker.filter((pl.col("strategy") == "lgbm_tuned") & (pl.col("rung") == r))["mean_R"][0]
        for r in (1, 2, 3)
    }
    dsr = json.loads((R / "phase3_step3/dsr.json").read_text())
    pbo = json.loads((R / "phase3_step3/pbo.json").read_text())
    rows = [
        (
            "highest gross edge (A, taker)",
            f"{best_gross['strat']} rung {best_gross['rung']}: "
            f"{fmt(best_gross['gross_bps'], 1)} bps per trade",
        ),
        ("shuffled-label control's gross edge (same threshold procedure)", f"{fmt(shuffled, 1)} bps"),
        ("gross edge beyond the shuffled control", f"{fmt(best_gross['gross_bps'] - shuffled, 1)} bps"),
        ("costs of that configuration (gross − net)", f"{cost:.1f} bps per trade"),
        (
            "best mean net R of any model configuration [95% CI]",
            f"{fmt(best_r['mean_R'])} [{fmt(best_r['ci_lo'])}, {fmt(best_r['ci_hi'])}] "
            f"({best_r['label_set']}, {best_r['scenario']}, {best_r['strategy']} rung {best_r['rung']})",
        ),
        ("always-trade baseline, A taker (mean of long and short)", f"{fmt(always)} R"),
        (
            "news ablation, A taker, untuned LightGBM rung 1 / 2 / 3",
            " / ".join(fmt(rung[r]) for r in (1, 2, 3)) + " R",
        ),
        (
            "news ablation, A taker, tuned LightGBM rung 1 / 2 / 3",
            " / ".join(fmt(rung_t[r]) for r in (1, 2, 3)) + " R",
        ),
        (
            "deflated Sharpe ratio of the best configuration",
            f"{dsr['dsr']:.4f} (N = {dsr['n_trials']:,} trials)",
        ),
        (
            "probability of backtest overfitting (CSCV)",
            f"{pbo['pbo']:.3f} across {pbo['n_strategies']} configurations",
        ),
    ]
    return "| | |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows) + "\n"


# ---------- rewrite / check ----------


def render(doc: str) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in BLOCKS:
            raise KeyError(f"no generator registered for block {name!r}")
        return f"<!-- generated:{name} -->\n{BLOCKS[name]()}<!-- /generated:{name} -->"

    return MARK.sub(sub, doc)


def mismatches(path: Path) -> list[str]:
    """Names of generated blocks in `path` that differ from their source."""
    doc = path.read_text()
    return [m.group(1) for m in MARK.finditer(doc) if m.group(2) != BLOCKS[m.group(1)]()]


def main() -> None:
    docs = [p for p in DOCS if p.exists()]
    if "--check" in sys.argv:
        bad = {str(p): mismatches(p) for p in docs}
        bad = {k: v for k, v in bad.items() if v}
        if bad:
            print(f"generated tables out of date: {bad}")
            sys.exit(1)
        print("all generated tables match their sources")
        return
    for p in docs:
        p.write_text(render(p.read_text()))
    print("rebuilt generated tables in", ", ".join(map(str, docs)))


if __name__ == "__main__":
    main()
