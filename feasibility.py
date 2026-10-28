"""Read-only feasibility checks: Binance public archive + GDELT DOC 2.0.

Usage: uv run feasibility.py [check ...]   (default: all checks)
Checks: history_depth, binance_day, binance_scan, gdelt_week, gdelt_volume, gdelt_buckets, gdelt_bulk

Each check rewrites only its own <!-- name --> section of FEASIBILITY.md.
"""

import csv
import html
import http.client
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from calendar import monthrange
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

VISION = "https://data.binance.vision/data"
S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = {"User-Agent": "mbpe-feasibility/0.2"}
REPORT = "FEASIBILITY.md"
FILES_CSV = "feasibility_binance_files.csv"
SCAN_START = date(2020, 9, 1)
GDELT_START = datetime(2020, 9, 1, tzinfo=UTC)
ARTLIST_CAP = 250


# ---------- helpers ----------

def get(url: str, method: str = "GET") -> bytes:
    req = urllib.request.Request(url, headers=UA, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fetch_optional(url: str) -> bytes | None:
    """GET with retries; None on 404 (file not published)."""
    for attempt in range(4):
        try:
            return get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException):  # e.g. IncompleteRead
            if attempt == 3:
                raise
        time.sleep(2 * (attempt + 1))


def unzip_csv(blob: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode()
    return [r for r in csv.reader(io.StringIO(text)) if r]


def drop_header(rows: list[list[str]]) -> list[list[str]]:
    # some files carry a header row (open_time / create_time / calc_time), older ones don't
    return rows[1:] if rows and rows[0][0].endswith("_time") else rows


def epoch_to_dt(ts: str) -> datetime:
    n = int(ts)
    if n > 10**14:  # spot switched to microsecond timestamps in 2025
        n //= 1000
    return datetime.fromtimestamp(n / 1000, UTC)


def gdelt(params: dict) -> dict:
    url = f"{GDELT}?{urllib.parse.urlencode({'format': 'json', **params})}"
    for attempt in range(6):  # GDELT 429s aggressively; back off and retry
        try:
            body = get(url).decode()
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(30 * (attempt + 1))  # long-span queries get throttled harder
    time.sleep(6)  # GDELT asks for ≤1 request per 5 s
    try:
        return json.loads(body)
    except json.JSONDecodeError:  # GDELT returns plain-text errors (bad query, too long, ...)
        raise RuntimeError(f"GDELT returned non-JSON: {body[:200]!r}")


def gdelt_ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def write_section(name: str, lines: list[str]) -> None:
    start, end = f"<!-- {name} -->", f"<!-- /{name} -->"
    stamp = f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `feasibility.py {name}`_"
    block = "\n".join([start, lines[0], "", stamp, *lines[1:], end])
    try:
        with open(REPORT) as f:
            doc = f.read()
    except FileNotFoundError:
        doc = "# MBPE feasibility\n"
    if start in doc:
        pre, rest = doc.split(start, 1)
        doc = pre + block + rest.split(end, 1)[1]
    else:
        doc = doc.rstrip("\n") + "\n\n" + block + "\n"
    with open(REPORT, "w") as f:
        f.write(doc)


def ranges(days: list[str]) -> list[str]:
    """Collapse sorted ISO dates into 'a → b (n days)' runs."""
    out, run = [], []
    for d in days:
        if run and date.fromisoformat(d) - date.fromisoformat(run[-1]) != timedelta(days=1):
            out.append(run)
            run = []
        run.append(d)
    if run:
        out.append(run)
    return [r[0] if len(r) == 1 else f"{r[0]} → {r[-1]} ({len(r)} days)" for r in out]


# ---------- checks ----------

def check_history_depth() -> list[str]:
    prefix = "data/futures/um/daily/metrics/BTCUSDT/"
    listing = get(f"{S3_LIST}?{urllib.parse.urlencode({'delimiter': '/', 'prefix': prefix, 'max-keys': 1})}").decode()
    first_key = listing.split("<Key>", 1)[1].split("</Key>", 1)[0] if "<Key>" in listing else "none"

    def status(d: str) -> int:
        try:
            get(f"{VISION}/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{d}.zip", "HEAD")
            return 200
        except urllib.error.HTTPError as e:
            return e.code

    arts = gdelt({
        "query": "bitcoin", "mode": "artlist", "maxrecords": 5,
        "startdatetime": "20200101000000", "enddatetime": "20200108000000",
    }).get("articles", [])
    return [
        "## History depth",
        "",
        f"- Earliest USD-M BTCUSDT metrics file in the archive listing: `{first_key.rsplit('/', 1)[-1]}`",
        f"- `BTCUSDT-metrics-2020-01-01.zip` → HTTP {status('2020-01-01')}; "
        f"`BTCUSDT-metrics-2020-09-01.zip` → HTTP {status('2020-09-01')}",
        "- **Implication:** the open-interest/ratio metrics start 2020-09-01, not 2020-01. "
        "The derivatives features (or the whole dataset) start there.",
        f"- GDELT DOC artlist for `bitcoin`, 2020-01-01 → 2020-01-08: {len(arts)} articles returned "
        f"(first seendate `{arts[0]['seendate'] if arts else 'n/a'}`). The DOC API does serve 2020 history.",
        "",
    ]


def check_binance_day(day: str) -> list[str]:
    out = [f"## Binance single-day sample — BTCUSDT USD-M, {day}", ""]

    url = f"{VISION}/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{day}.zip"
    header, *rows = unzip_csv(get(url))
    times = sorted(datetime.fromisoformat(r[0]) for r in rows)
    steps = Counter(b - a for a, b in zip(times, times[1:]))
    out += [
        f"### Metrics\n- URL: `{url}`",
        f"- Rows: **{len(rows)}**",
        f"- Columns: `{', '.join(header)}`",
        f"- Range: {times[0]} → {times[-1]}",
        "- Time steps (step: count): " + ", ".join(f"{s}: {n}" for s, n in steps.most_common()),
        "",
    ]

    url = f"{VISION}/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{day}.zip"
    rows = drop_header(unzip_csv(get(url)))
    times = sorted(epoch_to_dt(r[0]) for r in rows)
    steps = Counter(b - a for a, b in zip(times, times[1:]))
    out += [
        f"### 1m klines\n- URL: `{url}`",
        f"- Rows: **{len(rows)}** (expected 1440)",
        f"- Range: {times[0]} → {times[-1]}",
        "- Time steps (step: count): " + ", ".join(f"{s}: {n}" for s, n in steps.most_common()),
        "",
    ]
    return out


# name -> (url template, period kind, expected rows fn, row -> (period, timestamp), bar spacing or None)
DATASETS = {
    "um_metrics": (
        f"{VISION}/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{{p}}.zip", "day",
        lambda p: 288, lambda r: (r[0][:10], datetime.fromisoformat(r[0])), timedelta(minutes=5),
    ),
    "um_klines_1m": (
        f"{VISION}/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{{p}}.zip", "day",
        lambda p: 1440, lambda r: (epoch_to_dt(r[0]).date().isoformat(), epoch_to_dt(r[0])),
        timedelta(minutes=1),
    ),
    "spot_klines_1m": (
        f"{VISION}/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{{p}}.zip", "day",
        lambda p: 1440, lambda r: (epoch_to_dt(r[0]).date().isoformat(), epoch_to_dt(r[0])),
        timedelta(minutes=1),
    ),
    "um_funding_monthly": (
        f"{VISION}/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{{p}}.zip", "month",
        # BTCUSDT funds every 8h -> 3 per day; calc_time carries ms jitter, so no spacing check
        lambda p: 3 * monthrange(int(p[:4]), int(p[5:]))[1],
        lambda r: (epoch_to_dt(r[0]).strftime("%Y-%m"), epoch_to_dt(r[0])), None,
    ),
}
PENDING_DAYS = 3  # daily files this recent (and the current month's funding) may not be published yet
FLAGS = ["short", "dupes", "extra", "uneven"]


def is_pending(period: str, today: date) -> bool:
    if len(period) == 7:
        return period == today.strftime("%Y-%m")
    return date.fromisoformat(period) > today - timedelta(days=PENDING_DAYS)


def scan_file(name: str, period: str, today: date) -> dict:
    tmpl, _, expected_fn, key, step = DATASETS[name]
    rec = {"dataset": name, "period": period, "expected": expected_fn(period), "rows": 0,
           "in_period": 0, "in_period_unique": 0, "max_gap": "", "status": "ok"}
    try:
        blob = fetch_optional(tmpl.format(p=period))
    except Exception as e:
        return {**rec, "status": f"error: {type(e).__name__}"}
    if blob is None:
        return {**rec, "status": "pending" if is_pending(period, today) else "missing"}
    rows = drop_header(unzip_csv(blob))
    ts = sorted(t for p, t in map(key, rows) if p == period)
    uniq = sorted(set(ts))
    gaps = Counter(b - a for a, b in zip(uniq, uniq[1:]))
    rec.update(rows=len(rows), in_period=len(ts), in_period_unique=len(uniq),
               max_gap=str(max(gaps)) if gaps else "")
    flags = []
    if len(uniq) < rec["expected"]:
        flags.append("short")
    if len(ts) != len(uniq):
        flags.append("dupes")
    if len(rows) != len(ts):
        flags.append("extra")  # rows belonging to another period
    if step and (set(gaps) - {step} or (uniq and (uniq[0] - uniq[0].replace(hour=0, minute=0, second=0,
                                                                              microsecond=0)) % step)):
        flags.append("uneven")  # a gap, an off-grid timestamp, or a sub-step spacing
    rec["status"] = "+".join(flags) or "ok"
    return rec


def check_binance_scan() -> list[str]:
    today = date.today()
    days = [(SCAN_START + timedelta(n)).isoformat() for n in range((today - SCAN_START).days + 1)]
    months = sorted({d[:7] for d in days})
    jobs = [(name, p) for name, (_, kind, *_) in DATASETS.items()
            for p in (days if kind == "day" else months)]

    print(f"binance_scan: {len(jobs)} files …", flush=True)
    with ThreadPoolExecutor(32) as ex:
        recs = list(ex.map(lambda j: scan_file(*j, today), jobs))

    with open(FILES_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0]))
        w.writeheader()
        w.writerows(recs)

    out = [
        f"## Binance archive scan — BTCUSDT, {SCAN_START} → {today}",
        "",
        f"Per-file row counts for all {len(recs)} files: [`{FILES_CSV}`]({FILES_CSV}).",
        "",
        "- `pending` = not published yet. Applies to daily files from the last "
        f"{PENDING_DAYS} days and the current month's funding file. Not counted as missing.",
        "- `short` = fewer unique in-period timestamps than expected.",
        "- `dupes` = repeated timestamps.",
        "- `extra` = rows that belong to another day/month.",
        "- `uneven` = in-period timestamps not on an exact 5-min (metrics) / 1-min (klines) grid: a gap, "
        "an off-grid timestamp, or tighter spacing. Checked even when the row count is right. "
        "Funding has no spacing check (calc_time has ms jitter).",
        "- One file can carry several flags, e.g. `short+uneven`.",
        "",
        "| dataset | files | present | ok | missing | pending | " + " | ".join(FLAGS) + " | error | common row counts |",
        "|---|---|---|---|---|---|" + "---|" * len(FLAGS) + "---|---|",
    ]
    detail = []
    for name in DATASETS:
        rs = [r for r in recs if r["dataset"] == name]
        st = Counter(f for r in rs for f in r["status"].split(":")[0].split("+"))
        counts = Counter(r["rows"] for r in rs if r["rows"])
        present = len(rs) - st["missing"] - st["pending"] - st["error"]
        out.append(
            f"| {name} | {len(rs)} | {present} | {st['ok']} | {st['missing']} | {st['pending']} | "
            + " | ".join(str(st[f]) for f in FLAGS) + f" | {st['error']} | "
            + ", ".join(f"{k}×{v}" for k, v in counts.most_common(3)) + " |"
        )
        daily = DATASETS[name][1] == "day"
        missing = [r["period"] for r in rs if r["status"] == "missing"]
        pending = [r["period"] for r in rs if r["status"] == "pending"]
        odd = [r for r in rs if r["status"] not in ("ok", "missing", "pending")]
        detail += [f"### {name}", ""]
        detail.append("- Missing: " + (", ".join(ranges(missing) if daily else missing) if missing else "none"))
        detail.append("- Pending: " + (", ".join(pending) if pending else "none"))
        if odd:
            detail.append("- Flagged:")
            detail += [f"  - {r['period']}: **{r['status']}** rows={r['rows']}, in-period={r['in_period']}, "
                       f"unique={r['in_period_unique']}, expected={r['expected']}, max gap={r['max_gap'] or '-'}"
                       for r in odd]
        else:
            detail.append("- Flagged: none")
        detail.append("")
    return out + [""] + detail


def check_gdelt_week() -> list[str]:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    window = {"query": "bitcoin", "startdatetime": gdelt_ts(start), "enddatetime": gdelt_ts(end)}
    out = [f"## GDELT one-week sample — `bitcoin`, {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC", ""]

    arts = gdelt({**window, "mode": "artlist", "maxrecords": ARTLIST_CAP}).get("articles", [])
    fields = sorted({k for a in arts for k in a})
    seen = sorted(a["seendate"] for a in arts if "seendate" in a)
    out += [
        "### ArtList",
        f"- Articles returned: **{len(arts)}** (one ArtList call returns at most {ARTLIST_CAP})",
        f"- Fields: `{', '.join(fields)}`",
        "- Timestamp field: `seendate` only (when GDELT first saw the article, 15-min resolution); "
        f"range {seen[0] if seen else '-'} → {seen[-1] if seen else '-'}",
        "",
    ]

    tl = gdelt({**window, "mode": "timelinevolraw"}).get("timeline", [])
    points = tl[0]["data"] if tl else []
    out += [
        "### TimelineVolRaw (true volume, not capped)",
        f"- Total matching articles in window: **{sum(p.get('value', 0) for p in points)}**",
        f"- Buckets: {len(points)}"
        + (f", e.g. `{points[0]['date']}` → `{points[1]['date']}`" if len(points) > 1 else ""),
        "",
    ]
    return out


def gdelt_many(requests: dict) -> tuple[dict, dict]:
    """Run several GDELT requests; throttling is intermittent, so retry failures in later passes."""
    results, errors = {}, {}
    for pass_no in range(3):
        todo = [k for k in requests if k not in results]
        if not todo:
            break
        if pass_no:
            time.sleep(120)
        for k in todo:
            try:
                results[k] = gdelt(requests[k])
            except Exception as e:
                errors[k] = f"{type(e).__name__}: {e}"
    return results, {k: v for k, v in errors.items() if k not in results}


def timeline_points(resp: dict) -> list[dict]:
    tl = resp.get("timeline") or [{}]
    return tl[0].get("data", [])


def bucket_size(points: list[dict]) -> str:
    if len(points) < 2:
        return "-"
    fmt = "%Y%m%dT%H%M%SZ"
    steps = Counter(datetime.strptime(b["date"], fmt) - datetime.strptime(a["date"], fmt)
                    for a, b in zip(points, points[1:]))
    return str(steps.most_common(1)[0][0])  # most common step, robust to a missing bucket


GDELT_QUERIES = [
    "bitcoin sourcelang:english",
    "crypto SEC sourcelang:english",
    '"Federal Reserve" sourcelang:english',
    "stablecoin sourcelang:english",
]


def check_gdelt_volume() -> list[str]:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    out = [
        f"## GDELT daily volume — {GDELT_START:%Y-%m-%d} → {end:%Y-%m-%d}",
        "",
        f"`timelinevolraw`, counts summed per UTC day. Days over {ARTLIST_CAP} need more than one "
        "ArtList call per day to backfill.",
        "",
        "| query | bucket size | days with data | total articles | max daily count (date) "
        f"| median daily | days > {ARTLIST_CAP} |",
        "|---|---|---|---|---|---|---|",
    ]
    results, errors = gdelt_many({q: {"query": q, "mode": "timelinevolraw",
                                      "startdatetime": gdelt_ts(GDELT_START), "enddatetime": gdelt_ts(end)}
                                  for q in GDELT_QUERIES})
    for q in GDELT_QUERIES:
        if q not in results:
            out.append(f"| `{q}` | FAILED after 3 passes: `{errors[q]}` | | | | | |")
            continue
        points = timeline_points(results[q])
        daily = Counter()
        for p in points:
            daily[p["date"][:8]] += p.get("value", 0)
        bucket = bucket_size(points)
        vals = sorted(daily.values())
        top_day, top = max(daily.items(), key=lambda kv: kv[1]) if daily else ("-", 0)
        out.append(
            f"| `{q}` | {bucket} | {sum(v > 0 for v in vals)} | {sum(vals)} | "
            f"{top} ({top_day[:4]}-{top_day[4:6]}-{top_day[6:]}) | {vals[len(vals) // 2] if vals else 0} | "
            f"{sum(v > ARTLIST_CAP for v in vals)} |"
        )
    return out + [""]


BUCKET_QUERIES = ["bitcoin sourcelang:english", '"Federal Reserve" sourcelang:english']
BUCKET_MODES = ["timelinevolraw", "timelinetone"]
BUCKET_WINDOWS = [1, 3, 7, 30]  # days


def check_gdelt_buckets() -> list[str]:
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    reqs = {(q, m, d): {"query": q, "mode": m, "startdatetime": gdelt_ts(end - timedelta(days=d)),
                        "enddatetime": gdelt_ts(end)}
            for q in BUCKET_QUERIES for m in BUCKET_MODES for d in BUCKET_WINDOWS}
    results, errors = gdelt_many(reqs)
    out = [
        f"## GDELT bucket size by window length — windows ending {end:%Y-%m-%d %H:%M} UTC",
        "",
        "Bucket size = most common step between consecutive timeline points; `n` = number of points.",
        "",
        "| query | mode | " + " | ".join(f"{d}d window" for d in BUCKET_WINDOWS) + " |",
        "|---|---|" + "---|" * len(BUCKET_WINDOWS),
    ]
    for q in BUCKET_QUERIES:
        for m in BUCKET_MODES:
            cells = []
            for d in BUCKET_WINDOWS:
                if (q, m, d) in results:
                    pts = timeline_points(results[(q, m, d)])
                    cells.append(f"{bucket_size(pts)} (n={len(pts)})")
                else:
                    cells.append(f"FAILED: `{errors[(q, m, d)]}`")
            out.append(f"| `{q}` | {m} | " + " | ".join(cells) + " |")
    return out + [""]


GDELT_BULK = "https://data.gdeltproject.org/gdeltv2"
# GKG 2.1 columns (files have no header row); from the GDELT GKG 2.1 codebook
GKG_COLUMNS = [
    "GKGRECORDID", "V2.1DATE", "V2SOURCECOLLECTIONIDENTIFIER", "V2SOURCECOMMONNAME",
    "V2DOCUMENTIDENTIFIER", "V1COUNTS", "V2.1COUNTS", "V1THEMES", "V2ENHANCEDTHEMES",
    "V1LOCATIONS", "V2ENHANCEDLOCATIONS", "V1PERSONS", "V2ENHANCEDPERSONS", "V1ORGANIZATIONS",
    "V2ENHANCEDORGANIZATIONS", "V1.5TONE", "V2.1ENHANCEDDATES", "V2GCAM", "V2.1SHARINGIMAGE",
    "V2.1RELATEDIMAGES", "V2.1SOCIALIMAGEEMBEDS", "V2.1SOCIALVIDEOEMBEDS", "V2.1QUOTATIONS",
    "V2.1ALLNAMES", "V2.1AMOUNTS", "V2.1TRANSLATIONINFO", "V2EXTRASXML",
]
BULK_QUERIES = {
    "bitcoin": lambda t: "bitcoin" in t,
    "crypto + SEC": lambda t: "crypto" in t and " sec " in f" {t} ",
    "stablecoin": lambda t: "stablecoin" in t,
    "Federal Reserve": lambda t: "federal reserve" in t,
}


def page_title(extras: str) -> str:
    m = re.search(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", extras, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def check_gdelt_bulk() -> list[str]:
    # bulk file server only; no GDELT search-API requests
    last = get(f"{GDELT_BULK}/lastupdate.txt").decode().split("\n")
    gkg_line = next(line for line in last if line.endswith(".gkg.csv.zip"))
    size_c, _, gkg_url = gkg_line.split()

    gkg = []  # (timestamp, size) for every English GKG file in the master list
    req = urllib.request.Request(f"{GDELT_BULK}/masterfilelist.txt", headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            parts = raw.decode(errors="replace").split()
            if len(parts) == 3 and parts[2].endswith(".gkg.csv.zip") and parts[0].isdigit():
                gkg.append((parts[2].rsplit("/", 1)[-1][:14], int(parts[0])))
    gkg.sort()
    since = [s for ts, s in gkg if ts >= SCAN_START.strftime("%Y%m%d")]

    blob = get(gkg_url)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(z.namelist()[0])
    text = raw.decode("utf-8", errors="replace")
    rows = [line.split("\t") for line in text.split("\n") if line]
    ncols = Counter(len(r) for r in rows)
    recs = [{"date": r[1], "source": r[3], "url": r[4], "tone": r[15].split(",")[0],
             "title": page_title(r[26]) if len(r) > 26 else ""} for r in rows]
    with_title = sum(bool(x["title"]) for x in recs)
    ratio = len(raw) / len(blob)

    def norm(x: dict) -> str:
        return re.sub(r"[^a-z0-9]+", " ", f"{x['title']} {x['url']}".lower()).strip()

    est_files = len(since)
    est_from_one = est_files * len(blob)
    out = [
        "## GDELT bulk files (GKG 2.1)",
        "",
        "Bulk download server `data.gdeltproject.org` only; no search-API requests. "
        "Downloaded: `lastupdate.txt`, `masterfilelist.txt` (index files) and **one** GKG data file.",
        "",
        "### Master list",
        f"- GKG files listed: {len(gkg):,}; **earliest GKG file: `{gkg[0][0]}`** "
        f"({gkg[0][0][:4]}-{gkg[0][0][4:6]}-{gkg[0][0][6:8]} {gkg[0][0][8:10]}:{gkg[0][0][10:12]} UTC); "
        f"latest: `{gkg[-1][0]}`",
        "- Naming: `http://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.gkg.csv.zip`, one per 15 minutes "
        "(timestamp = end of the 15-min batch, UTC). Each master-list line is `<size bytes> <md5> <url>`. "
        "Same folder holds `.export.CSV.zip` (events) and `.mentions.CSV.zip`. Non-English sources are in "
        "separate `.translation.gkg.csv.zip` files (`masterfilelist-translation.txt`), not counted here.",
        "",
        f"### Sample file: `{gkg_url.rsplit('/', 1)[-1]}`",
        f"- Compressed: {len(blob) / 1e6:.2f} MB (lastupdate says {int(size_c) / 1e6:.2f} MB); "
        f"uncompressed: {len(raw) / 1e6:.2f} MB (×{ratio:.1f})",
        f"- Rows: **{len(rows):,}** (one per article); tab-separated, no header; columns per row: "
        + ", ".join(f"{k}×{v}" for k, v in ncols.most_common()),
        f"- Columns (GKG 2.1 codebook order): `{', '.join(GKG_COLUMNS)}`",
        f"- **Page title:** {'yes' if with_title else 'no'}. `V2EXTRASXML` holds `<PAGE_TITLE>…</PAGE_TITLE>` "
        f"for {with_title:,} of {len(rows):,} rows ({with_title / len(rows):.0%}).",
        "- `V2.1DATE` = the 15-min batch time the article entered GDELT, usable as `available_at`. "
        "Tone = first field of `V1.5TONE`.",
        "",
        "Example rows:",
        "",
        "| date | source | url | tone | title |",
        "|---|---|---|---|---|",
    ]
    for x in [x for x in recs if x["title"]][:3]:
        out.append(f"| {x['date']} | {x['source']} | {x['url']} | {x['tone']} | {x['title'].replace('|', '/')} |")
    out += ["", "### Query matches in this one file (case-insensitive, title or URL)", "",
            "| query | rows |", "|---|---|"]
    matches = {q: [x for x in recs if f(norm(x))] for q, f in BULK_QUERIES.items()}
    out += [f"| {q} | {len(m)} |" for q, m in matches.items()]
    out.append("")
    for q, m in matches.items():
        out.append(f"- **{q}**: " + ("; ".join(f"“{x['title'] or x['url']}”" for x in m[:5]) or "no matches"))
    out += [
        "",
        f"### Download estimate, {SCAN_START} → today",
        f"- GKG files since {SCAN_START}: **{est_files:,}** "
        f"(a gap-free 15-min series would be ~{((date.today() - SCAN_START).days + 1) * 96:,})",
        f"- Exact, from master-list sizes: **{sum(since) / 1e9:.1f} GB compressed** "
        f"(≈{sum(since) * ratio / 1e9:.0f} GB uncompressed at this file's ratio); "
        f"median file {sorted(since)[len(since) // 2] / 1e6:.1f} MB",
        f"- Extrapolated from this one file: {est_from_one / 1e9:.1f} GB compressed",
        "",
    ]
    return out


# ---------- Phase 4: order book ----------

OB_PREFIX = "data/futures/um/{freq}/{name}/BTCUSDT/"


def s3_objects(prefix: str) -> list[tuple[str, int]]:
    """(file name, size bytes) of every object under prefix (paginated S3 listing; no data downloaded)."""
    out, marker = [], ""
    while True:
        q = {"delimiter": "/", "prefix": prefix, "max-keys": 1000, **({"marker": marker} if marker else {})}
        xml = get(f"{S3_LIST}?{urllib.parse.urlencode(q)}").decode()
        for key, size in re.findall(r"<Key>([^<]*)</Key>.*?<Size>(\d+)</Size>", xml, re.S):
            out.append((key.rsplit("/", 1)[-1], int(size)))
        m = re.search(r"<NextMarker>([^<]*)</NextMarker>", xml)
        if "<IsTruncated>true</IsTruncated>" not in xml or not m:
            return out
        marker = m.group(1)


def period_of(fname: str) -> str:
    return re.search(r"(\d{4}-\d{2}(?:-\d{2})?)\.zip$", fname).group(1)


def describe_file(name: str, fname: str, blob: bytes) -> list[str]:
    rows = unzip_csv(blob)
    header, body = rows[0], rows[1:]
    tcol = next(i for i, c in enumerate(header) if "time" in c)
    ts = [epoch_to_dt(r[tcol]) if r[tcol].isdigit()  # bookDepth: 'YYYY-mm-dd HH:MM:SS' (UTC)
          else datetime.fromisoformat(r[tcol]).replace(tzinfo=UTC) for r in body]
    uniq = sorted(set(ts))
    gaps = Counter((b - a).total_seconds() for a, b in zip(uniq, uniq[1:]))
    lines = [
        f"### {name}: `{fname}` (downloaded; {len(blob) / 1e6:.1f} MB zipped)",
        "",
        f"- Columns: `{', '.join(header)}`",
        f"- Rows: {len(body):,}; unique `{header[tcol]}` values: {len(uniq):,}; "
        f"range {uniq[0]:%Y-%m-%d %H:%M:%S} → {uniq[-1]:%Y-%m-%d %H:%M:%S} UTC",
        "- Spacing between unique timestamps (seconds: count, top 5): "
        + ", ".join(f"{k:g}: {v:,}" for k, v in gaps.most_common(5))
        + f"; max gap {max(gaps):g} s",
        f"- First rows: `{body[0]}`, `{body[1]}`",
    ]
    if name == "bookDepth":
        pct = Counter(r[header.index("percentage")] for r in body)
        per_snap = Counter(Counter(r[tcol] for r in body).values())
        lines += [
            "- Percentage bands (rows each): " + ", ".join(f"{k}: {v:,}" for k, v in
                                                       sorted(pct.items(), key=lambda kv: float(kv[0]))),
            "- Rows per snapshot (rows: snapshots): " + ", ".join(f"{k}: {v:,}" for k, v in per_snap.most_common()),
        ]
    return lines


def check_orderbook() -> list[str]:
    today = date.today()
    out = [
        f"## Phase 4: order book feasibility, BTCUSDT USD-M ({today})",
        "",
        "Source: `data.binance.vision`, from the public S3 listing (file names and sizes; no data downloaded) "
        "plus **one** recent daily file per dataset. The missing-day scan uses the listing only: presence of "
        "each `.zip` and its `.CHECKSUM`, not row contents (that would mean downloading everything).",
        "",
        "| dataset | files | first | last | missing in range | pending (last 3 days) | total size | "
        "median file | smallest file |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    detail = []
    for name, freq in (("bookDepth", "daily"), ("bookTicker", "daily"), ("bookTicker", "monthly")):
        listing = s3_objects(OB_PREFIX.format(freq=freq, name=name))
        objs = sorted(o for o in listing if o[0].endswith(".zip"))
        sums = {f.removesuffix(".CHECKSUM") for f, _ in listing if f.endswith(".zip.CHECKSUM")}
        if not objs:
            out.append(f"| {name} {freq} | 0 | – | – | – | – | – | – | – |")
            continue
        periods = [period_of(f) for f, _ in objs]
        sizes = sorted(s for _, s in objs)
        first, last = periods[0], periods[-1]
        if freq == "daily":
            d0 = date.fromisoformat(first)
            expected = [(d0 + timedelta(n)).isoformat() for n in range((today - d0).days + 1)]
        else:
            expected = sorted({d.strftime("%Y-%m") for d in (date.fromisoformat(first + "-01") + timedelta(n)
                               for n in range((today - date.fromisoformat(first + "-01")).days + 1))})
        have = set(periods)
        absent = [p for p in expected if p not in have]
        pending = [p for p in absent if is_pending(p, today)]
        after = [p for p in absent if p > last and p not in pending]  # stopped publishing, not gaps
        missing = [p for p in absent if p not in pending and p not in after]
        small = min(objs, key=lambda o: o[1])
        median = sizes[len(sizes) // 2]
        tiny = [(period_of(f), sz) for f, sz in objs if sz < 0.1 * median]
        no_sum = [period_of(f) for f, _ in objs if f not in sums]
        out.append(
            f"| {name} {freq} | {len(objs):,} | {first} | {last} | {len(missing)} "
            f"| {len(pending) if not after else f'– (none after {last})'} "
            f"| {sum(sizes) / 1e9:.2f} GB | {sizes[len(sizes) // 2] / 1e6:.1f} MB "
            f"| {small[1] / 1e6:.2f} MB ({period_of(small[0])}) |"
        )
        miss_txt = ", ".join(ranges(missing) if freq == "daily" else missing) if missing else "none"
        detail += [
            f"### {name} {freq}: missing / pending", "",
            f"- Missing between first and last file: {miss_txt}",
            f"- Pending: {', '.join(pending) or 'none'}" if not after else
            f"- **No files after {last}** ({len(after) + len(pending)} {'days' if freq == 'daily' else 'months'} "
            "to today): the dataset is no longer published, not a gap.",
            "- Files under 10% of the median size: "
            + (", ".join(f"{p} ({sz:,} bytes)" for p, sz in tiny) or "none"),
            f"- Files without a `.CHECKSUM`: {', '.join(no_sum) or 'none'}", "",
        ]
        if freq == "daily":
            fname = objs[-1][0]
            blob = get(f"{VISION}/futures/um/daily/{name}/BTCUSDT/{fname}")
            detail += describe_file(name, fname, blob) + [""]
    return out + [""] + detail


CHECKS = {
    "history_depth": check_history_depth,
    "binance_day": lambda: check_binance_day(str(date.today() - timedelta(days=3))),
    "binance_scan": check_binance_scan,
    "gdelt_week": check_gdelt_week,
    "gdelt_volume": check_gdelt_volume,
    "gdelt_buckets": check_gdelt_buckets,
    "gdelt_bulk": check_gdelt_bulk,
    "orderbook": check_orderbook,
}


def main() -> None:
    for name in sys.argv[1:] or list(CHECKS):
        print(f"== {name}", flush=True)
        try:
            lines = CHECKS[name]()
        except Exception as e:  # record failures as findings instead of aborting
            lines = [f"## {name} — FAILED", "", f"- `{type(e).__name__}: {e}`"]
        write_section(name, lines)
        print("\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
