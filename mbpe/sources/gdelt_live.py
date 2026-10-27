"""GDELT GKG live collector: the newest English 15-min GKG batch, plus catch-up for missed batches.

Usage: uv run python -m mbpe.sources.gdelt_live      # one collection run; schedule it every 15 minutes
       uv run python -m mbpe.sources.gdelt_live lag  # arrival-lag statistics of the newest-batch fetches
       (examples in deploy/: cron and systemd timer at minutes 2, 17, 32, 47)

Each run:
- reads lastupdate.txt and downloads the newest English GKG file, verifying size and MD5 against the
  listing (catch-up files aren't listed there, so they get the zip's own CRC check instead)
- fetches any batch from the last 48 h that isn't saved yet, by file name
- writes data/raw/gdelt_live/<YYYY-MM-DD>/<HHMM>.parquet: the matched rows (same columns as the
  BigQuery backfill, plus fetched_at), and <HHMM>.json: batch metadata, including n_rows, the total
  number of English articles in the batch (live coverage). The .json is written last; its presence
  marks a saved batch.
- lists unsaved batches older than the 48 h window in data/raw/gdelt_live/gaps.json

Parsing (parse_gkg) is shared with the backfill parity check, and keyword matching is the backfill's
generated rule (gdelt_bq.match_keywords).
"""

import hashlib
import io
import json
import logging
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import polars as pl

from mbpe.sources.gdelt_bq import AVAILABILITY_LAG, BACKFILL_SCHEMA, match_keywords

log = logging.getLogger(__name__)
BASE = "https://data.gdeltproject.org/gdeltv2"
LIVE = Path("data/raw/gdelt_live")
STEP = timedelta(minutes=15)
CATCHUP = timedelta(hours=48)
UA = {"User-Agent": "mbpe/0.1"}
TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>")  # same pattern as the backfill's REGEXP_EXTRACT
GKG_FIELDS = 27  # GKG 2.1 columns
DATE, SOURCE, URL, TONE, TRANSLATION, EXTRAS = 1, 3, 4, 15, 25, 26  # column positions


class ChecksumError(ValueError):
    pass


# ---------- parsing (shared with the parity check) ----------


def parse_gkg(raw: bytes) -> tuple[pl.DataFrame, int]:
    """Parse one uncompressed GKG 2.1 file.

    Returns (matched English-source rows in BACKFILL_SCHEMA, total English-source rows).
    """
    rows, n_rows = [], 0
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        f = line.split("\t")
        if len(f) < GKG_FIELDS or f[TRANSLATION]:  # malformed / translated (English files have none)
            continue
        n_rows += 1
        m = TITLE_RE.search(f[EXTRAS])
        title = m.group(1) if m else None
        keywords = match_keywords(title, f[URL])
        if keywords:
            rows.append((int(f[DATE]), f[SOURCE], f[URL], f[TONE], title, keywords))
    return pl.DataFrame(rows, schema=BACKFILL_SCHEMA, orient="row"), n_rows


def unzip_verified(blob: bytes, size: int | None = None, md5: str | None = None) -> bytes:
    """Check size and MD5 when known, then the zip's CRC; return the uncompressed CSV."""
    if size is not None and len(blob) != size:
        raise ChecksumError(f"size {len(blob)} != listed {size}")
    if md5 is not None and hashlib.md5(blob).hexdigest() != md5:
        raise ChecksumError("MD5 mismatch")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if (bad := z.testzip()) is not None:
            raise ChecksumError(f"zip CRC failed for {bad}")
        return z.read(z.namelist()[0])


# ---------- download ----------


def http_get(url: str) -> tuple[bytes, dict] | None:
    """(body, headers), or None on 404."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            return r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def batch_url(ts: datetime) -> str:
    return f"{BASE}/{ts:%Y%m%d%H%M%S}.gkg.csv.zip"


def parse_lastupdate(text: str) -> tuple[datetime, int, str]:
    """(batch time, size, md5) of the English GKG file in lastupdate.txt."""
    for line in text.splitlines():
        size, md5, url = line.split()
        if url.endswith(".gkg.csv.zip"):
            ts = datetime.strptime(url.rsplit("/", 1)[1][:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            return ts, int(size), md5
    raise ValueError("no GKG line in lastupdate.txt")


# ---------- storage ----------


def batch_paths(ts: datetime, root: Path = LIVE) -> tuple[Path, Path]:
    d = root / f"{ts:%Y-%m-%d}"
    return d / f"{ts:%H%M}.parquet", d / f"{ts:%H%M}.json"


def is_saved(ts: datetime, root: Path = LIVE) -> bool:
    return batch_paths(ts, root)[1].exists()


def save_batch(ts: datetime, blob: bytes, meta: dict, root: Path = LIVE, **verify) -> dict:
    df, n_rows = parse_gkg(unzip_verified(blob, **verify))
    fetched_at = datetime.fromisoformat(meta["fetched_at"])
    df = df.with_columns(fetched_at=pl.lit(fetched_at, dtype=pl.Datetime("ms", "UTC")))
    parquet, record = batch_paths(ts, root)
    parquet.parent.mkdir(parents=True, exist_ok=True)
    tmp = parquet.with_name(parquet.name + ".part")
    df.write_parquet(tmp)
    tmp.replace(parquet)
    meta = {**meta, "DATE": int(f"{ts:%Y%m%d%H%M%S}"), "n_rows": n_rows, "matched": df.height,
            "size": len(blob), "md5": hashlib.md5(blob).hexdigest()}  # fmt: skip
    record.write_text(json.dumps(meta, indent=2))  # last: marks the batch as saved
    return meta


def check_publish_lag(ts: datetime, published_at: datetime) -> dict:
    """Warn when GDELT published a batch more than AVAILABILITY_LAG after its label.

    Then "available at = batch time + 15 min" would treat the batch as available before it existed.
    Only measurable for the newest batch, whose publish time is lastupdate.txt's Last-Modified.
    """
    lag = published_at - ts
    late = lag > AVAILABILITY_LAG
    if late:
        log.warning("batch %s published %s after its label (> %s): availability rule would leak",
                    ts.isoformat(), lag, AVAILABILITY_LAG)  # fmt: skip
    return {"publish_minus_label_s": lag.total_seconds(), "late_publish": late}


# ---------- collection run ----------


def collect(root: Path = LIVE, get=http_get, now: datetime | None = None) -> dict:
    """One run: newest batch (size + MD5 checked), then catch-up over the last 48 h."""
    body, headers = get(f"{BASE}/lastupdate.txt")
    latest, size, md5 = parse_lastupdate(body.decode())
    # when GDELT last rewrote lastupdate.txt, i.e. when the newest batch was published (header case varies)
    modified = next((v for k, v in headers.items() if k.lower() == "last-modified"), None)
    published_at = parsedate_to_datetime(modified).astimezone(UTC).isoformat() if modified else None
    summary = {"latest": latest.isoformat(), "saved": [], "already": 0, "not_published": []}

    ts = latest
    while ts > latest - CATCHUP:
        if is_saved(ts, root):
            summary["already"] += 1
        else:
            got = get(batch_url(ts))
            if got is None:  # GDELT never published this batch (an outage) or not yet
                summary["not_published"].append(ts.isoformat())
            else:
                fetched_at = (now or datetime.now(UTC)).isoformat()
                newest = ts == latest
                meta = {
                    "fetched_at": fetched_at,
                    "kind": "latest" if newest else "catchup",
                    "md5_verified": newest,
                    "lastupdate_modified": published_at if newest else None,
                }
                if newest and published_at:
                    meta |= check_publish_lag(ts, datetime.fromisoformat(published_at))
                verify = {"size": size, "md5": md5} if newest else {}
                save_batch(ts, got[0], meta, root, **verify)
                summary["saved"].append(ts.isoformat())
        ts -= STEP

    summary["gaps_beyond_48h"] = update_gaps(latest, root)
    for t in summary["not_published"]:
        log.warning("batch %s not available (GDELT outage or not yet published)", t)
    log.info("gdelt_live: %s", {k: (len(v) if isinstance(v, list) else v) for k, v in summary.items()})
    return summary


def update_gaps(latest: datetime, root: Path = LIVE) -> int:
    """Rewrite gaps.json: unsaved batches between our first saved batch and the 48 h window."""
    saved = sorted(
        datetime.strptime(f"{p.parent.name} {p.stem}", "%Y-%m-%d %H%M").replace(tzinfo=UTC)
        for p in root.glob("*/[0-9][0-9][0-9][0-9].json")
    )
    window_start = latest - CATCHUP
    gaps = []
    if saved:
        have = set(saved)
        ts = saved[0]
        while ts <= window_start:
            if ts not in have:
                gaps.append(ts.isoformat())
            ts += STEP
    path = root / "gaps.json"
    previous = json.loads(path.read_text()) if path.exists() else []
    if new := sorted(set(gaps) - set(previous)):
        log.warning("gdelt_live: %d batch(es) now older than 48 h and never saved: %s ...", len(new), new[:3])
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gaps, indent=0))
    return len(gaps)


def lag_report(root: Path = LIVE) -> pl.DataFrame:
    """Per newest-batch fetch: fetched_at - label and publish time - label (catch-up fetches excluded)."""
    metas = [json.loads(p.read_text()) for p in sorted(root.glob("*/[0-9][0-9][0-9][0-9].json"))]
    label = pl.col("DATE").cast(pl.String).str.strptime(pl.Datetime("ms", "UTC"), "%Y%m%d%H%M%S")
    parse = lambda c: pl.col(c).str.to_datetime(time_zone="UTC", time_unit="ms")  # noqa: E731
    return (
        pl.DataFrame([m for m in metas if m["kind"] == "latest"])
        .select(
            "DATE",
            fetch_lag_min=(parse("fetched_at") - label).dt.total_seconds() / 60,
            publish_lag_min=(parse("lastupdate_modified") - label).dt.total_seconds() / 60,
        )
        .with_columns(late_publish=pl.col("publish_lag_min") > AVAILABILITY_LAG.total_seconds() / 60)
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if sys.argv[1:] == ["lag"]:
        r = lag_report()
        with pl.Config(tbl_rows=50):
            print(r)
            print(r.select(pl.len(), pl.col("fetch_lag_min", "publish_lag_min").min().name.suffix("_min"),
                           pl.col("fetch_lag_min", "publish_lag_min").median().name.suffix("_median"),
                           pl.col("fetch_lag_min", "publish_lag_min").max().name.suffix("_max"),
                           pl.col("late_publish").sum()))  # fmt: skip
    else:
        collect()
