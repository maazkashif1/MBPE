import csv

from mbpe.labeling.label import load, run

TITLES = ["Bitcoin climbs to record", "Exchange hacked", "Fed meets Wednesday", "CPI cools"]


def setup(tmp_path):
    sample = tmp_path / "sample.csv"
    with sample.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "label"])
        w.writerows((f"h{i:03d}", t, "") for i, t in enumerate(TITLES, 1))
    return sample, tmp_path / "labels.csv"


def session(sample, labels, keys, excluded=None):
    rows, done = load(sample, labels, excluded or sample.with_name("no_exclusions.csv"))
    out = []
    it = iter(keys)
    run(rows, done, lambda: next(it), out.append, labels=labels)
    return "".join(out)


def saved(labels):
    with labels.open(newline="") as f:
        return {r["id"]: r["label"] for r in csv.DictReader(f)}


def test_label_save_quit_and_resume(tmp_path):
    sample, labels = setup(tmp_path)
    out = session(sample, labels, ["p", "x", "n", "q"])  # "x" is ignored
    assert saved(labels) == {"h001": "positive", "h002": "negative"}  # saved after each answer
    assert "2/4 labeled" in out
    out = session(sample, labels, ["u", "q"])  # resumes at the first unlabeled headline
    assert "Fed meets Wednesday" in out.split("\n\n")[1]
    assert saved(labels)["h003"] == "neutral"


def test_back_relabels_previous_and_finish_prints_counts(tmp_path):
    sample, labels = setup(tmp_path)
    out = session(sample, labels, ["p", "b", "n", "u", "u", "u"])  # back to h001, change it to negative
    assert saved(labels) == {"h001": "negative", "h002": "neutral", "h003": "neutral", "h004": "neutral"}
    assert "currently: positive" in out
    assert out.rstrip().endswith("positive: 0  negative: 1  neutral: 3")


def test_only_titles_are_shown(tmp_path):
    sample, labels = setup(tmp_path)
    out = session(sample, labels, ["q"])
    assert "Bitcoin climbs to record" in out
    for leak in ("score", "DATE", "http", "keywords", "Source"):
        assert leak not in out


def test_excluded_headlines_are_skipped(tmp_path):
    sample, labels = setup(tmp_path)
    excluded = tmp_path / "excluded.csv"
    excluded.write_text("id,reason\nh001,discussed with an LLM\n")
    rows, done = load(sample, labels, excluded)
    assert [r["id"] for r in rows] == ["h002", "h003", "h004"]
    out = []
    keys = iter(["u", "u", "u"])
    run(rows, done, lambda: next(keys), out.append, labels=labels)
    assert "Bitcoin climbs to record" not in "".join(out)
    assert "0/3 labeled" in out[0]
    assert set(saved(labels)) == {"h002", "h003", "h004"}


def test_labels_of_excluded_headlines_are_kept_but_not_counted(tmp_path):
    sample, labels = setup(tmp_path)
    labels.write_text("id,label\nh001,neutral\n")  # labeled before h001 was excluded
    excluded = tmp_path / "excluded.csv"
    excluded.write_text("id,reason\nh001,discussed with an LLM\n")
    out = session(sample, labels, ["p", "q"], excluded)
    assert "0/3 labeled" in out and "1/3 labeled" not in out.split("\n\n")[0]
    assert saved(labels) == {"h001": "neutral", "h002": "positive"}  # h001 kept on file
    out = session(sample, labels, ["n", "u"], excluded)
    assert out.rstrip().endswith("positive: 1  negative: 1  neutral: 1")  # counts cover the 3 shown only


def test_agreement_metrics_on_hand_example():
    import pytest

    from mbpe.labeling.agreement import cohen_kappa, macro_f1

    truth = ["positive", "negative", "neutral", "neutral"]
    pred = ["positive", "neutral", "neutral", "neutral"]
    # observed 3/4; expected = sum over classes of P_truth * P_pred = 1/16 + 0 + 2/4 * 3/4 = 7/16
    assert cohen_kappa(truth, pred) == pytest.approx((0.75 - 7 / 16) / (1 - 7 / 16))
    # F1: negative 0, neutral 2*2/(2*2+1+0) = 0.8, positive 1.0
    assert macro_f1(truth, pred) == pytest.approx((0 + 0.8 + 1.0) / 3)
    assert cohen_kappa(truth, truth) == pytest.approx(1.0)
