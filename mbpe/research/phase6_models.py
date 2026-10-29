import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression

from mbpe.research.backtest import simulate
from mbpe.research.experiment import (
    PHASE6_RUNG,
    SIDES,
    is_categorical,
    load_dataset,
    prepare_arrays,
    rung_features,
    threshold_stats,
)
from mbpe.research.walk_forward import load_eval_config, make_folds, split, uniqueness

CLASSES = ("sl", "expiry", "tp")
META_FIRST_FOLD = 4
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
    return load_eval_config().model_copy(
        update={"embargo_hours": 48, "blackout_events": ("CPI", "FOMC", "NFP")}
    )


def fit_calibrators(raw_val, y_val, w_val) -> list[IsotonicRegression]:
    return [
        IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(
            raw_val[:, c], (y_val == c).astype(float), sample_weight=w_val
        )
        for c in range(len(CLASSES))
    ]


def apply_calibrators(isos: list[IsotonicRegression], raw: np.ndarray) -> np.ndarray:
    if not len(raw):
        return np.zeros((0, len(CLASSES)))
    p = np.column_stack([iso.predict(raw[:, c]) for c, iso in enumerate(isos)])
    s = p.sum(axis=1, keepdims=True)
    return np.where(s > 0, p / np.where(s > 0, s, 1), 1 / len(CLASSES))


def calibrate_multiclass(raw_val, y_val, w_val, raw_test) -> tuple[np.ndarray, np.ndarray]:
    isos = fit_calibrators(raw_val, y_val, w_val)
    return apply_calibrators(isos, raw_val), apply_calibrators(isos, raw_test)


def fit_primary(kind: str, names: list[str], d: dict, cfg) -> tuple[np.ndarray, np.ndarray, object, dict]:
    if kind == "lgbm":
        cat = [i for i, n in enumerate(names) if is_categorical(n)]
        params = {**cfg.lgbm, "objective": "multiclass", "num_class": len(CLASSES)}
        dtr = lgb.Dataset(
            d["X_train"], d["y_train"], weight=d["w_train"], categorical_feature=cat, free_raw_data=False
        )
        dv = lgb.Dataset(d["X_val"], d["y_val"], weight=d["w_val"], reference=dtr, categorical_feature=cat)
        model = lgb.train(
            params,
            dtr,
            num_boost_round=cfg.lgbm_max_rounds,
            valid_sets=[dv],
            callbacks=[lgb.early_stopping(cfg.lgbm_early_stopping, verbose=False)],
        )
        it = model.best_iteration or model.current_iteration()
        raw_v, raw_t = (
            model.predict(d["X_val"], num_iteration=it),
            model.predict(d["X_test"], num_iteration=it),
        )
        info = {"best_iteration": it}
    else:
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
    isos = fit_calibrators(raw_v, d["y_val"], d["w_val"])
    info["calibrators"] = isos
    return apply_calibrators(isos, raw_v), apply_calibrators(isos, raw_t), model, info


def class_codes(df: pl.DataFrame, side: str) -> np.ndarray:
    return (
        df[f"{side}_exit_reason"]
        .replace_strict({c: i for i, c in enumerate(CLASSES)})
        .cast(pl.Int8)
        .to_numpy()
    )


def class_means(y: np.ndarray, net: np.ndarray) -> np.ndarray:
    return np.array([net[y == c].mean() if (y == c).any() else 0.0 for c in range(len(CLASSES))])


def expected_r(p: np.ndarray, means: np.ndarray) -> np.ndarray:
    return p @ means


def choose_side(e_long: np.ndarray, e_short: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    best = np.maximum(e_long, e_short)
    side = np.where(e_long > e_short, 1, np.where(e_short > e_long, -1, 0))
    return np.where((best > 0) & eligible, side, 0)


def tree_shap(kind: str, model, X: np.ndarray, n_features: int) -> np.ndarray:
    if not len(X):
        return np.full(n_features, np.nan)
    if kind == "lgbm" and isinstance(model, lgb.Booster):
        c = model.predict(X, pred_contrib=True, num_iteration=model.best_iteration or None)
        k = c.shape[1] // (n_features + 1)
        c = c.reshape(len(X), k, n_features + 1)
    else:
        from catboost import Pool

        c = model.get_feature_importance(Pool(X), type="ShapValues")
        c = c.reshape(len(X), -1, n_features + 1)
    return np.abs(c[:, :, :n_features]).mean(axis=(0, 1))


def meta_train_mask(cand: pl.DataFrame, fold) -> np.ndarray:
    return ((cand["fold"] < fold.index) & (cand["exit_time"] < fold.test_start)).to_numpy()


def fit_meta(train: pl.DataFrame, test: pl.DataFrame, cfg) -> tuple[np.ndarray, dict]:
    train = train.sort("decision_time")
    t = train["decision_time"].to_numpy()
    cut = int(t[0] + (1 - cfg.val_fraction) * (t[-1] - t[0]))
    val = t >= cut
    tr = (~val) & (train["exit_time"].to_numpy() < cut)
    X = train.select(META_INPUTS).cast(pl.Float64).to_numpy()
    y = (train["net_R_taker"] > 0).cast(pl.Int8).to_numpy()
    w = uniqueness(t, train["exit_time"].to_numpy())
    dtr = lgb.Dataset(X[tr], y[tr], weight=w[tr], free_raw_data=False)
    dv = lgb.Dataset(X[val], y[val], weight=w[val], reference=dtr)
    model = lgb.train(
        cfg.lgbm,
        dtr,
        num_boost_round=cfg.lgbm_max_rounds,
        valid_sets=[dv],
        callbacks=[lgb.early_stopping(cfg.lgbm_early_stopping, verbose=False)],
    )
    it = model.best_iteration or model.current_iteration()
    Xt = test.select(META_INPUTS).cast(pl.Float64).to_numpy()
    raw_v, raw_t = (
        model.predict(X[val], num_iteration=it),
        model.predict(Xt, num_iteration=it) if len(Xt) else np.zeros(0),
    )
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(raw_v, y[val], sample_weight=w[val])
    p_val, p_test = iso.predict(raw_v), iso.predict(raw_t) if len(raw_t) else np.zeros(0)
    vthr = threshold_stats(
        p_val,
        t[val],
        train["exit_time"].to_numpy()[val],
        {"taker": train["net_R_taker"].to_numpy()[val]},
        np.ones(val.sum(), bool),
        cfg,
    )["taker"][0]
    info = {
        "meta_train": int(tr.sum()),
        "meta_val": int(val.sum()),
        "meta_best_iteration": it,
        "val_threshold": vthr,
        "shap": tree_shap("lgbm", model, Xt, len(META_INPUTS)),
        "model": model,
        "calibrator": iso,
    }
    return p_test, info


def by_side(
    is_long: np.ndarray, rows: np.ndarray, long_vals: np.ndarray, short_vals: np.ndarray
) -> np.ndarray:
    return np.where(is_long, long_vals[rows], short_vals[rows])


def run(
    kind: str, cfg=None, df: pl.DataFrame | None = None, folds=None, shuffle_seed: int | None = None
) -> dict:
    cfg = cfg or cfg_p6()
    df = load_dataset("A", cfg) if df is None else df
    names = rung_features(PHASE6_RUNG)
    X = df.select(names).cast(pl.Float64).to_numpy()
    decision, arr, weights, eligible = prepare_arrays(df, cfg)
    cls = {s: class_codes(df, s) for s in SIDES}
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
                "X_train": X[m["train"]],
                "y_train": y[m["train"]],
                "w_train": weights[s][m["train"]],
                "X_val": X[m["val"]],
                "y_val": y[m["val"]],
                "w_val": weights[s][m["val"]],
                "X_test": X[test],
            }
            _, p_test, model, info = fit_primary(kind, names, d, cfg)
            info.pop("calibrators")
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
        rows = np.flatnonzero(test)[pick]
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
        info.pop("model"), info.pop("calibrator")
        meta_p[te] = p
        vthr[fold.index] = info.pop("val_threshold")
        shap_rows.append(
            {"fold": fold.index, "model": "meta", **dict(zip(META_INPUTS, info.pop("shap"), strict=True))}
        )
        info_rows.append({"fold": fold.index, "side": "meta", **info, "val_threshold": vthr[fold.index]})
    cand = cand.with_columns(meta_p=pl.Series(meta_p).fill_nan(None))
    return {
        "candidates": cand,
        "trades": trades(cand, vthr, make_folds(cfg) if folds is None else folds),
        "shap": pl.DataFrame(shap_rows, infer_schema_length=None),
        "info": pl.DataFrame(info_rows, infer_schema_length=None),
    }


def trades(cand: pl.DataFrame, vthr: dict, folds) -> pl.DataFrame:
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
                        "decision_time",
                        "side",
                        "exit_time",
                        "gross_R",
                        "bps_per_R",
                        "exit_class",
                        net_R=pl.col(f"net_R_{scen}"),
                    ).with_columns(strategy=pl.lit(strat), scenario=pl.lit(scen), fold=pl.lit(fold.index))
                )
    return pl.concat(out) if out else pl.DataFrame()
