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
