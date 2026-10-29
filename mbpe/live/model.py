"""Frozen Phase 6 system (Workstreams D/E): train once, save, load, predict one decision time.

freeze(): the LightGBM three-class primary per side, trained exactly like a walk-forward fold whose test
period starts at the holdout start (2026-04-26): training = everything before, the same purge, last-20%
validation slice, weights and calibration; class means from the training slice. The meta-model is trained
on the out-of-sample candidates of the 18 walk-forward test folds (Phase 6 Workstream C procedure), all of
which exit before the holdout start; its validation-chosen threshold is stored next to the pre-registered
0.60. The artifact lives in data/models/phase6/<version>/ (git-ignored), with manifest.json: version =
sha256 of a canonical serialization of every fitted parameter, so retraining identically gives the same
version.

predict(): one feature row -> per-side calibrated class probabilities, expected net R, the candidate side
(blackout applied), the meta P(success) and the decision at the artifact's threshold.

Usage: uv run python -m mbpe.live.model freeze [--threshold 0.60 | --threshold val]
"""

import hashlib
import json
import pickle
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.validation import phase6 as p6
from mbpe.validation.harness import PHASE6_RUNG, SIDES, load_dataset, prepare_arrays, rung_features
from mbpe.validation.walk_forward import Fold, split, to_ms

MODEL_ROOT = Path("data/models/phase6")


@dataclass
class Frozen:
    version: str
    features: list[str]
    primary: dict  # side -> {"booster": lgb.Booster, "calibrators": [...], "class_means": np.ndarray}
    meta: dict  # {"booster", "calibrator"}
    threshold: float
    val_threshold: float | None
    manifest: dict


def _canonical(primary: dict, meta: dict) -> bytes:
    """Stable serialization of every fitted parameter (for the version hash)."""
    obj = {
        s: {
            "booster": v["booster"].model_to_string(),
            "calibrators": [
                [iso.X_thresholds_.tolist(), iso.y_thresholds_.tolist()] for iso in v["calibrators"]
            ],
            "class_means": np.asarray(v["class_means"]).tolist(),
        }
        for s, v in primary.items()
    }
    obj["meta"] = {
        "booster": meta["booster"].model_to_string(),
        "calibrator": [meta["calibrator"].X_thresholds_.tolist(), meta["calibrator"].y_thresholds_.tolist()],
    }
    return json.dumps(obj, sort_keys=True).encode()


def freeze(threshold: float | str = p6.META_THRESHOLD, root: Path = MODEL_ROOT, cfg=None, df=None) -> Path:
    """threshold: a P(success) cutoff, or "val" for the meta-model's validation-chosen threshold."""
    cfg = cfg or p6.cfg_p6()
    df = load_dataset("A", cfg) if df is None else df
    names = rung_features(PHASE6_RUNG)
    X = df.select(names).cast(pl.Float64).to_numpy()
    decision, arr, weights, _ = prepare_arrays(df, cfg)
    h = to_ms(cfg.holdout_start)
    final = Fold(index=len(p6.make_folds(cfg)), test_start=h, test_end=h)  # train on everything before
    cls = {s: p6.class_codes(df, s) for s in SIDES}
    primary, ranges = {}, {}
    for s in SIDES:
        m = split(decision, arr[s]["exit"], final, cfg)
        d = {
            "X_train": X[m["train"]], "y_train": cls[s][m["train"]], "w_train": weights[s][m["train"]],
            "X_val": X[m["val"]], "y_val": cls[s][m["val"]], "w_val": weights[s][m["val"]], "X_test": X[:0],
        }  # fmt: skip
        _, _, booster, info = p6.fit_primary("lgbm", names, d, cfg)
        primary[s] = {
            "booster": booster,
            "best_iteration": info["best_iteration"],
            "calibrators": info["calibrators"],
            "class_means": p6.class_means(cls[s][m["train"]], arr[s]["net"]["taker"][m["train"]]),
        }
        ranges[s] = {
            k: [int(decision[m[k]].min()), int(decision[m[k]].max()), int(m[k].sum())]
            for k in ("train", "val")
        }
    # meta: out-of-sample candidates of the walk-forward folds (a deterministic re-run of Workstream C)
    cand = p6.run("lgbm", cfg=cfg, df=df)["candidates"].filter(pl.col("exit_time") < h)
    _, minfo = p6.fit_meta(cand, cand.head(0), cfg)
    meta = {
        "booster": minfo["model"],
        "calibrator": minfo["calibrator"],
        "best_iteration": minfo["meta_best_iteration"],
    }
    if threshold == "val":
        if minfo["val_threshold"] is None:
            raise ValueError(
                "no validation-chosen threshold qualified (Phase 3 rule: >= 100 validation trades)"
            )
        threshold = float(minfo["val_threshold"])
    version = hashlib.sha256(_canonical(primary, meta)).hexdigest()[:16]
    out = root / version
    out.mkdir(parents=True, exist_ok=True)
    with (out / "model.pkl").open("wb") as f:
        pickle.dump({"primary": primary, "meta": meta, "features": names}, f)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    manifest = {
        "version": version,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": commit,
        "threshold": threshold,
        "val_threshold": minfo["val_threshold"],
        "trained_before": cfg.holdout_start.isoformat(),
        "primary_ranges_ms": ranges,
        "meta_candidates": cand.height,
        "features": names,
        "model_sha256": hashlib.sha256((out / "model.pkl").read_bytes()).hexdigest(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return out


def load(path: Path) -> Frozen:
    manifest = json.loads((path / "manifest.json").read_text())
    blob = (path / "model.pkl").read_bytes()
    if hashlib.sha256(blob).hexdigest() != manifest["model_sha256"]:
        raise ValueError(f"{path}: model.pkl does not match its manifest")
    m = pickle.loads(blob)  # our own artifact, verified against the manifest above
    return Frozen(manifest["version"], m["features"], m["primary"], m["meta"], manifest["threshold"],
                  manifest["val_threshold"], manifest)  # fmt: skip


def predict(fz: Frozen, row: pl.DataFrame, eligible: bool) -> dict:
    """Decision for one feature row (columns include fz.features and the meta volatility inputs)."""
    X = row.select(fz.features).cast(pl.Float64).to_numpy()
    probs, e = {}, {}
    for s in SIDES:
        v = fz.primary[s]
        raw = v["booster"].predict(X, num_iteration=v["best_iteration"])
        probs[s] = p6.apply_calibrators(v["calibrators"], raw)[0]
        e[s] = float(probs[s] @ v["class_means"])
    side = int(p6.choose_side(np.array([e["long"]]), np.array([e["short"]]), np.array([eligible]))[0])
    out = {
        "probs": {s: dict(zip(p6.CLASSES, map(float, probs[s]), strict=True)) for s in SIDES},
        "expected_R": e,
        "candidate_side": side,
        "meta_p": None,
        "threshold": fz.threshold,
        "decision": "none",
    }
    if side == 0:
        out["reason"] = "blackout" if not eligible else "no side with expected net R > 0"
        return out
    ps = probs["long" if side == 1 else "short"]
    vol = row.select(p6.META_VOL).cast(pl.Float64).to_numpy()[0]
    mx = np.concatenate([ps, [side], vol])[None, :]
    raw = fz.meta["booster"].predict(mx, num_iteration=fz.meta["best_iteration"])
    out["meta_p"] = float(fz.meta["calibrator"].predict(raw)[0])
    if out["meta_p"] >= fz.threshold:
        out["decision"], out["reason"] = ("long" if side == 1 else "short"), "meta P(success) >= threshold"
    else:
        out["reason"] = "meta P(success) below threshold"
    return out


def latest(root: Path = MODEL_ROOT) -> Path:
    """The most recently created artifact directory."""
    dirs = [d for d in root.iterdir() if (d / "manifest.json").exists()]
    return max(dirs, key=lambda d: json.loads((d / "manifest.json").read_text())["created_at"])


if __name__ == "__main__":
    if sys.argv[1] == "freeze":
        arg = sys.argv[sys.argv.index("--threshold") + 1] if "--threshold" in sys.argv else p6.META_THRESHOLD
        thr = arg if arg == "val" else float(arg)
        path = freeze(thr)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in json.loads((path / "manifest.json").read_text()).items()
                    if k != "features"
                },
                indent=1,
            )
        )
