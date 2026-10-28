"""Phase 6 Workstream B (SCOPE.md, "Phase 6: delivery of the full scope doc"): three-class primary model,
meta-labeling filter, TreeSHAP. No holdout.

Primary (per side): classes 0 = stop first (-1), 1 = expiry (0), 2 = take-profit first (+1) on label set A.
LightGBM (multiclass) or CatBoost (MultiClass), fixed settings, early stopping on the validation slice,
isotonic calibration per class (one-vs-rest) renormalized to sum 1.
Decision: per side, E[net R] = sum_c P(c) x mean taker net R of class c in that side's training slice; the
candidate is the side with the higher E if E > 0 (and the hour isn't blacked out), else no trade.
Meta-labeling (test fold k >= META_FIRST_FOLD): LightGBM binary on the OUT-OF-SAMPLE candidates of folds < k
(their label windows end before fold k starts); inputs = the proposed side's class probabilities, the side
and volatility features; target = candidate net R > 0 (taker). Last 20% of those candidates = its validation
slice (early stopping, isotonic calibration, validation-chosen threshold). Execute at P(success) >= 0.60
(headline) or at the validation-chosen threshold (reported alongside).
SHAP: exact TreeSHAP from the libraries themselves (LightGBM pred_contrib, CatBoost ShapValues), mean |value|
per feature on each test fold.
"""

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression

from mbpe.validation.backtest import simulate
from mbpe.validation.harness import (
    PHASE6_RUNG,
    SIDES,
    is_categorical,
    load_dataset,
    prepare_arrays,
    rung_features,
    threshold_stats,
)
from mbpe.validation.walk_forward import load_eval_config, make_folds, split, uniqueness

CLASSES = ("sl", "expiry", "tp")  # class index 0, 1, 2 (labels -1, 0, +1)
META_FIRST_FOLD = 4  # first test fold with >= 4 earlier folds of out-of-sample primary predictions
META_THRESHOLD = 0.60
META_VOL = ("atr_1h_pct", "rv_1h", "rv_4h", "rv_24h")
META_INPUTS = ("p_sl", "p_expiry", "p_tp", "side", *META_VOL)
CATBOOST = {
    "loss_function": "MultiClass",
    "iterations": 3000,
    "learning_rate": 0.03,
    "depth": 4,
    "l2_leaf_reg": 3.0,
    "random_seed": 7,
    "thread_count": 4,
    "od_type": "Iter",
    "od_wait": 100,
    "verbose": False,
    "allow_writing_files": False,
}


def cfg_p6():
    """Phase 6 protocol on the Phase 3 folds: 48 h embargo, CPI/FOMC/NFP blackout (SCOPE.md, Workstream C)."""
    return load_eval_config().model_copy(
        update={"embargo_hours": 48, "blackout_events": ("CPI", "FOMC", "NFP")}
    )


# ---------- primary model ----------


def calibrate_multiclass(raw_val, y_val, w_val, raw_test) -> tuple[np.ndarray, np.ndarray]:
    """Isotonic per class (one-vs-rest) on validation, rows renormalized to sum 1 (uniform if all zero)."""
    out = []
    for raw in (raw_val, raw_test):
        cols = []
        for c in range(len(CLASSES)):
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
            iso.fit(raw_val[:, c], (y_val == c).astype(float), sample_weight=w_val)
            cols.append(iso.predict(raw[:, c]) if len(raw) else np.zeros(0))
        p = np.column_stack(cols) if len(raw) else np.zeros((0, len(CLASSES)))
        s = p.sum(axis=1, keepdims=True)
        out.append(np.where(s > 0, p / np.where(s > 0, s, 1), 1 / len(CLASSES)))
    return out[0], out[1]


def fit_primary(kind: str, names: list[str], d: dict, cfg) -> tuple[np.ndarray, np.ndarray, object, dict]:
    """(calibrated P on validation, on test, fitted model, info). d: X/y/w for train and val, X for test."""
    if kind == "lgbm":
        cat = [i for i, n in enumerate(names) if is_categorical(n)]
        params = {**cfg.lgbm, "objective": "multiclass", "num_class": len(CLASSES)}
        dtr = lgb.Dataset(
            d["X_train"], d["y_train"], weight=d["w_train"], categorical_feature=cat, free_raw_data=False
        )
        dv = lgb.Dataset(d["X_val"], d["y_val"], weight=d["w_val"], reference=dtr, categorical_feature=cat)
        model = lgb.train(
            params, dtr, num_boost_round=cfg.lgbm_max_rounds, valid_sets=[dv],
            callbacks=[lgb.early_stopping(cfg.lgbm_early_stopping, verbose=False)],
        )  # fmt: skip
        it = model.best_iteration or model.current_iteration()
        raw_v, raw_t = (
            model.predict(d["X_val"], num_iteration=it),
            model.predict(d["X_test"], num_iteration=it),
        )
        info = {"best_iteration": it}
    else:  # catboost; the OI/price quadrant code is numeric here (CatBoost categoricals can't be NaN floats)
        from catboost import CatBoostClassifier, Pool

        model = CatBoostClassifier(**CATBOOST)
        model.fit(
            Pool(d["X_train"], d["y_train"], weight=d["w_train"]),
            eval_set=Pool(d["X_val"], d["y_val"], weight=d["w_val"]),
            use_best_model=True,
        )
        raw_v = model.predict_proba(d["X_val"])
        raw_t = model.predict_proba(d["X_test"]) if len(d["X_test"]) else np.zeros((0, len(CLASSES)))
        info = {"best_iteration": int(model.get_best_iteration() or 0)}
    p_val, p_test = calibrate_multiclass(raw_v, d["y_val"], d["w_val"], raw_t)
    return p_val, p_test, model, info


def class_means(y: np.ndarray, net: np.ndarray) -> np.ndarray:
    """Mean net R per class (training slice); 0 for a class absent from it."""
    return np.array([net[y == c].mean() if (y == c).any() else 0.0 for c in range(len(CLASSES))])


def expected_r(p: np.ndarray, means: np.ndarray) -> np.ndarray:
    return p @ means


def choose_side(e_long: np.ndarray, e_short: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """+1 long, -1 short, 0 no trade: the side with the higher expected net R, if > 0 (ties -> no trade)."""
    best = np.maximum(e_long, e_short)
    side = np.where(e_long > e_short, 1, np.where(e_short > e_long, -1, 0))
    return np.where((best > 0) & eligible, side, 0)


def tree_shap(kind: str, model, X: np.ndarray, n_features: int) -> np.ndarray:
    """Mean |SHAP| per feature over the rows of X (multiclass: averaged over classes; bias column dropped)."""
    if not len(X):
        return np.full(n_features, np.nan)
    if kind == "lgbm" and isinstance(model, lgb.Booster):
        c = model.predict(X, pred_contrib=True, num_iteration=model.best_iteration or None)
        k = c.shape[1] // (n_features + 1)  # 1 (binary) or the number of classes
        c = c.reshape(len(X), k, n_features + 1)
    else:
        from catboost import Pool

        c = model.get_feature_importance(Pool(X), type="ShapValues")
        c = c.reshape(len(X), -1, n_features + 1)
    return np.abs(c[:, :, :n_features]).mean(axis=(0, 1))


# ---------- meta-labeling ----------


def meta_train_mask(cand: pl.DataFrame, fold) -> np.ndarray:
    """Candidates usable to train the meta-model for test fold `fold`: from earlier folds, label window ended
    before the fold starts (purge)."""
    return ((cand["fold"] < fold.index) & (cand["exit_time"] < fold.test_start)).to_numpy()


def fit_meta(train: pl.DataFrame, test: pl.DataFrame, cfg) -> tuple[np.ndarray, dict]:
    """P(success) for the test candidates, plus the validation-chosen threshold (None if none qualifies)."""
    train = train.sort("decision_time")
    t = train["decision_time"].to_numpy()
    cut = int(t[0] + (1 - cfg.val_fraction) * (t[-1] - t[0]))
    val = t >= cut
    tr = (~val) & (
        train["exit_time"].to_numpy() < cut
    )  # purge training candidates whose trade reaches validation
    X = train.select(META_INPUTS).cast(pl.Float64).to_numpy()
    y = (train["net_R_taker"] > 0).cast(pl.Int8).to_numpy()
    w = uniqueness(t, train["exit_time"].to_numpy())
    dtr = lgb.Dataset(X[tr], y[tr], weight=w[tr], free_raw_data=False)
    dv = lgb.Dataset(X[val], y[val], weight=w[val], reference=dtr)
    model = lgb.train(
        cfg.lgbm, dtr, num_boost_round=cfg.lgbm_max_rounds, valid_sets=[dv],
        callbacks=[lgb.early_stopping(cfg.lgbm_early_stopping, verbose=False)],
    )  # fmt: skip
    it = model.best_iteration or model.current_iteration()
    Xt = test.select(META_INPUTS).cast(pl.Float64).to_numpy()
    raw_v, raw_t = (
        model.predict(X[val], num_iteration=it),
        model.predict(Xt, num_iteration=it) if len(Xt) else np.zeros(0),
    )
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(raw_v, y[val], sample_weight=w[val])
    p_val, p_test = iso.predict(raw_v), iso.predict(raw_t) if len(raw_t) else np.zeros(0)
    vthr = threshold_stats(
        p_val, t[val], train["exit_time"].to_numpy()[val], {"taker": train["net_R_taker"].to_numpy()[val]},
        np.ones(val.sum(), bool), cfg,
    )["taker"][0]  # fmt: skip
    info = {
        "meta_train": int(tr.sum()),
        "meta_val": int(val.sum()),
        "meta_best_iteration": it,
        "val_threshold": vthr,
        "shap": tree_shap("lgbm", model, Xt, len(META_INPUTS)),
    }
    return p_test, info


def by_side(
    is_long: np.ndarray, rows: np.ndarray, long_vals: np.ndarray, short_vals: np.ndarray
) -> np.ndarray:
    """Values of full-length per-side arrays at the candidate rows, taken from each candidate's side."""
    return np.where(is_long, long_vals[rows], short_vals[rows])


# ---------- one run over the walk-forward folds ----------


def run(
    kind: str, cfg=None, df: pl.DataFrame | None = None, folds=None, shuffle_seed: int | None = None
) -> dict:
    """Primary (+ meta) over the folds -> {"candidates", "trades", "shap", "info"} (polars frames).

    `shuffle_seed`: shuffled-label control. Training and validation rows' (class, net R) pairs are permuted
    jointly per side, so the class means follow the shuffled labels.
    """
    cfg = cfg or cfg_p6()
    df = load_dataset("A", cfg) if df is None else df
    names = rung_features(PHASE6_RUNG)
    X = df.select(names).cast(pl.Float64).to_numpy()
    decision, arr, weights, eligible = prepare_arrays(df, cfg)
    cls = {
        s: df[f"{s}_exit_reason"]
        .replace_strict({c: i for i, c in enumerate(CLASSES)})
        .cast(pl.Int8)
        .to_numpy()
        for s in SIDES
    }
    vol = df.select(META_VOL).cast(pl.Float64).to_numpy()
    rng = np.random.default_rng(shuffle_seed) if shuffle_seed is not None else None
    cands, shap_rows, info_rows = [], [], []
    for fold in folds or make_folds(cfg):
        masks = {s: split(decision, arr[s]["exit"], fold, cfg) for s in SIDES}
        test = masks["long"]["test"]
        e, probs = {}, {}
        for s in SIDES:
            m = masks[s]
            y, net = cls[s].copy(), arr[s]["net"]["taker"].copy()
            if rng is not None:
                for part in ("train", "val"):
                    idx = np.flatnonzero(m[part])
                    perm = rng.permutation(idx)
                    y[idx], net[idx] = y[perm], net[perm]
            d = {
                "X_train": X[m["train"]], "y_train": y[m["train"]], "w_train": weights[s][m["train"]],
                "X_val": X[m["val"]], "y_val": y[m["val"]], "w_val": weights[s][m["val"]], "X_test": X[test],
            }  # fmt: skip
            _, p_test, model, info = fit_primary(kind, names, d, cfg)
            means = class_means(y[m["train"]], net[m["train"]])
            probs[s], e[s] = p_test, expected_r(p_test, means)
            info_rows.append(
                {
                    "fold": fold.index,
                    "side": s,
                    **info,
                    **{f"mean_R_{c}": v for c, v in zip(CLASSES, means, strict=True)},
                }
            )
            shap_rows.append(
                {
                    "fold": fold.index,
                    "model": f"primary_{s}",
                    **dict(zip(names, tree_shap(kind, model, X[test], len(names)), strict=True)),
                }
            )
        side = choose_side(e["long"], e["short"], eligible[test])
        pick = side != 0
        rows = np.flatnonzero(test)[pick]  # dataset rows of this fold's candidates
        is_long = side[pick] == 1

        p_side = np.where(is_long[:, None], probs["long"][pick], probs["short"][pick])
        cand = pl.DataFrame(
            {
                "fold": np.full(len(rows), fold.index),
                "row": rows,
                "decision_time": decision[rows],
                "side": side[pick],
                "expected_R": np.where(is_long, e["long"][pick], e["short"][pick]),
                **{f"p_{c}": p_side[:, i] for i, c in enumerate(CLASSES)},
                **{v: vol[rows, i] for i, v in enumerate(META_VOL)},
                # outcomes are always the true (unshuffled) labels
                "exit_time": by_side(is_long, rows, arr["long"]["exit"], arr["short"]["exit"]),
                "net_R_taker": by_side(
                    is_long, rows, arr["long"]["net"]["taker"], arr["short"]["net"]["taker"]
                ),
                "net_R_maker_entry_tp": by_side(
                    is_long, rows, arr["long"]["net"]["maker_entry_tp"], arr["short"]["net"]["maker_entry_tp"]
                ),
                "gross_R": by_side(is_long, rows, arr["long"]["gross"], arr["short"]["gross"]),
                "bps_per_R": by_side(is_long, rows, arr["long"]["bps"], arr["short"]["bps"]),
                "exit_class": by_side(is_long, rows, cls["long"], cls["short"]),
            }
        )
        cands.append(cand)
    cand = pl.concat(cands)
    meta_p = np.full(cand.height, np.nan)
    vthr = {}
    for fold in folds or make_folds(cfg):
        if fold.index < META_FIRST_FOLD:
            continue
        tr = meta_train_mask(cand, fold)
        te = (cand["fold"] == fold.index).to_numpy()
        if tr.sum() < 2 or not te.any():
            continue
        p, info = fit_meta(cand.filter(tr), cand.filter(te), cfg)
        meta_p[te] = p
        vthr[fold.index] = info.pop("val_threshold")
        shap_rows.append(
            {"fold": fold.index, "model": "meta", **dict(zip(META_INPUTS, info.pop("shap"), strict=True))}
        )
        info_rows.append({"fold": fold.index, "side": "meta", **info, "val_threshold": vthr[fold.index]})
    cand = cand.with_columns(meta_p=pl.Series(meta_p).fill_nan(None))  # null = no meta prediction
    return {
        "candidates": cand,
        "trades": trades(cand, vthr, make_folds(cfg) if folds is None else folds),
        "shap": pl.DataFrame(shap_rows, infer_schema_length=None),
        "info": pl.DataFrame(info_rows, infer_schema_length=None),
    }


def trades(cand: pl.DataFrame, vthr: dict, folds) -> pl.DataFrame:
    """Position rule per fold for: primary only; primary + meta at 0.60; primary + meta at the
    validation-chosen threshold. Each candidate trades on its own side; net R per cost scenario."""
    out = []
    for fold in folds:
        c = cand.filter(pl.col("fold") == fold.index).sort("decision_time")
        filters = {"primary": np.ones(c.height, bool)}
        if fold.index >= META_FIRST_FOLD:
            mp = c["meta_p"].fill_null(np.nan).to_numpy()
            filters["primary_meta060"] = mp >= META_THRESHOLD
            t = vthr.get(fold.index)
            filters["primary_metaval"] = mp >= t if t is not None else np.zeros(c.height, bool)
        side = c["side"].to_numpy()
        for strat, keep in filters.items():
            longs, shorts = keep & (side == 1), keep & (side == -1)
            ex = c["exit_time"].to_numpy()
            idx, _ = simulate(c["decision_time"].to_numpy(), longs, shorts, ex, ex)
            t_ = c[idx]
            for scen in ("taker", "maker_entry_tp"):
                out.append(
                    t_.select(
                        "decision_time", "side", "exit_time", "gross_R", "bps_per_R", "exit_class",
                        net_R=pl.col(f"net_R_{scen}"),
                    ).with_columns(strategy=pl.lit(strat), scenario=pl.lit(scen), fold=pl.lit(fold.index))
                )  # fmt: skip
    return pl.concat(out) if out else pl.DataFrame()
