"""Evaluation harness: walk-forward models, baselines, simulator, metrics, MLflow (protocol in SCOPE.md).

Usage: uv run python -m mbpe.validation.harness   -> reports/phase3_step1/*.csv + MLflow (mlruns/)

For each label set (A, B) x rung (1-3) x model (logreg, lgbm) x test fold x side (long, short):
- fit on the purged training rows with uniqueness weights (early stopping on validation, LightGBM)
- calibrate with isotonic regression on the validation slice
- per cost scenario: choose the threshold on validation (max mean net R, >= min_val_trades), then trade the
  test fold through the position rule and the blackout
Baselines per fold: always-long, always-short, and random entry at the model's per-side signal frequency
(random_seeds seeds). Holdout rows are never loaded (guard in walk_forward).
"""

import os
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from mbpe.features.market import MARKET_PATH, REGISTRY
from mbpe.features.news import NEWS_REGISTRY, load_calendar
from mbpe.labels.triple_barrier import load_config, scenario_net_r
from mbpe.validation.backtest import blackout, metrics, simulate
from mbpe.validation.walk_forward import (
    EvalConfig,
    guard_holdout,
    load_eval_config,
    make_folds,
    split,
    to_ms,
    uniqueness,
)

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
LABEL_SETS = {"A": Path("config/labels.yaml"), "B": Path("config/labels_B.yaml")}
SCENARIOS = ("taker", "maker_entry_tp")
RUNGS = {
    1: ("technical",),
    2: ("technical", "derivatives"),
    3: ("technical", "derivatives", "news", "calendar"),
}
MODELS = ("logreg", "lgbm")
SIDES = ("long", "short")
REPORT_DIR = Path("reports/phase3_step1")
TRACKING_URI = "sqlite:///mlruns/mlflow.db"
GROUP = {n: f.group for n, f in {**REGISTRY, **NEWS_REGISTRY}.items()}


def rung_features(rung: int) -> list[str]:
    return [n for n, g in GROUP.items() if g in RUNGS[rung]]


def is_categorical(name: str) -> bool:
    return name.startswith("oi_price_quadrant")


# ---------- data ----------


def load_dataset(
    label_set: str, cfg: EvalConfig, allow_holdout: bool = False, news_path: Path | None = None
) -> pl.DataFrame:
    """One row per decision time: both sides' label columns + all features, inside the evaluation window."""
    lcfg = load_config(LABEL_SETS[label_set])
    lab = pl.read_parquet(lcfg.output)
    lab = lab.with_columns(
        net_R_taker=pl.col("net_R"),
        net_R_maker_entry_tp=scenario_net_r(lab, lcfg, "maker_entry_tp"),
        bps_per_R=(pl.col("entry_price") - pl.col("sl")).abs() / pl.col("entry_price") * 1e4,
    )
    keep = ["label", "exit_time", "gross_R", "net_R_taker", "net_R_maker_entry_tp", "bps_per_R"]
    wide = lab.filter(pl.col("side") == "long").select(
        "decision_time", *[pl.col(c).alias(f"long_{c}") for c in keep]
    )
    wide = wide.join(
        lab.filter(pl.col("side") == "short").select(
            "decision_time", *[pl.col(c).alias(f"short_{c}") for c in keep]
        ),
        on="decision_time",
    )
    feats = pl.read_parquet(MARKET_PATH).join(
        pl.read_parquet(news_path or cfg.news_features), on="decision_time"
    )
    df = (
        wide.join(feats, on="decision_time")
        .filter(pl.col("decision_time") >= cfg.eval_start)
        .sort("decision_time")
    )
    if not allow_holdout:  # outcomes too: a trade's exit must also precede the holdout
        h = cfg.holdout_start
        df = df.filter(
            (pl.col("decision_time") < h) & (pl.col("long_exit_time") < h) & (pl.col("short_exit_time") < h)
        )
    return guard_holdout(df, cfg, allow_holdout, reason=f"load_dataset {label_set}")


# ---------- models ----------


def fit_predict(
    kind: str, names: list[str], data: dict, cfg: EvalConfig, params: dict | None = None
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Calibrated probabilities on validation and test. data: X/y/w for train, val; X for test.

    `params` overrides the configured LightGBM settings (tuned models).
    """
    cat = [i for i, n in enumerate(names) if is_categorical(n)]
    num = [i for i in range(len(names)) if i not in cat]
    if kind == "lgbm":
        dtr = lgb.Dataset(
            data["X_train"],
            data["y_train"],
            weight=data["w_train"],
            categorical_feature=cat,
            free_raw_data=False,
        )
        dv = lgb.Dataset(
            data["X_val"], data["y_val"], weight=data["w_val"], reference=dtr, categorical_feature=cat
        )
        booster = lgb.train(
            {**cfg.lgbm, **(params or {})},
            dtr,
            num_boost_round=cfg.lgbm_max_rounds,
            valid_sets=[dv],
            callbacks=[lgb.early_stopping(cfg.lgbm_early_stopping, verbose=False)],
        )
        it = booster.best_iteration or booster.current_iteration()
        raw_v, raw_t = (
            booster.predict(data["X_val"], num_iteration=it),
            booster.predict(data["X_test"], num_iteration=it),
        )
        info = {"best_iteration": it}
    else:
        pre = ColumnTransformer(
            [
                (
                    "num",
                    make_pipeline(
                        SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
                        StandardScaler(),
                    ),
                    num,
                ),
                (
                    "cat",
                    make_pipeline(
                        SimpleImputer(strategy="constant", fill_value=0),
                        OneHotEncoder(handle_unknown="ignore"),
                    ),
                    cat,
                ),
            ]
        )
        model = make_pipeline(pre, LogisticRegression(C=cfg.logreg_C, max_iter=2000))
        model.fit(data["X_train"], data["y_train"], logisticregression__sample_weight=data["w_train"])
        raw_v, raw_t = model.predict_proba(data["X_val"])[:, 1], model.predict_proba(data["X_test"])[:, 1]
        info = {}
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(
        raw_v, data["y_val"], sample_weight=data["w_val"]
    )
    return iso.predict(raw_v), iso.predict(raw_t) if len(raw_t) else np.zeros(0), info


def threshold_stats(
    p: np.ndarray,
    decision: np.ndarray,
    exit_: np.ndarray,
    net: dict[str, np.ndarray],
    eligible: np.ndarray,
    cfg: EvalConfig,
) -> dict[str, tuple[float | None, float, int]]:
    """Per scenario: (threshold, validation mean net R, validation trades) of the threshold rule.

    The threshold maximizes validation mean net R with >= min_val_trades; (None, -inf, 0) if none qualifies.
    One side alone through the simulator (position rule within the side). Ties -> the lowest threshold.
    """
    if not eligible.any():
        return {s: (None, -np.inf, 0) for s in net}
    cands = np.unique(np.quantile(p[eligible], np.linspace(0, 0.995, cfg.threshold_candidates)))
    best = {s: (None, -np.inf, 0) for s in net}
    none = np.zeros(len(p), bool)
    for c in cands:
        idx, _ = simulate(decision, (p >= c) & eligible, none, exit_, exit_)
        if len(idx) < cfg.min_val_trades:
            continue
        for s, r in net.items():
            m = r[idx].mean()
            if m > best[s][1]:
                best[s] = (float(c), float(m), len(idx))
    return best


def select_threshold(p, decision, exit_, net, eligible, cfg) -> dict[str, float | None]:
    """Per scenario: the threshold of threshold_stats (None if no candidate meets min_val_trades)."""
    return {s: v[0] for s, v in threshold_stats(p, decision, exit_, net, eligible, cfg).items()}


# ---------- one configuration ----------


def side_arrays(df: pl.DataFrame, side: str) -> dict[str, np.ndarray]:
    return {
        "y": df[f"{side}_label"].cast(pl.Int8).to_numpy(),
        "exit": df[f"{side}_exit_time"].dt.epoch("ms").to_numpy(),
        "net": {s: df[f"{side}_net_R_{s}"].to_numpy() for s in SCENARIOS},
        "gross": df[f"{side}_gross_R"].to_numpy(),
        "bps": df[f"{side}_bps_per_R"].to_numpy(),
    }


def trades_frame(
    df_idx: np.ndarray, sides: np.ndarray, decision: np.ndarray, arr: dict, scenario: str
) -> pl.DataFrame:
    exit_ = np.where(sides == 1, arr["long"]["exit"][df_idx], arr["short"]["exit"][df_idx])
    net = np.where(sides == 1, arr["long"]["net"][scenario][df_idx], arr["short"]["net"][scenario][df_idx])
    bps = np.where(sides == 1, arr["long"]["bps"][df_idx], arr["short"]["bps"][df_idx])
    gross = np.where(sides == 1, arr["long"]["gross"][df_idx], arr["short"]["gross"][df_idx])
    return pl.DataFrame(
        {
            "decision_time": decision[df_idx],
            "side": sides,
            "exit_time": exit_,
            "net_R": net,
            "gross_R": gross,
            "bps_per_R": bps,
        }
    )


def prepare_arrays(df: pl.DataFrame, cfg: EvalConfig):
    """(decision ms, per-side arrays, per-side uniqueness weights, eligible = not blacked out)."""
    decision = df["decision_time"].dt.epoch("ms").to_numpy()
    arr = {s: side_arrays(df, s) for s in SIDES}
    weights = {s: uniqueness(decision, arr[s]["exit"]) for s in SIDES}
    events = load_calendar()
    eligible = ~blackout(
        decision, events, cfg.blackout_before_minutes, cfg.blackout_horizon_minutes, cfg.latency_minutes
    )
    return decision, arr, weights, eligible


def run_label_set(
    label_set: str,
    cfg: EvalConfig,
    models=MODELS,
    rungs=RUNGS,
    shuffle_labels: bool = False,
    folds=None,
    df: pl.DataFrame | None = None,
    scenarios=SCENARIOS,
    tuned_params: dict | None = None,
    baselines: bool = True,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """All rungs x models for one label set -> (trades, thresholds); trades tagged by strategy/config.

    `df` (load_dataset's layout) replaces loading from disk, for tests. `tuned_params[(rung, fold)]` overrides
    the LightGBM settings (strategy "lgbm_tuned"). `baselines=False` skips always-long/short.
    """
    df = load_dataset(label_set, cfg) if df is None else df
    decision, arr, weights, eligible = prepare_arrays(df, cfg)
    atr = df["atr_1h_pct"].fill_null(np.nan).to_numpy()
    trades, thresholds = [], []
    rng_shuffle = np.random.default_rng(12345)

    def add(tr: pl.DataFrame, **tags):
        trades.append(tr.with_columns(*[pl.lit(v).alias(k) for k, v in tags.items()]))

    for fold in folds or make_folds(cfg):
        masks = {s: split(decision, arr[s]["exit"], fold, cfg) for s in SIDES}
        test = masks["long"]["test"]  # same decision times for both sides
        dt = decision[test]
        tag = {"label_set": label_set, "fold": fold.index}
        # baselines: always-long / always-short (position rule within the side), every scenario
        for scen in scenarios if baselines else ():
            for side, sgn in (("long", 1), ("short", -1)):
                sig, off = eligible[test], np.zeros(test.sum(), bool)
                idx, sd = simulate(
                    dt,
                    sig if sgn == 1 else off,
                    sig if sgn == -1 else off,
                    arr["long"]["exit"][test],
                    arr["short"]["exit"][test],
                )
                add(
                    trades_frame(np.flatnonzero(test)[idx], sd, decision, arr, scen),
                    strategy=f"always_{side}",
                    rung=0,
                    model="baseline",
                    scenario=scen,
                    seed=-1,
                    **tag,
                )
        for rung in rungs:
            names = rung_features(rung)
            X = df.select(names).cast(pl.Float64).to_numpy()
            for kind in models:
                probs, thr = {}, {}
                for side in SIDES:
                    m = masks[side]
                    y_tr, y_v = arr[side]["y"][m["train"]], arr[side]["y"][m["val"]]
                    if shuffle_labels:
                        y_tr, y_v = rng_shuffle.permutation(y_tr), rng_shuffle.permutation(y_v)
                    data = {
                        "X_train": X[m["train"]],
                        "y_train": y_tr,
                        "w_train": weights[side][m["train"]],
                        "X_val": X[m["val"]],
                        "y_val": y_v,
                        "w_val": weights[side][m["val"]],
                        "X_test": X[test],
                    }
                    params = tuned_params.get((rung, fold.index)) if tuned_params else None
                    p_val, p_test, info = fit_predict(kind, names, data, cfg, params)
                    v = m["val"]
                    thr[side] = select_threshold(
                        p_val,
                        decision[v],
                        arr[side]["exit"][v],
                        {s: arr[side]["net"][s][v] for s in SCENARIOS},
                        eligible[v],
                        cfg,
                    )
                    probs[side] = p_test
                    for scen in scenarios:
                        thresholds.append(
                            {
                                **tag,
                                "rung": rung,
                                "model": kind,
                                "side": side,
                                "scenario": scen,
                                "threshold": thr[side][scen],
                                "n_train": int(m["train"].sum()),
                                "n_val": int(v.sum()),
                                **info,
                            }
                        )
                for scen in scenarios:
                    sig = {
                        side: (probs[side] >= thr[side][scen]) & eligible[test]
                        if thr[side][scen] is not None
                        else np.zeros(test.sum(), bool)
                        for side in SIDES
                    }
                    idx, sd = simulate(
                        dt, sig["long"], sig["short"], arr["long"]["exit"][test], arr["short"]["exit"][test]
                    )
                    strat = (
                        f"{kind}_shuffled" if shuffle_labels else f"{kind}_tuned" if tuned_params else kind
                    )
                    add(
                        trades_frame(np.flatnonzero(test)[idx], sd, decision, arr, scen),
                        strategy=strat,
                        rung=rung,
                        model=kind,
                        scenario=scen,
                        seed=-1,
                        **tag,
                    )
                    # random entry at the model's per-side signal frequency
                    q = {
                        side: sig[side][eligible[test]].mean() if eligible[test].any() else 0.0
                        for side in SIDES
                    }
                    for seed in range(cfg.random_seeds):
                        r = np.random.default_rng([fold.index, rung, seed, MODELS.index(kind)])
                        rs = {side: (r.random(test.sum()) < q[side]) & eligible[test] for side in SIDES}
                        idx, sd = simulate(
                            dt, rs["long"], rs["short"], arr["long"]["exit"][test], arr["short"]["exit"][test]
                        )
                        add(
                            trades_frame(np.flatnonzero(test)[idx], sd, decision, arr, scen),
                            strategy=f"random_vs_{strat}",
                            rung=rung,
                            model=kind,
                            scenario=scen,
                            seed=seed,
                            **tag,
                        )
                    # volatility filter: random direction in high-ATR hours, at the model's signal frequency;
                    # the ATR cutoff comes from the validation slice
                    qtot = q["long"] + q["short"]
                    if qtot > 0:
                        v_ok = masks["long"]["val"] & eligible & np.isfinite(atr)
                        cut = np.quantile(atr[v_ok], max(0.0, 1 - qtot)) if v_ok.any() else np.inf
                        hot = (np.nan_to_num(atr[test], nan=-np.inf) >= cut) & eligible[test]
                        for seed in range(cfg.random_seeds):
                            r = np.random.default_rng([fold.index, rung, seed, MODELS.index(kind), 7])
                            up = r.random(test.sum()) < q["long"] / qtot
                            idx, sd = simulate(
                                dt, hot & up, hot & ~up, arr["long"]["exit"][test], arr["short"]["exit"][test]
                            )
                            add(
                                trades_frame(np.flatnonzero(test)[idx], sd, decision, arr, scen),
                                strategy=f"volfilter_vs_{strat}",
                                rung=rung,
                                model=kind,
                                scenario=scen,
                                seed=seed,
                                **tag,
                            )
    return pl.concat(trades, how="diagonal_relaxed"), pl.DataFrame(thresholds)


# ---------- metrics and report ----------

KEYS = ["label_set", "scenario", "strategy", "rung", "model"]


def summarize(trades: pl.DataFrame, cfg: EvalConfig) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(per-fold metrics, overall metrics) for every strategy; random baselines per seed."""
    folds = {f.index: f for f in make_folds(cfg)}
    per_fold, overall = [], []
    start, end = min(f.test_start for f in folds.values()), to_ms(cfg.holdout_start)
    for key, g in trades.group_by(KEYS + ["seed"], maintain_order=True):
        k = dict(zip(KEYS + ["seed"], key, strict=True))
        ci = k["seed"] == -1  # random-baseline seeds are summarized as a distribution, no per-seed CI
        for f in folds.values():
            gf = g.filter(pl.col("fold") == f.index)
            per_fold.append(
                {
                    **k,
                    "fold": f.index,
                    **metrics(
                        gf,
                        f.test_start,
                        f.test_end,
                        cfg.risk_per_trade,
                        cfg.bootstrap_block,
                        cfg.bootstrap_samples,
                        ci,
                    ),
                }
            )
        overall.append(
            {
                **k,
                **metrics(g, start, end, cfg.risk_per_trade, cfg.bootstrap_block, cfg.bootstrap_samples, ci),
            }
        )
    return pl.DataFrame(per_fold), pl.DataFrame(overall)


def log_mlflow(
    per_fold: pl.DataFrame, overall: pl.DataFrame, thresholds: pl.DataFrame, cfg: EvalConfig
) -> None:
    import mlflow

    Path("mlruns").mkdir(exist_ok=True)
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment("mbpe-phase3-step1")
    for row in overall.filter(pl.col("seed") == -1).iter_rows(named=True):
        k = {c: row[c] for c in KEYS}
        with mlflow.start_run(run_name="-".join(str(v) for v in k.values())):
            mlflow.log_params(
                {**k, "lgbm": cfg.lgbm if k["model"] == "lgbm" else None, "holdout_start": cfg.holdout_start}
            )
            mlflow.log_metrics(
                {
                    f"overall_{m}": float(row[m])
                    for m in ("trades", "mean_R", "return_per_year", "hit_rate", "sharpe", "max_drawdown")
                    if row[m] is not None and np.isfinite(row[m])
                }
            )
            pf = per_fold.filter(*[pl.col(c) == v for c, v in k.items()], pl.col("seed") == -1)
            for r in pf.iter_rows(named=True):
                for m in ("trades", "mean_R"):
                    if r[m] is not None and np.isfinite(r[m]):
                        mlflow.log_metric(f"fold_{m}", float(r[m]), step=r["fold"])


def main() -> None:
    import sys

    cfg = load_eval_config()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    all_trades, all_thr = [], []
    for ls in LABEL_SETS:
        tr, th = run_label_set(ls, cfg)
        all_trades.append(tr)
        all_thr.append(th)
        print(f"label set {ls}: {tr.height:,} trade rows, {time.perf_counter() - t0:.0f}s", flush=True)
    if "--no-sanity" not in sys.argv:  # shuffled-label sanity run: LightGBM, rung 3, label set A
        tr, th = run_label_set("A", cfg, models=("lgbm",), rungs={3: RUNGS[3]}, shuffle_labels=True)
        all_trades.append(tr.filter(~pl.col("strategy").str.starts_with("always")))
        all_thr.append(th.with_columns(model=pl.lit("lgbm_shuffled")))
    trades, thresholds = (
        pl.concat(all_trades, how="diagonal_relaxed"),
        pl.concat(all_thr, how="diagonal_relaxed"),
    )
    per_fold, overall = summarize(trades, cfg)
    per_fold.write_csv(REPORT_DIR / "fold_metrics.csv")
    overall.write_csv(REPORT_DIR / "overall_metrics.csv")
    thresholds.write_csv(REPORT_DIR / "thresholds.csv")
    trades.write_parquet(Path("data/features") / "phase3_step1_trades.parquet")
    log_mlflow(per_fold, overall, thresholds, cfg)
    print(f"done in {time.perf_counter() - t0:.0f}s -> {REPORT_DIR}")


if __name__ == "__main__":
    main()


# ---------- markdown report ----------


def _fmt(x, nd=3, pct=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "–" if x is None or np.isnan(x) else ("∞" if x > 0 else "−∞")
    return f"{x:.1%}" if pct else f"{x:+.{nd}f}" if nd else f"{x:,.0f}"


def _strategy_rows(ov: pl.DataFrame) -> list[tuple[str, int]]:
    """Strategies present, in a fixed order: baselines, models by rung, tuned, sanity."""
    order = ["always_long", "always_short", "logreg", "lgbm", "lgbm_tuned", "lgbm_shuffled"]
    have = set(ov.filter(pl.col("seed") == -1).select("strategy", "rung").iter_rows())
    return [(s, r) for s in order for r in (0, 1, 2, 3) if (s, r) in have]


def _band(x: pl.Series, nd: int = 3) -> str:
    if not x.len():
        return "–"
    return f"{_fmt(x.median(), nd)} [{_fmt(x.quantile(0.05), nd)}, {_fmt(x.quantile(0.95), nd)}]"


def markdown_report(per_fold: pl.DataFrame, overall: pl.DataFrame) -> str:
    """Per label set x scenario: performance (R and bps), baseline comparison, per-fold results."""
    out = []
    for ls in LABEL_SETS:
        for scen in SCENARIOS:
            ov = overall.filter((pl.col("label_set") == ls) & (pl.col("scenario") == scen))
            pf = per_fold.filter((pl.col("label_set") == ls) & (pl.col("scenario") == scen))
            if not ov.height:
                continue
            rows = _strategy_rows(ov)
            models = [(s, r) for s, r in rows if r]

            def one(df, s, r):
                x = df.filter((pl.col("strategy") == s) & (pl.col("rung") == r) & (pl.col("seed") == -1))
                return x.row(0, named=True) if x.height else None

            out += [
                f"#### Label set {ls}, cost scenario `{scen}`",
                "",
                "| strategy | rung | trades | mean net R [95% CI] | net bps | gross bps | return/yr | hit "
                "| PF "
                "| Sharpe | max DD |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for s, r in rows:
                x = one(ov, s, r)
                out.append(
                    f"| {s} | {r or '–'} | {x['trades']:,} | {_fmt(x['mean_R'])} [{_fmt(x['ci_lo'])}, "
                    f"{_fmt(x['ci_hi'])}] | {_fmt(x['bps_per_trade'], 1)} "
                    f"| {_fmt(x['gross_bps_per_trade'], 1)} "
                    f"| {_fmt(x['return_per_year'], pct=True)} | {_fmt(x['hit_rate'], pct=True)} "
                    f"| {_fmt(x['profit_factor'], 2)} | {_fmt(x['sharpe'], 2)} "
                    f"| {_fmt(x['max_drawdown'], pct=True)} |"
                )
            out += [
                "",
                "Baselines at each model's trade frequency (20 seeds; median [5%, 95%]). Folds won = folds "
                "where the model's mean net R beats the baseline's median (no model trades = lost).",
                "",
                "| model | rung | random: net R | random: net bps | random: gross bps | vol filter: net R "
                "| vol filter: net bps | vol filter: gross bps | folds won vs random | vs vol filter |",
                "|---|---|---|---|---|---|---|---|---|---|",
            ]
            for s, r in models:
                rnd = ov.filter((pl.col("strategy") == f"random_vs_{s}") & (pl.col("rung") == r))
                vol = ov.filter((pl.col("strategy") == f"volfilter_vs_{s}") & (pl.col("rung") == r))
                mf = pf.filter((pl.col("strategy") == s) & (pl.col("rung") == r) & (pl.col("seed") == -1))
                won = []
                for b in ("random", "volfilter"):
                    bf = (
                        pf.filter((pl.col("strategy") == f"{b}_vs_{s}") & (pl.col("rung") == r))
                        .group_by("fold")
                        .agg(med=pl.col("mean_R").median())
                    )
                    j = mf.join(bf, on="fold", how="left").filter(pl.col("trades") > 0)
                    won.append(f"{(j['mean_R'] > j['med']).sum()}/{mf.height}" if bf.height else "–")
                out.append(
                    f"| {s} | {r} | {_band(rnd['mean_R'])} | {_band(rnd['bps_per_trade'], 1)} "
                    f"| {_band(rnd['gross_bps_per_trade'], 1)} | {_band(vol['mean_R'])} "
                    f"| {_band(vol['bps_per_trade'], 1)} | {_band(vol['gross_bps_per_trade'], 1)} "
                    f"| {won[0]} | {won[1]} |"
                )
            # per fold: mean net R (trades), then net-bps advantage over the baselines
            out += [
                "",
                "Per fold, mean net R (trades); `·` = no trades.",
                "",
                "| fold | start | " + " | ".join(f"{s} r{r}" if r else s for s, r in rows) + " |",
                "|---|---|" + "---|" * len(rows),
            ]
            folds = make_folds(load_eval_config())
            for f in folds:
                cells = []
                for s, r in rows:
                    x = pf.filter(
                        (pl.col("strategy") == s)
                        & (pl.col("rung") == r)
                        & (pl.col("seed") == -1)
                        & (pl.col("fold") == f.index)
                    )
                    x = x.row(0, named=True) if x.height else None
                    cells.append(
                        "·" if not x or not x["trades"] else f"{_fmt(x['mean_R'], 2)} ({x['trades']})"
                    )
                start = np.datetime64(f.test_start, "ms").astype("datetime64[D]")
                out.append(f"| {f.index} | {start} | " + " | ".join(cells) + " |")
            out += [
                "",
                "Per fold, model net bps per trade minus the baseline median (random / vol filter); "
                "`·` = no model trades.",
                "",
                "| fold | " + " | ".join(f"{s} r{r}" for s, r in models) + " |",
                "|---|" + "---|" * len(models),
            ]
            for f in folds:
                cells = []
                for s, r in models:
                    x = pf.filter(
                        (pl.col("strategy") == s)
                        & (pl.col("rung") == r)
                        & (pl.col("seed") == -1)
                        & (pl.col("fold") == f.index)
                    )
                    x = x.row(0, named=True) if x.height else None
                    if not x or not x["trades"]:
                        cells.append("·")
                        continue
                    adv = []
                    for b in ("random", "volfilter"):
                        bb = pf.filter(
                            (pl.col("strategy") == f"{b}_vs_{s}")
                            & (pl.col("rung") == r)
                            & (pl.col("fold") == f.index)
                        )["bps_per_trade"]
                        adv.append(
                            _fmt(x["bps_per_trade"] - bb.median(), 1) if bb.drop_nulls().len() else "–"
                        )
                    cells.append(" / ".join(adv))
                out.append(f"| {f.index} | " + " | ".join(cells) + " |")
            out.append("")
    return "\n".join(out)
