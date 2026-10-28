"""Phase 6 Workstream B: three-class primary model, decision rule, meta-labeling, TreeSHAP."""

from datetime import UTC, datetime

import lightgbm as lgb
import numpy as np
import polars as pl
import pytest

from mbpe.validation import phase6 as p6
from mbpe.validation.harness import PHASE6_RUNG, rung_features
from mbpe.validation.walk_forward import Fold

HOUR = 3_600_000


def test_calibrated_probabilities_are_a_distribution():
    rng = np.random.default_rng(0)
    raw_v = rng.dirichlet(np.ones(3), 400)
    y_v = rng.integers(0, 3, 400)
    raw_t = np.vstack([rng.dirichlet(np.ones(3), 50), [[1.0, 0.0, 0.0]]])
    pv, pt = p6.calibrate_multiclass(raw_v, y_v, np.ones(400), raw_t)
    for p in (pv, pt):
        assert ((p >= 0) & (p <= 1)).all() and np.allclose(p.sum(axis=1), 1)


def test_decision_rule():
    means = p6.class_means(np.array([0, 0, 1, 2, 2]), np.array([-1.1, -0.9, -0.1, 1.9, 2.1]))
    assert means == pytest.approx([-1.0, -0.1, 2.0])
    p_long = np.array([[0.2, 0.5, 0.3], [0.6, 0.2, 0.2], [0.3, 0.4, 0.3], [0.1, 0.4, 0.5]])
    p_short = np.array([[0.5, 0.4, 0.1], [0.2, 0.3, 0.5], [0.3, 0.4, 0.3], [0.1, 0.4, 0.5]])
    e_l, e_s = p6.expected_r(p_long, means), p6.expected_r(p_short, means)
    assert e_l[0] == pytest.approx(-0.2 - 0.05 + 0.6)
    side = p6.choose_side(e_l, e_s, np.array([True, True, True, False]))
    # row 0: long > 0; row 1: short better; row 2: tie -> none; row 3: blacked out
    assert side.tolist() == [1, -1, 0, 0]
    assert p6.choose_side(np.array([-0.1]), np.array([-0.2]), np.array([True])).tolist() == [0]  # E <= 0


def test_meta_trains_only_on_earlier_folds_with_finished_trades():
    cand = pl.DataFrame({"fold": [0, 1, 2, 2, 3], "exit_time": [10, 20, 30, 41, 50]})
    m = p6.meta_train_mask(cand, Fold(3, 40, 60))
    assert m.tolist() == [
        True,
        True,
        True,
        False,
        False,
    ]  # fold 2's trade exiting after fold 3 starts: purged


@pytest.mark.parametrize("kind", ["lgbm", "catboost"])
def test_tree_shap_ranks_the_informative_feature(kind):
    rng = np.random.default_rng(1)
    X = rng.normal(size=(1500, 4))
    y = np.digitize(X[:, 0], [-0.5, 0.5])  # class from feature 0 only
    cfg = p6.cfg_p6()
    d = {"X_train": X[:1000], "y_train": y[:1000], "w_train": np.ones(1000), "X_val": X[1000:1300],
         "y_val": y[1000:1300], "w_val": np.ones(300), "X_test": X[1300:]}  # fmt: skip
    _, p_test, model, _ = p6.fit_primary(kind, ["a", "b", "c", "d"], d, cfg)
    assert (p_test.argmax(axis=1) == y[1300:]).mean() > 0.8
    s = p6.tree_shap(kind, model, X[1300:], 4)
    assert s.argmax() == 0 and s[0] > 5 * s[1:].max()


def test_lgbm_contributions_add_up_to_the_raw_score():
    rng = np.random.default_rng(2)
    X, y = rng.normal(size=(500, 3)), rng.integers(0, 3, 500)
    b = lgb.train({"objective": "multiclass", "num_class": 3, "verbose": -1}, lgb.Dataset(X, y), 20)
    c = b.predict(X[:5], pred_contrib=True).reshape(5, 3, 4)
    assert np.allclose(c.sum(axis=2), b.predict(X[:5], raw_score=True))


def synthetic_dataset(seed: int = 0) -> pl.DataFrame:
    """load_dataset layout over 2021-01 -> 2021-11, hourly, with a planted signal in ret_1h."""
    rng = np.random.default_rng(seed)
    start, end = (int(datetime(2021, m, 1, tzinfo=UTC).timestamp() * 1000) for m in (1, 11))
    t = np.arange(start, end, HOUR, dtype=np.int64)
    n = len(t)
    names = rung_features(PHASE6_RUNG)
    feats = {c: rng.normal(size=n) for c in names}
    feats |= {c: rng.integers(0, 4, n).astype(float) for c in names if c.startswith("oi_price_quadrant")}
    feats["atr_1h_pct"] = np.abs(feats["atr_1h_pct"]) * 0.01 + 0.002
    sig = feats["ret_1h"]
    cols = {"decision_time": t, **feats}
    for side, sgn in (("long", 1), ("short", -1)):
        u = rng.uniform(size=n)
        win = (sgn * sig > 0.8) & (u < 0.85) | (u < 0.05)
        lose = ~win & (u < 0.6)
        reason = np.where(win, "tp", np.where(lose, "sl", "expiry"))
        net = np.where(win, 1.9, np.where(lose, -1.1, rng.normal(-0.1, 0.3, n)))
        cols |= {
            f"{side}_label": net > 0,
            f"{side}_exit_time": t + 4 * HOUR,
            f"{side}_exit_reason": reason,
            f"{side}_gross_R": net + 0.1,
            f"{side}_net_R_taker": net,
            f"{side}_net_R_maker_entry_tp": net + 0.05,
            f"{side}_bps_per_R": np.full(n, 60.0),
        }
    return pl.DataFrame(cols).with_columns(
        pl.col("decision_time", "long_exit_time", "short_exit_time").cast(pl.Datetime("ms", "UTC"))
    )


def test_end_to_end_on_a_planted_signal():
    cfg = p6.cfg_p6().model_copy(
        update={
            "eval_start": datetime(2021, 1, 1, tzinfo=UTC),
            "first_test_fold": datetime(2021, 3, 1, tzinfo=UTC),
            "holdout_start": datetime(2021, 11, 1, tzinfo=UTC),
            "fold_months": 1,
        }
    )
    df = synthetic_dataset()
    res = p6.run("lgbm", cfg=cfg, df=df)
    cand, tr, info = res["candidates"], res["trades"], res["info"]
    # the planted signal is found: candidates trade the right side and make money net
    assert (
        cand.height > 100
        and (cand["side"] == np.sign(df["ret_1h"].to_numpy()[cand["row"].to_numpy()])).mean() > 0.9
    )
    rows = cand["row"].to_numpy()
    long_r, short_r = df["long_exit_reason"].to_numpy()[rows], df["short_exit_reason"].to_numpy()[rows]
    truth = np.where(cand["side"].to_numpy() == 1, long_r, short_r)
    assert [p6.CLASSES[c] for c in cand["exit_class"]] == truth.tolist()  # true outcome of the chosen side
    prim = tr.filter((pl.col("strategy") == "primary") & (pl.col("scenario") == "taker"))
    assert prim["net_R"].mean() > 0.5
    # meta-labeling: only from fold META_FIRST_FOLD, trained on earlier folds' out-of-sample candidates only
    assert cand.filter(pl.col("fold") < p6.META_FIRST_FOLD)["meta_p"].is_null().all()
    assert cand.filter(pl.col("fold") >= p6.META_FIRST_FOLD)["meta_p"].is_not_null().all()
    meta = info.filter(pl.col("side") == "meta")
    for r in meta.iter_rows(named=True):
        earlier = cand.filter(pl.col("fold") < r["fold"]).height
        assert 0 < r["meta_train"] + r["meta_val"] <= earlier
    assert set(tr["strategy"].unique()) == {"primary", "primary_meta060", "primary_metaval"}
    assert tr.filter(pl.col("strategy") != "primary")["fold"].min() >= p6.META_FIRST_FOLD
    # SHAP: the planted feature dominates the primary models
    shap = res["shap"].filter(pl.col("model") == "primary_long").drop("fold", "model")
    assert shap.mean().transpose(include_header=True).sort("column_0")["column"][-1] == "ret_1h"
    # shuffled-label control: the signal disappears
    sh = p6.run("lgbm", cfg=cfg, df=df, shuffle_seed=0)["trades"]
    shp = sh.filter((pl.col("strategy") == "primary") & (pl.col("scenario") == "taker"))
    assert shp.height == 0 or shp["net_R"].mean() < prim["net_R"].mean() - 0.5
