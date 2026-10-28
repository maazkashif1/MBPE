"""Terminal tool for hand-labeling the headline sample.

Usage: uv run python -m mbpe.labeling.label

Shows one headline at a time from labeling/sample.csv (title only: no scores, dates, sources or keywords).
Headlines listed in labeling/excluded.csv (id,reason) are skipped: they're left out of the model comparison.
Keys: p = positive, n = negative, u = neutral, b = back one, q = quit.
Every answer is saved to labeling/labels.csv (id,label) immediately; restarting resumes at the next unlabeled
headline. When all are labeled, prints a count per label. See labeling/GUIDE.md for what the labels mean.
"""

import csv
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

SAMPLE = Path("labeling/sample.csv")
LABELS = Path("labeling/labels.csv")
EXCLUDED = Path("labeling/excluded.csv")
KEYS = {"p": "positive", "n": "negative", "u": "neutral"}
HELP = "p = positive   n = negative   u = neutral   b = back   q = quit"


def excluded_ids(path: Path = EXCLUDED) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        return {r["id"] for r in csv.DictReader(f)}


def load(
    sample: Path = SAMPLE, labels: Path = LABELS, excluded: Path = EXCLUDED
) -> tuple[list[dict], dict[str, str]]:
    """Sample rows to label (excluded ids removed) and the labels saved so far."""
    skip = excluded_ids(excluded)
    with sample.open(newline="") as f:
        rows = [{"id": r["id"], "title": r["title"]} for r in csv.DictReader(f) if r["id"] not in skip]
    done = {}
    if labels.exists():
        with labels.open(newline="") as f:
            done = {r["id"]: r["label"] for r in csv.DictReader(f) if r["label"] in KEYS.values()}
    return rows, done


def save(rows: list[dict], done: dict[str, str], labels: Path = LABELS) -> None:
    """Write id,label in sample order, atomically. Labels for ids not in `rows` (e.g. excluded headlines
    labeled earlier) are kept at the end: nothing already saved is ever dropped."""
    tmp = labels.with_name(labels.name + ".part")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "label"])
        shown = {r["id"] for r in rows}
        w.writerows((r["id"], done[r["id"]]) for r in rows if r["id"] in done)
        w.writerows((i, done[i]) for i in sorted(done) if i not in shown)
    tmp.replace(labels)


def next_unlabeled(rows: list[dict], done: dict[str, str], after: int = -1) -> int | None:
    """First unlabeled index after `after`, wrapping to the start; None if everything is labeled."""
    order = list(range(after + 1, len(rows))) + list(range(0, after + 1))
    return next((i for i in order if rows[i]["id"] not in done), None)


def counts(rows: list[dict], done: dict[str, str]) -> str:
    c = Counter(done[r["id"]] for r in rows if r["id"] in done)
    return "  ".join(f"{label}: {c.get(label, 0)}" for label in KEYS.values())


def run(
    rows: list[dict],
    done: dict[str, str],
    read_key: Callable[[], str],
    write: Callable[[str], None],
    labels: Path = LABELS,
    clear: str = "",
) -> dict[str, str]:
    i = next_unlabeled(rows, done)
    n_done = lambda: sum(r["id"] in done for r in rows)  # noqa: E731  (excluded headlines don't count)
    while i is not None:
        r = rows[i]
        current = f"   (currently: {done[r['id']]})" if r["id"] in done else ""
        write(
            f"{clear}{n_done()}/{len(rows)} labeled   headline {i + 1} of {len(rows)}{current}\n\n"
            f"  {r['title']}\n\n{HELP}\n"
        )
        key = read_key().lower()
        if key in KEYS:
            done[r["id"]] = KEYS[key]
            save(rows, done, labels)
            i = next_unlabeled(rows, done, i)
        elif key == "b":
            i = max(0, i - 1)
        elif key == "q":
            write(f"\nSaved {n_done()}/{len(rows)} to {labels}. Run again to resume.\n")
            return done
    write(f"{clear}All {len(rows)} headlines labeled ({labels}).\n{counts(rows, done)}\n")
    return done


def getch() -> str:
    """One keypress without Enter (falls back to a line of input when not on a terminal)."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line.strip()[:1] if line else "q"
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return "q" if ch in ("\x03", "\x04") else ch  # Ctrl-C / Ctrl-D quit (progress is already saved)


def main() -> None:
    rows, done = load()
    clear = "\033[2J\033[H" if sys.stdout.isatty() else ""
    run(rows, done, getch, lambda s: (sys.stdout.write(s), sys.stdout.flush()), clear=clear)


if __name__ == "__main__":
    main()
