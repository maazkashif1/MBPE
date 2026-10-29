import sys
from pathlib import Path

import numpy as np
import polars as pl

from mbpe.labeling.label_tool import EXCLUDED, excluded_ids

SCORES = Path("data/labeling/sample_scores.parquet")
DEFAULT_LABELS = Path("data/labeling/llm_labels.csv")
CLASSES = ["negative", "neutral", "positive"]


def cohen_kappa(a: list[str], b: list[str]) -> float:
    a, b = np.array(a), np.array(b)
    po = (a == b).mean()
    pe = sum((a == c).mean() * (b == c).mean() for c in CLASSES)
    return float((po - pe) / (1 - pe)) if pe < 1 else np.nan


def macro_f1(truth: list[str], pred: list[str]) -> float:
    t, p = np.array(truth), np.array(pred)
    f1 = []
    for c in CLASSES:
        tp, fp, fn = ((p == c) & (t == c)).sum(), ((p == c) & (t != c)).sum(), ((p != c) & (t == c)).sum()
        f1.append(2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0)
    return float(np.mean(f1))


def agreement(labels_path: Path, scores_path: Path = SCORES, excluded: Path = EXCLUDED) -> str:
    labels = pl.read_csv(labels_path).filter(~pl.col("id").is_in(list(excluded_ids(excluded))))
    judge = labels["judge"][0] if "judge" in labels.columns else "human (labeling/labels.csv)"
    scores = pl.read_parquet(scores_path).with_columns(
        pred=pl.concat_list("p_neg", "p_neu", "p_pos").list.arg_max().replace_strict(dict(enumerate(CLASSES)))
    )
    out = [
        f"Labels: {labels_path} ({labels.height} benchmark headlines; judged by: {judge})",
        "",
        "| scorer | n | accuracy | Cohen's kappa | macro-F1 | Spearman(score, label) |",
        "|---|---|---|---|---|---|",
    ]
    mats = []
    for model in scores["model"].unique(maintain_order=True):
        j = labels.join(scores.filter(pl.col("model") == model), on="id")
        truth, pred = j["label"].to_list(), j["pred"].to_list()
        ordinal = j["label"].replace_strict(
            {"negative": -1, "neutral": 0, "positive": 1}, return_dtype=pl.Float64
        )
        rho = (
            pl.DataFrame({"s": j["score"], "o": ordinal}).select(pl.corr("s", "o", method="spearman")).item()
        )
        out.append(
            f"| {model} | {j.height} | {np.mean(np.array(truth) == np.array(pred)):.1%} | "
            f"{cohen_kappa(truth, pred):.3f} | {macro_f1(truth, pred):.3f} | {rho:.3f} |"
        )
        m = pl.DataFrame({"label": truth, "pred": pred}).group_by("label", "pred").len()
        rows = ["| label \\ scorer | " + " | ".join(CLASSES) + " |", "|---|---|---|---|"]
        for c in CLASSES:
            cells = [m.filter((pl.col("label") == c) & (pl.col("pred") == p))["len"].sum() for p in CLASSES]
            rows.append(f"| {c} | " + " | ".join(str(x) for x in cells) + " |")
        mats += ["", f"Confusion matrix, {model} (rows: labels; columns: scorer argmax):", "", *rows]
    return "\n".join(out + mats) + "\n"


if __name__ == "__main__":
    print(agreement(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LABELS))
