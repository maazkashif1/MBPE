"""Frozen model: deterministic version, predictions identical to the research code, tamper check."""

from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from mbpe.live import model as lmod
from mbpe.validation import phase6 as p6
from mbpe.validation.harness import PHASE6_RUNG, SIDES, prepare_arrays, rung_features
from mbpe.validation.walk_forward import Fold, split, to_ms
from tests.test_phase6 import synthetic_dataset


def cfg_small():
    return p6.cfg_p6().model_copy(
        update={
            "eval_start": datetime(2021, 1, 1, tzinfo=UTC),
            "first_test_fold": datetime(2021, 3, 1, tzinfo=UTC),
            "holdout_start": datetime(2021, 10, 1, tzinfo=UTC),
            "fold_months": 1,
        }
    )


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    cfg, df = cfg_small(), synthetic_dataset()
    root = tmp_path_factory.mktemp("models")
    a = lmod.freeze(root=root, cfg=cfg, df=df)
    b = lmod.freeze(root=tmp_path_factory.mktemp("models2"), cfg=cfg, df=df)
    return cfg, df, a, b


def test_freeze_is_deterministic_and_trained_before_the_holdout(frozen):
    cfg, _, a, b = frozen
    assert a.name == b.name  # same fitted parameters -> same version
    fz = lmod.load(a)
    h = to_ms(cfg.holdout_start)
    for s in SIDES:
        for part in ("train", "val"):
            assert fz.manifest["primary_ranges_ms"][s][part][1] < h
    assert fz.threshold == p6.META_THRESHOLD and fz.manifest["meta_candidates"] > 0


def test_predict_equals_the_research_code(frozen):
    cfg, df, a, _ = frozen
    fz = lmod.load(a)
    names = rung_features(PHASE6_RUNG)
    X = df.select(names).cast(pl.Float64).to_numpy()
    decision, arr, weights, _ = prepare_arrays(df, cfg)
    h = to_ms(cfg.holdout_start)
    rows = np.flatnonzero(decision >= h)[:50]
    fold = Fold(index=len(p6.make_folds(cfg)), test_start=h, test_end=h)
    for s in SIDES:
        m = split(decision, arr[s]["exit"], fold, cfg)
        y = p6.class_codes(df, s)
        d = {
            "X_train": X[m["train"]], "y_train": y[m["train"]], "w_train": weights[s][m["train"]],
            "X_val": X[m["val"]], "y_val": y[m["val"]], "w_val": weights[s][m["val"]], "X_test": X[rows],
        }  # fmt: skip
        _, p_ref, _, _ = p6.fit_primary("lgbm", names, d, cfg)
        for i, r in enumerate(rows):
            got = lmod.predict(fz, df[int(r)], eligible=True)["probs"][s]
            assert np.allclose([got[c] for c in p6.CLASSES], p_ref[i], atol=1e-12)


def test_tampered_artifact_is_refused(frozen, tmp_path):
    _, _, a, _ = frozen
    import shutil

    bad = tmp_path / "bad"
    shutil.copytree(a, bad)
    (bad / "model.pkl").write_bytes((bad / "model.pkl").read_bytes() + b"x")
    with pytest.raises(ValueError, match="does not match"):
        lmod.load(bad)
