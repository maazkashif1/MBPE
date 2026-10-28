"""Tests for the walk-forward splitter, weights, holdout guard, simulator and harness."""

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from mbpe.validation.backtest import blackout, simulate
from mbpe.validation.harness import SIDES, run_label_set, rung_features, summarize
from mbpe.validation.walk_forward import (
    HoldoutError,
    guard_holdout,
    load_eval_config,
    make_folds,
    split,
    to_ms,
    uniqueness,
)

CFG = load_eval_config()
HOUR, MIN = 3_600_000, 60_000


def hourly(start: datetime, end: datetime, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Hourly decision times with label windows of 2-241 minutes (like the 4 h triple barrier)."""
    d = np.arange(to_ms(start), to_ms(end), HOUR, dtype=np.int64)
    return d, d + np.random.default_rng(seed).integers(2, 242, len(d)) * MIN


def test_folds_cover_quarters_up_to_the_holdout():
    folds = make_folds(CFG)
    assert folds[0].test_start == to_ms(datetime(2022, 1, 1, tzinfo=UTC))
    assert all(a.test_end == b.test_start for a, b in zip(folds, folds[1:], strict=False))
    assert folds[-1].test_end == to_ms(CFG.holdout_start)
    assert folds[1].test_start == to_ms(datetime(2022, 4, 1, tzinfo=UTC))


def test_no_training_or_validation_label_window_overlaps_its_test_fold_or_embargo():
    d, e = hourly(datetime(2021, 1, 1, tzinfo=UTC), datetime(2026, 4, 26, tzinfo=UTC))
    for f in make_folds(CFG):
        m = split(d, e, f, CFG)
        for part in ("train", "val"):
            dd, ee = d[m[part]], e[m[part]]
            assert (ee < f.test_start).all(), (f.index, part)  # window ends before the fold
            assert not ((dd >= f.test_end) & (dd < f.test_end + CFG.embargo_hours * HOUR)).any()  # embargo
            assert not ((dd < f.test_end) & (ee >= f.test_start)).any()
        val_start = d[m["val"]].min()
        assert (e[m["train"]] < val_start).all()  # train purged against validation
        assert not (m["train"] & m["val"]).any() and not ((m["train"] | m["val"]) & m["test"]).any()
        assert m["val"].sum() > 0.15 * (m["train"].sum() + m["val"].sum())


def test_uniqueness_on_hand_built_example():
    # windows in minutes: A [0,4), B [2,6), C [0,2)
    w = uniqueness(np.array([0, 2, 0]) * MIN, np.array([4, 6, 2]) * MIN)
    # minute: 0,1 -> A,C (1/2 each); 2,3 -> A,B (1/2); 4,5 -> B alone (1)
    assert w == pytest.approx([0.5, 0.75, 0.5])
    assert uniqueness(np.array([0]) * MIN, np.array([10]) * MIN) == pytest.approx([1.0])


def test_holdout_guard(tmp_path):
    h = CFG.holdout_start
    df = pl.DataFrame(
        {"decision_time": [h - timedelta(hours=1), h], "long_exit_time": [h - timedelta(minutes=5), h]}
    )
    with pytest.raises(HoldoutError):
        guard_holdout(df, CFG, log=tmp_path / "log.jsonl")
    leaky_exit = pl.DataFrame(
        {"decision_time": [h - timedelta(hours=1)], "short_exit_time": [h + timedelta(hours=1)]}
    )
    with pytest.raises(HoldoutError):  # an outcome reaching into the holdout counts too
        guard_holdout(leaky_exit, CFG, log=tmp_path / "log.jsonl")
    guard_holdout(df, CFG, allow_holdout=True, reason="test", log=tmp_path / "log.jsonl")
    entry = json.loads((tmp_path / "log.jsonl").read_text().splitlines()[-1])
    assert entry["rows"] == 1 and entry["reason"] == "test"
    guard_holdout(df.head(1), CFG, log=tmp_path / "log.jsonl")  # pre-holdout rows pass silently


def test_position_rule_never_allows_two_open_trades():
    rng = np.random.default_rng(0)
    d, e_long = hourly(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC), seed=1)
    _, e_short = hourly(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC), seed=2)
    for p in (0.05, 0.3, 0.9):
        sl, ss = rng.random(len(d)) < p, rng.random(len(d)) < p
        idx, side = simulate(d, sl, ss, e_long, e_short)
        exits = np.where(side == 1, e_long[idx], e_short[idx])
        assert (d[idx][1:] >= exits[:-1]).all()  # each entry after the previous exit: never two open
        assert not (sl[idx] & ss[idx]).any()  # both signalled -> not taken
        assert ((side == 1) == sl[idx]).all()
        # greedy: every skipped single-side signal falls inside an open trade
        taken = set(idx.tolist())
        for i in np.flatnonzero(sl ^ ss):
            if i not in taken:
                k = np.searchsorted(d[idx], d[i], "right") - 1
                assert k >= 0 and d[i] < exits[k]


def test_blackout_window():
    ev = np.array([to_ms(datetime(2024, 1, 10, 13, 30, tzinfo=UTC))])
    t = lambda h, m=0: to_ms(datetime(2024, 1, 10, h, m, tzinfo=UTC))  # noqa: E731
    blocked = blackout(np.array([t(9), t(9, 30), t(13), t(14), t(15)]), ev, 30, 240, 1)
    # entry = t + 1 min; blocked if 13:30 is in [entry - 30 min, entry + 4 h]
    assert blocked.tolist() == [False, True, True, False, False]


# ---------- harness on synthetic data ----------


def synthetic_dataset(signal: float, seed: int = 0) -> pl.DataFrame:
    """load_dataset layout: hourly rows Jan 2021 - Jul 2022, rung-1 features; feature 0 predicts R."""
    d, e_long = hourly(datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 7, 1, tzinfo=UTC), seed=seed)
    _, e_short = hourly(datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 7, 1, tzinfo=UTC), seed=seed + 1)
    rng = np.random.default_rng(seed)
    names = rung_features(1)
    X = rng.normal(size=(len(d), len(names)))
    cols = {"decision_time": pl.Series(d).cast(pl.Datetime("ms", "UTC"))}
    for side, sgn, e in (("long", 1, e_long), ("short", -1, e_short)):
        r = sgn * signal * X[:, 0] + rng.normal(0, 1, len(d)) - 0.2
        cols |= {
            f"{side}_label": r > 0,
            f"{side}_exit_time": pl.Series(e).cast(pl.Datetime("ms", "UTC")),
            f"{side}_gross_R": r + 0.2,
            f"{side}_net_R_taker": r,
            f"{side}_net_R_maker_entry_tp": r + 0.1,
            f"{side}_bps_per_R": np.full(len(d), 70.0),
        }
    return pl.DataFrame(cols).hstack(pl.DataFrame(X, schema=names))


def test_threshold_unchanged_when_test_fold_data_is_altered():
    df = synthetic_dataset(signal=0.5)
    folds = make_folds(CFG)[:2]
    _, th = run_label_set("A", CFG, models=("logreg", "lgbm"), rungs={1: ("technical",)}, folds=folds, df=df)
    # alter only the last fold's test period (earlier test periods are later folds' training data)
    in_test = pl.col("decision_time") >= datetime(2022, 4, 1, tzinfo=UTC)
    names = rung_features(1)
    noise = np.random.default_rng(9).normal(size=(df.height, len(names)))
    altered = df.with_columns(
        *[
            pl.when(in_test).then(-pl.col(c)).otherwise(pl.col(c)).alias(c)
            for c in ("long_net_R_taker", "short_net_R_taker")
        ],
        *[
            pl.when(in_test).then(~pl.col(f"{s}_label")).otherwise(pl.col(f"{s}_label")).alias(f"{s}_label")
            for s in SIDES
        ],
        *[
            pl.when(in_test).then(pl.lit(noise[:, i])).otherwise(pl.col(n)).alias(n)
            for i, n in enumerate(names)
        ],
    )
    _, th2 = run_label_set(
        "A", CFG, models=("logreg", "lgbm"), rungs={1: ("technical",)}, folds=folds, df=altered
    )
    assert th.select("fold", "model", "side", "scenario", "threshold").equals(
        th2.select("fold", "model", "side", "scenario", "threshold")
    )


def test_shuffled_labels_show_no_edge_but_real_labels_do():
    """Harness sanity: with a real signal the model beats always-trade; with shuffled labels it doesn't."""
    df = synthetic_dataset(signal=0.6, seed=3)
    folds = make_folds(CFG)[:2]
    res = {}
    for shuffled in (False, True):
        tr, _ = run_label_set(
            "A",
            CFG,
            models=("logreg",),
            rungs={1: ("technical",)},
            folds=folds,
            df=df,
            shuffle_labels=shuffled,
        )
        _, ov = summarize(tr.filter(pl.col("scenario") == "taker"), CFG)
        res[shuffled] = {r["strategy"]: r for r in ov.filter(pl.col("seed") == -1).iter_rows(named=True)}
    always = (res[False]["always_long"]["mean_R"] + res[False]["always_short"]["mean_R"]) / 2
    real = res[False]["logreg"]
    assert real["trades"] > 100 and real["ci_lo"] > always  # the harness finds a real signal
    fake = res[True].get("logreg_shuffled")
    if fake is not None and fake["trades"]:
        assert fake["ci_lo"] <= always + 0.05  # no edge beyond always-trade from shuffled labels
        assert fake["mean_R"] < real["ci_lo"]


def test_tuning_never_uses_the_test_fold():
    from mbpe.validation.tuning import tune

    df = synthetic_dataset(signal=0.5, seed=4)
    fold = make_folds(CFG)[1]  # 2022Q2: its test period is the last quarter of the synthetic data
    trials, best = tune(CFG, 1, n_trials=3, folds=[fold], df=df)
    in_test = pl.col("decision_time") >= datetime(2022, 4, 1, tzinfo=UTC)
    altered = df.with_columns(
        *[
            pl.when(in_test).then(-pl.col(c)).otherwise(pl.col(c)).alias(c)
            for c in ("long_net_R_taker", "short_net_R_taker")
        ],
        *[
            pl.when(in_test).then(~pl.col(f"{s}_label")).otherwise(pl.col(f"{s}_label")).alias(f"{s}_label")
            for s in SIDES
        ],
    )
    trials2, best2 = tune(CFG, 1, n_trials=3, folds=[fold], df=altered)
    assert best == best2 and trials["value"].to_list() == trials2["value"].to_list()


def test_volatility_baseline_trades_only_high_atr_hours():
    df = synthetic_dataset(signal=0.5, seed=5)
    folds = make_folds(CFG)[:2]
    tr, _ = run_label_set(
        "A", CFG, models=("logreg",), rungs={1: ("technical",)}, folds=folds, df=df, scenarios=("taker",)
    )
    vol = tr.filter(pl.col("strategy") == "volfilter_vs_logreg")
    assert vol.height > 0
    atr = df.select("decision_time", "atr_1h_pct")
    traded = vol.with_columns(pl.col("decision_time").cast(pl.Datetime("ms", "UTC"))).join(
        atr, on="decision_time"
    )
    # every traded hour's ATR is at least the cutoff, which sits well above the fold's median ATR
    for f in folds:
        t = traded.filter(pl.col("fold") == f.index)
        test_atr = df.filter(
            pl.col("decision_time").dt.epoch("ms").is_between(f.test_start, f.test_end, closed="left")
        )["atr_1h_pct"]
        if t.height:
            assert t["atr_1h_pct"].min() > test_atr.median()
    assert set(vol["seed"].unique().to_list()) == set(range(CFG.random_seeds))
