import json
import time
from itertools import combinations
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import spearmanr

from mbpe.features.market import REGISTRY
from mbpe.features.news import NEWS_REGISTRY
from mbpe.research import phase6_models as p6
from mbpe.research.backtest import simulate
from mbpe.research.experiment import load_dataset, prepare_arrays, summarize
from mbpe.research.walk_forward import make_folds, split

OUT = Path("reports/phase6/eval")
TRADES = Path("data/features/phase6_trades.parquet")
CANDIDATES = Path("data/features/phase6_candidates.parquet")
KLINES = Path("data/clean/binance/futures_klines_1m.parquet")
KINDS = ("lgbm", "catboost")
STRATEGIES = ("primary", "primary_meta060", "primary_metaval")
N_PERMUTATIONS = 20
HEADLINE = {"model": "lgbm", "strategy": "primary_meta060", "scenario": "taker"}
TARGETS = {"hit_rate": (0.54, 0.58), "sharpe": 1.8, "profit_factor": 1.6, "max_drawdown": 0.12}
GROUP = {n: f.group for n, f in {**REGISTRY, **NEWS_REGISTRY}.items()}


def tag(t: pl.DataFrame, **k) -> pl.DataFrame:
    return t.with_columns(
        label_set=pl.lit("A"), rung=pl.lit(p6.PHASE6_RUNG), **{a: pl.lit(b) for a, b in k.items()}
    )


def controls(cand: pl.DataFrame, df: pl.DataFrame, cfg, kind: str) -> pl.DataFrame:
    decision, arr, _, eligible = prepare_arrays(df, cfg)
    atr = df["atr_1h_pct"].fill_null(np.nan).to_numpy()
    out = []
    for fold in make_folds(cfg):
        masks = split(decision, arr["long"]["exit"], fold, cfg)
        test, val = masks["test"], masks["val"]
        el = eligible[test]
        dt, n = decision[test], test.sum()
        c = cand.filter(pl.col("fold") == fold.index)
        for strat in STRATEGIES:
            if strat != "primary" and fold.index < p6.META_FIRST_FOLD:
                continue
            keep = (
                np.ones(c.height, bool)
                if strat == "primary"
                else (c["meta_p"].fill_null(-1) >= p6.META_THRESHOLD).to_numpy()
            )
            if strat == "primary_metaval":
                keep = (c["meta_p"].fill_null(-1) >= c["val_thr"].fill_null(np.inf)).to_numpy()
            q = {
                s: float(((c["side"] == v).to_numpy() & keep).sum() / max(el.sum(), 1))
                for s, v in (("long", 1), ("short", -1))
            }
            qtot = q["long"] + q["short"]
            for seed in range(cfg.random_seeds):
                r = np.random.default_rng([fold.index, seed, STRATEGIES.index(strat), KINDS.index(kind), 6])
                rs = {s: (r.random(n) < q[s]) & el for s in ("long", "short")}
                out += _trades(
                    dt, rs["long"], rs["short"], arr, test, f"random_vs_{strat}", kind, seed, fold.index
                )
                if qtot > 0:
                    v_ok = val & eligible & np.isfinite(atr)
                    cut = np.quantile(atr[v_ok], max(0.0, 1 - qtot)) if v_ok.any() else np.inf
                    hot = (np.nan_to_num(atr[test], nan=-np.inf) >= cut) & el
                    up = r.random(n) < q["long"] / qtot
                    out += _trades(
                        dt, hot & up, hot & ~up, arr, test, f"volfilter_vs_{strat}", kind, seed, fold.index
                    )
        if kind == KINDS[0]:
            off = np.zeros(n, bool)
            out += _trades(dt, el, off, arr, test, "always_long", "baseline", -1, fold.index)
            out += _trades(dt, off, el, arr, test, "always_short", "baseline", -1, fold.index)
    return pl.concat(out, how="diagonal_relaxed")


def _trades(dt, sig_l, sig_s, arr, test, strategy, model, seed, fold) -> list[pl.DataFrame]:
    idx, sd = simulate(dt, sig_l, sig_s, arr["long"]["exit"][test], arr["short"]["exit"][test])
    rows = np.flatnonzero(test)[idx]
    is_long = sd == 1
    frames = []
    for scen in ("taker", "maker_entry_tp"):
        frames.append(
            tag(
                pl.DataFrame(
                    {
                        "decision_time": dt[idx],
                        "side": sd,
                        "exit_time": p6.by_side(is_long, rows, arr["long"]["exit"], arr["short"]["exit"]),
                        "gross_R": p6.by_side(is_long, rows, arr["long"]["gross"], arr["short"]["gross"]),
                        "bps_per_R": p6.by_side(is_long, rows, arr["long"]["bps"], arr["short"]["bps"]),
                        "net_R": p6.by_side(
                            is_long, rows, arr["long"]["net"][scen], arr["short"]["net"][scen]
                        ),
                    }
                ),
                strategy=strategy,
                model=model,
                seed=seed,
                fold=fold,
                scenario=scen,
            )
        )
    return frames


def directional(trades: pl.DataFrame) -> pl.DataFrame:
    keys = ["scenario", "strategy", "model", "seed"]
    return trades.group_by(keys).agg(direction_right=(pl.col("gross_R") > 0).mean(), trades=pl.len())


def shap_tables(shap: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    long_rows, stab = [], []
    for (kind, model), g in shap.group_by("kind", "model", maintain_order=True):
        feats = [c for c in g.columns if c not in ("fold", "model", "kind") and g[c].null_count() < g.height]
        M = g.select(feats).to_numpy()
        mean = np.nanmean(M, axis=0)
        total = np.nansum(mean)
        for f, v in zip(feats, mean, strict=True):
            long_rows.append(
                {
                    "kind": kind,
                    "model": model,
                    "feature": f,
                    "group": GROUP.get(f, "meta input"),
                    "mean_abs_shap": v,
                    "share": v / total if total else np.nan,
                }
            )
        rhos = [spearmanr(M[i], M[j]).statistic for i, j in combinations(range(len(M)), 2)]
        stab.append(
            {
                "kind": kind,
                "model": model,
                "folds": len(M),
                "spearman_mean": float(np.nanmean(rhos)),
                "spearman_min": float(np.nanmin(rhos)),
            }
        )
    imp = pl.DataFrame(long_rows).with_columns(
        rank=pl.col("mean_abs_shap").rank("ordinal", descending=True).over("kind", "model")
    )
    return imp, pl.DataFrame(stab)


def regimes(decision_ms: np.ndarray, cfg) -> pl.DataFrame:
    k = pl.read_parquet(KLINES, columns=["open_time", "close"]).sort("open_time")
    h = (
        k.group_by_dynamic("open_time", every="1h")
        .agg(pl.col("close").last())
        .select(decision_time=pl.col("open_time").dt.offset_by("1h"), close="close")
        .with_columns(r=pl.col("close").log().diff())
        .with_columns(
            vol30=pl.col("r").rolling_std(720, min_samples=600), ret30=pl.col("close").log().diff(720)
        )
    )
    d = pl.DataFrame({"decision_time": decision_ms}).with_columns(
        pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))
    )
    d = d.join(h.select("decision_time", "vol30", "ret30"), on="decision_time", how="left")
    test = d["decision_time"].dt.epoch("ms") >= make_folds(cfg)[0].test_start
    lo, hi = np.nanquantile(d.filter(test)["vol30"].to_numpy(), [1 / 3, 2 / 3])
    return d.with_columns(
        vol_regime=pl.when(pl.col("vol30").is_null())
        .then(None)
        .when(pl.col("vol30") < lo)
        .then(pl.lit("low vol"))
        .when(pl.col("vol30") < hi)
        .then(pl.lit("mid vol"))
        .otherwise(pl.lit("high vol")),
        trend_regime=pl.when(pl.col("ret30").is_null())
        .then(None)
        .when(pl.col("ret30") > 0)
        .then(pl.lit("30d up"))
        .otherwise(pl.lit("30d down")),
        decision_time=pl.col("decision_time").dt.epoch("ms"),
    )


def meta_p_summary(cand: pl.DataFrame) -> pl.DataFrame:
    c = cand.filter(pl.col("meta_p").is_not_null())
    return (
        c.group_by("kind")
        .agg(
            candidates=pl.len(),
            success_rate=(pl.col("net_R_taker") > 0).mean(),
            p_median=pl.col("meta_p").median(),
            p_q90=pl.col("meta_p").quantile(0.9),
            p_q99=pl.col("meta_p").quantile(0.99),
            p_max=pl.col("meta_p").max(),
            share_ge_060=(pl.col("meta_p") >= p6.META_THRESHOLD).mean(),
        )
        .sort("kind", descending=True)
    )


def main() -> None:
    t0 = time.perf_counter()
    cfg = p6.cfg_p6()
    df = load_dataset("A", cfg)
    model_trades, cands, shaps, infos = [], [], [], []
    for kind in KINDS:
        res = p6.run(kind, cfg=cfg, df=df)
        vthr = {
            r["fold"]: r["val_threshold"]
            for r in res["info"].filter(pl.col("side") == "meta").iter_rows(named=True)
        }
        c = res["candidates"].with_columns(
            pl.col("fold").replace_strict(vthr, default=None, return_dtype=pl.Float64).alias("val_thr")
        )
        cands.append(c.with_columns(kind=pl.lit(kind)))
        model_trades.append(tag(res["trades"], model=kind, seed=-1))
        model_trades.append(controls(c, df, cfg, kind))
        shaps.append(res["shap"].with_columns(kind=pl.lit(kind)))
        infos.append(res["info"].with_columns(kind=pl.lit(kind)))
        print(f"{kind}: {time.perf_counter() - t0:.0f}s", flush=True)
    for perm in range(N_PERMUTATIONS):
        res = p6.run("lgbm", cfg=cfg, df=df, shuffle_seed=perm)
        model_trades.append(
            tag(res["trades"], model="lgbm", seed=perm).with_columns(
                pl.format("shuffled_{}", "strategy").alias("strategy")
            )
        )
    print(f"permutations: {time.perf_counter() - t0:.0f}s", flush=True)
    trades = pl.concat(model_trades, how="diagonal_relaxed")
    folds = make_folds(cfg)
    pf, ov = summarize(trades, cfg)
    _, ov_meta = summarize(
        trades.filter(pl.col("fold") >= p6.META_FIRST_FOLD), cfg, folds[p6.META_FIRST_FOLD :]
    )
    OUT.mkdir(parents=True, exist_ok=True)
    pf.write_csv(OUT / "fold_metrics.csv")
    pl.concat(
        [
            ov.with_columns(period=pl.lit(f"folds 0-{len(folds) - 1}")),
            ov_meta.with_columns(period=pl.lit(f"folds {p6.META_FIRST_FOLD}-{len(folds) - 1}")),
        ]
    ).write_csv(OUT / "overall_metrics.csv")
    directional(trades).write_csv(OUT / "directional.csv")
    imp, stab = shap_tables(pl.concat(shaps, how="diagonal_relaxed"))
    imp.write_csv(OUT / "shap_importance.csv")
    stab.write_csv(OUT / "shap_stability.csv")
    pl.concat(infos, how="diagonal_relaxed").write_csv(OUT / "model_info.csv")
    cand = pl.concat(cands, how="diagonal_relaxed")
    reg = regimes(np.unique(trades["decision_time"].to_numpy()), cfg)
    tr = trades.join(reg, on="decision_time", how="left")
    keys = ["model", "strategy", "scenario", "seed"]
    pl.concat(
        [
            tr.filter(pl.col(r).is_not_null())
            .group_by(*keys, regime=pl.col(r))
            .agg(trades=pl.len(), mean_R=pl.col("net_R").mean(), dimension=pl.lit(r))
            for r in ("vol_regime", "trend_regime")
        ]
    ).write_csv(OUT / "regimes.csv")
    h = tr.filter(*[pl.col(k) == v for k, v in HEADLINE.items()], pl.col("seed") == -1)
    h.sort("net_R").head(15).select(
        "decision_time", "fold", "side", "exit_class", "net_R", "vol_regime", "trend_regime"
    ).with_columns(
        pl.col("exit_class").replace_strict(dict(enumerate(p6.CLASSES)), return_dtype=pl.String)
    ).write_csv(OUT / "worst_trades.csv")
    h.group_by("fold").agg(
        trades=pl.len(),
        mean_R=pl.col("net_R").mean(),
        long_share=(pl.col("side") == 1).mean(),
        **{f"share_{c}": (pl.col("exit_class") == i).mean() for i, c in enumerate(p6.CLASSES)},
    ).sort("mean_R").write_csv(OUT / "folds_by_result.csv")
    trades.write_parquet(TRADES)
    cand.write_parquet(CANDIDATES)
    meta_p_summary(cand).write_csv(OUT / "meta_p.csv")
    (OUT / "run.json").write_text(
        json.dumps({"seconds": round(time.perf_counter() - t0), "trade_rows": trades.height})
    )
    print(f"done in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
