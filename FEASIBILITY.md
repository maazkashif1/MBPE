# MBPE feasibility

<!-- summary -->
## Go / no-go summary (written by hand; the script never touches this section)

**Verdict: GO for Phase 1.** News backfill via BigQuery fits inside the free tier.

| Area | Result | Status |
|---|---|---|
| Futures 1m candles (labels) | 2246/2246 days, no gaps, no duplicates | ✅ clean |
| Funding (monthly) | 73/73 months clean | ✅ clean |
| Metrics (OI, ratios, 5-min) | Starts **2020-09-01**, not 2020-01. Duplicates Sep 2020–May 2021; 71 short days (634 rows), mostly 2021 | ✅ manageable: dedupe + gap rules in SCOPE.md |
| Spot 1m candles | 10 short days (2020–2023), gaps ≤ 4h45m | ✅ manageable: gap rules |
| Holdout window (last 6 months) | No flagged days in any dataset | ✅ clean |
| News: GDELT DOC search API | 250-article cap per call; `bitcoin` > 250/day on 1318 days; IP throttled after a few dozen calls | ❌ rejected as backfill source |
| News: GDELT GKG bulk files | From 2015-02-18; title (`Extras`), tone, 15-min `DATE` usable as `available_at`; ~2% batches missing; 1.13 TB compressed since 2020-09 | ✅ source chosen |
| News backfill route | BigQuery dry run, `gdelt-bq.gdeltv2.gkg_partitioned`, columns DATE, SourceCommonName, DocumentIdentifier, V2Tone, Extras: **6.92 GB for Jan 2024** → ≈ 512 GB for Sep 2020–Oct 2026 (×74). Under the 1 TB/month free allowance, so one month's quota covers it with ~2× headroom for busier months | ✅ BigQuery chosen; cloud VM is the fallback |

Not completed: the GDELT bucket-size probe (`gdelt_buckets`). It was stopped when the search API throttled this machine, and it's no longer needed because the search API is out.

BigQuery caveats for Phase 1:
- Every run of the full-range query costs the full scan again, so run it once, filter in SQL, and save the matched rows locally.
- Adding `V2Themes` for theme matching needs its own dry run. Themes is a large column and could add a lot to the scan.
- Jan 2024 is one sample month. Before the real run, dry-run the exact full-range query to confirm the total.
<!-- /summary -->

<!-- history_depth -->
## history_depth — FAILED

_Generated 2026-10-26 14:43 UTC by `feasibility.py history_depth`_

- `HTTPError: HTTP Error 429: Too Many Requests`
<!-- /history_depth -->

<!-- binance_day -->
## Binance single-day sample — BTCUSDT USD-M, 2026-10-23

_Generated 2026-10-26 14:43 UTC by `feasibility.py binance_day`_

### Metrics
- URL: `https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2026-10-23.zip`
- Rows: **288**
- Columns: `create_time, symbol, sum_open_interest, sum_open_interest_value, count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio, count_long_short_ratio, sum_taker_long_short_vol_ratio`
- Range: 2026-10-23 00:00:00 → 2026-10-23 23:55:00
- Time steps (step: count): 0:05:00: 287

### 1m klines
- URL: `https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10-23.zip`
- Rows: **1440** (expected 1440)
- Range: 2026-10-23 00:00:00+00:00 → 2026-10-23 23:59:00+00:00
- Time steps (step: count): 0:01:00: 1439

<!-- /binance_day -->

<!-- gdelt_week -->
## GDELT one-week sample — `bitcoin`, 2026-10-19 14:00 → 2026-10-26 14:00 UTC

_Generated 2026-10-26 14:44 UTC by `feasibility.py gdelt_week`_

### ArtList
- Articles returned: **250** (one ArtList call returns at most 250)
- Fields: `domain, language, seendate, socialimage, sourcecountry, title, url, url_mobile`
- Timestamp field: `seendate` only (when GDELT first saw the article, 15-min resolution); range 20261019T140000Z → 20261026T071500Z

### TimelineVolRaw (true volume, not capped)
- Total matching articles in window: **767**
- Buckets: 133, e.g. `20261019T140000Z` → `20261019T150000Z`

<!-- /gdelt_week -->

<!-- gdelt_volume -->
## GDELT daily volume — 2020-09-01 → 2026-10-26

_Generated 2026-10-26 15:25 UTC by `feasibility.py gdelt_volume`_

`timelinevolraw`, counts summed per UTC day. Days over 250 need more than one ArtList call per day to backfill.

| query | bucket size | days with data | total articles | max daily count (date) | median daily | days > 250 |
|---|---|---|---|---|---|---|
| `bitcoin sourcelang:english` | 1 day, 0:00:00 | 2204 | 804616 | 1955 (2021-05-13) | 305 | 1318 |
| `crypto SEC sourcelang:english` | 1 day, 0:00:00 | 2187 | 66893 | 988 (2022-12-13) | 23 | 10 |
| `"Federal Reserve" sourcelang:english` | 1 day, 0:00:00 | 2207 | 1358658 | 3820 (2023-03-13) | 467 | 1662 |
| `stablecoin sourcelang:english` | 1 day, 0:00:00 | 2193 | 69621 | 482 (2022-05-13) | 26 | 3 |

<!-- /gdelt_volume -->

<!-- binance_scan -->
## Binance archive scan — BTCUSDT, 2020-09-01 → 2026-10-26

_Generated 2026-10-26 16:05 UTC by `feasibility.py binance_scan`_

Per-file row counts for all 6815 files: [`feasibility_binance_files.csv`](feasibility_binance_files.csv).

- `pending` = not published yet. Applies to daily files from the last 3 days and the current month's funding file. Not counted as missing.
- `short` = fewer unique in-period timestamps than expected.
- `dupes` = repeated timestamps.
- `extra` = rows that belong to another day/month.
- `uneven` = in-period timestamps not on an exact 5-min (metrics) / 1-min (klines) grid: a gap, an off-grid timestamp, or tighter spacing. Checked even when the row count is right. Funding has no spacing check (calc_time has ms jitter).
- One file can carry several flags, e.g. `short+uneven`.

| dataset | files | present | ok | missing | pending | short | dupes | extra | uneven | error | common row counts |
|---|---|---|---|---|---|---|---|---|---|---|---|
| um_metrics | 2247 | 2246 | 1938 | 0 | 1 | 71 | 263 | 0 | 69 | 0 | 288×1938, 576×237, 287×18 |
| um_klines_1m | 2247 | 2246 | 2246 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1440×2246 |
| spot_klines_1m | 2247 | 2246 | 2236 | 0 | 1 | 10 | 0 | 0 | 10 | 0 | 1440×2236, 1380×2, 1188×1 |
| um_funding_monthly | 74 | 73 | 73 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 93×42, 90×25, 84×5 |

### um_metrics

- Missing: none
- Pending: 2026-10-26
- Flagged:
  - 2020-09-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-05: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-07: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-10: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-11: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-12: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-14: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-15: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-16: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-18: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-19: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-20: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-21: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-22: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-23: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-24: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-25: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-26: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-27: **short+dupes+uneven** rows=568, in-period=568, unique=284, expected=288, max gap=0:25:00
  - 2020-09-28: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-29: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-09-30: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-05: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-07: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-10: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-11: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-12: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-14: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-15: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-16: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-18: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-19: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-20: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-21: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-22: **short+dupes+uneven** rows=556, in-period=556, unique=278, expected=288, max gap=0:55:00
  - 2020-10-23: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-24: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-25: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-26: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-27: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-28: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-29: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-30: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-10-31: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-05: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-07: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-10: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-11: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-12: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-14: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-15: **short+dupes+uneven** rows=554, in-period=554, unique=277, expected=288, max gap=1:00:00
  - 2020-11-16: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-18: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-19: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-20: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-21: **short+dupes+uneven** rows=560, in-period=560, unique=280, expected=288, max gap=0:45:00
  - 2020-11-22: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-23: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-24: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-25: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-26: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-27: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-28: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-29: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-11-30: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-05: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-07: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-10: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-11: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-12: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-14: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-15: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-16: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-18: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-19: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-20: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-21: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-22: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-23: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-24: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-25: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-26: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-27: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-28: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-29: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-30: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2020-12-31: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-05: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-07: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-10: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-11: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-12: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-14: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-15: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-16: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-18: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-19: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-20: **short+dupes+uneven** rows=542, in-period=542, unique=271, expected=288, max gap=1:30:00
  - 2021-01-21: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-22: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-23: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-24: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-25: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-26: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-27: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-28: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-29: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-30: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-01-31: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-05: **short+dupes+uneven** rows=558, in-period=558, unique=279, expected=288, max gap=0:50:00
  - 2021-02-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-07: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-02-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-10: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-02-11: **short+dupes+uneven** rows=552, in-period=552, unique=276, expected=288, max gap=0:15:00
  - 2021-02-12: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-14: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-15: **short+dupes+uneven** rows=570, in-period=570, unique=285, expected=288, max gap=0:15:00
  - 2021-02-16: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-02-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-18: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-02-19: **short+dupes+uneven** rows=436, in-period=436, unique=218, expected=288, max gap=1:05:00
  - 2021-02-20: **short+dupes** rows=544, in-period=544, unique=272, expected=288, max gap=0:05:00
  - 2021-02-21: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-22: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-23: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-24: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-02-25: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-26: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-27: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-02-28: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-03-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-05: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-03-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-07: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-10: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-03-11: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-12: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-03-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-14: **short+dupes+uneven** rows=570, in-period=570, unique=285, expected=288, max gap=0:15:00
  - 2021-03-15: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-16: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-18: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-19: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-20: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-21: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-22: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-23: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-24: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-03-25: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-26: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-27: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-28: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-29: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-30: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-03-31: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-05: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-07: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-10: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-11: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-12: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-14: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-15: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-16: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-18: **short+dupes+uneven** rows=574, in-period=574, unique=287, expected=288, max gap=0:10:00
  - 2021-04-19: **short+dupes+uneven** rows=572, in-period=572, unique=286, expected=288, max gap=0:15:00
  - 2021-04-20: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-21: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-22: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-23: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-24: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-25: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-26: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-27: **short+dupes+uneven** rows=516, in-period=516, unique=258, expected=288, max gap=2:35:00
  - 2021-04-28: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-29: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-04-30: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-01: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-02: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-03: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-04: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-05: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-06: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-07: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-08: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-09: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-10: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-11: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-12: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-13: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-14: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-15: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-16: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-17: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-18: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-19: **short+dupes+uneven** rows=570, in-period=570, unique=285, expected=288, max gap=0:15:00
  - 2021-05-20: **dupes** rows=576, in-period=576, unique=288, expected=288, max gap=0:05:00
  - 2021-05-21: **short+dupes+uneven** rows=292, in-period=292, unique=284, expected=288, max gap=0:10:00
  - 2021-05-23: **short+uneven** rows=284, in-period=284, unique=284, expected=288, max gap=0:10:00
  - 2021-05-28: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-06-22: **short+uneven** rows=126, in-period=126, unique=126, expected=288, max gap=4:25:00
  - 2021-06-23: **short+uneven** rows=275, in-period=275, unique=275, expected=288, max gap=1:05:00
  - 2021-06-24: **short+uneven** rows=279, in-period=279, unique=279, expected=288, max gap=0:30:00
  - 2021-08-24: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-09-06: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-09-07: **short+uneven** rows=284, in-period=284, unique=284, expected=288, max gap=0:10:00
  - 2021-09-10: **short+uneven** rows=286, in-period=286, unique=286, expected=288, max gap=0:10:00
  - 2021-09-13: **short+uneven** rows=286, in-period=286, unique=286, expected=288, max gap=0:10:00
  - 2021-09-19: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-09-20: **short+uneven** rows=281, in-period=281, unique=281, expected=288, max gap=0:10:00
  - 2021-09-21: **short+uneven** rows=283, in-period=283, unique=283, expected=288, max gap=0:15:00
  - 2021-09-22: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-09-24: **short+uneven** rows=285, in-period=285, unique=285, expected=288, max gap=0:10:00
  - 2021-09-25: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-09-26: **short+uneven** rows=284, in-period=284, unique=284, expected=288, max gap=0:10:00
  - 2021-10-01: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-10-02: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-10-17: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-10-21: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-10-22: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-10-27: **short+uneven** rows=280, in-period=280, unique=280, expected=288, max gap=0:30:00
  - 2021-10-28: **short+uneven** rows=286, in-period=286, unique=286, expected=288, max gap=0:10:00
  - 2021-10-30: **short+uneven** rows=283, in-period=283, unique=283, expected=288, max gap=0:10:00
  - 2021-10-31: **short+uneven** rows=286, in-period=286, unique=286, expected=288, max gap=0:10:00
  - 2021-11-10: **short+uneven** rows=285, in-period=285, unique=285, expected=288, max gap=0:15:00
  - 2021-11-16: **short+uneven** rows=281, in-period=281, unique=281, expected=288, max gap=0:25:00
  - 2021-11-18: **short+uneven** rows=283, in-period=283, unique=283, expected=288, max gap=0:20:00
  - 2021-11-19: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-11-22: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-11-25: **short+uneven** rows=286, in-period=286, unique=286, expected=288, max gap=0:10:00
  - 2021-11-26: **short+uneven** rows=276, in-period=276, unique=276, expected=288, max gap=0:40:00
  - 2021-11-27: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-11-28: **short+uneven** rows=286, in-period=286, unique=286, expected=288, max gap=0:10:00
  - 2021-11-30: **short+uneven** rows=284, in-period=284, unique=284, expected=288, max gap=0:15:00
  - 2021-12-04: **short+uneven** rows=285, in-period=285, unique=285, expected=288, max gap=0:15:00
  - 2021-12-15: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-12-24: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-12-29: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2021-12-31: **short+uneven** rows=287, in-period=287, unique=287, expected=288, max gap=0:10:00
  - 2023-09-12: **short+uneven** rows=285, in-period=285, unique=285, expected=288, max gap=0:20:00
  - 2024-02-16: **short** rows=163, in-period=163, unique=163, expected=288, max gap=0:05:00
  - 2024-10-28: **short+uneven** rows=286, in-period=286, unique=286, expected=288, max gap=0:15:00
  - 2025-08-29: **short+uneven** rows=285, in-period=285, unique=285, expected=288, max gap=0:20:00

### um_klines_1m

- Missing: none
- Pending: 2026-10-26
- Flagged: none

### spot_klines_1m

- Missing: none
- Pending: 2026-10-26
- Flagged:
  - 2020-11-30: **short+uneven** rows=1380, in-period=1380, unique=1380, expected=1440, max gap=1:01:00
  - 2020-12-21: **short+uneven** rows=1188, in-period=1188, unique=1188, expected=1440, max gap=4:13:00
  - 2020-12-25: **short+uneven** rows=1380, in-period=1380, unique=1380, expected=1440, max gap=1:01:00
  - 2021-02-11: **short+uneven** rows=1361, in-period=1361, unique=1361, expected=1440, max gap=1:20:00
  - 2021-03-06: **short+uneven** rows=1350, in-period=1350, unique=1350, expected=1440, max gap=1:31:00
  - 2021-04-20: **short+uneven** rows=1290, in-period=1290, unique=1290, expected=1440, max gap=2:31:00
  - 2021-04-25: **short+uneven** rows=1156, in-period=1156, unique=1156, expected=1440, max gap=4:45:00
  - 2021-08-13: **short+uneven** rows=1170, in-period=1170, unique=1170, expected=1440, max gap=4:31:00
  - 2021-09-29: **short+uneven** rows=1320, in-period=1320, unique=1320, expected=1440, max gap=2:01:00
  - 2023-03-24: **short+uneven** rows=1360, in-period=1360, unique=1360, expected=1440, max gap=1:21:00

### um_funding_monthly

- Missing: none
- Pending: 2026-10
- Flagged: none

<!-- /binance_scan -->

<!-- gdelt_bulk -->
## GDELT bulk files (GKG 2.1)

_Generated 2026-10-26 17:03 UTC by `feasibility.py gdelt_bulk`_

Bulk download server `data.gdeltproject.org` only; no search-API requests. Downloaded: `lastupdate.txt`, `masterfilelist.txt` (index files) and **one** GKG data file.

### Master list
- GKG files listed: 402,419; **earliest GKG file: `20150218230000`** (2015-02-18 23:00 UTC); latest: `20261026170000`
- Naming: `http://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.gkg.csv.zip`, one per 15 minutes (timestamp = end of the 15-min batch, UTC). Each master-list line is `<size bytes> <md5> <url>`. Same folder holds `.export.CSV.zip` (events) and `.mentions.CSV.zip`. Non-English sources are in separate `.translation.gkg.csv.zip` files (`masterfilelist-translation.txt`), not counted here.

### Sample file: `20261026170000.gkg.csv.zip`
- Compressed: 6.78 MB (lastupdate says 6.78 MB); uncompressed: 20.92 MB (×3.1)
- Rows: **1,583** (one per article); tab-separated, no header; columns per row: 27×1583
- Columns (GKG 2.1 codebook order): `GKGRECORDID, V2.1DATE, V2SOURCECOLLECTIONIDENTIFIER, V2SOURCECOMMONNAME, V2DOCUMENTIDENTIFIER, V1COUNTS, V2.1COUNTS, V1THEMES, V2ENHANCEDTHEMES, V1LOCATIONS, V2ENHANCEDLOCATIONS, V1PERSONS, V2ENHANCEDPERSONS, V1ORGANIZATIONS, V2ENHANCEDORGANIZATIONS, V1.5TONE, V2.1ENHANCEDDATES, V2GCAM, V2.1SHARINGIMAGE, V2.1RELATEDIMAGES, V2.1SOCIALIMAGEEMBEDS, V2.1SOCIALVIDEOEMBEDS, V2.1QUOTATIONS, V2.1ALLNAMES, V2.1AMOUNTS, V2.1TRANSLATIONINFO, V2EXTRASXML`
- **Page title:** yes. `V2EXTRASXML` holds `<PAGE_TITLE>…</PAGE_TITLE>` for 1,581 of 1,583 rows (100%).
- `V2.1DATE` = the 15-min batch time the article entered GDELT, usable as `available_at`. Tone = first field of `V1.5TONE`.

Example rows:

| date | source | url | tone | title |
|---|---|---|---|---|
| 20261026170000 | eastbaytimes.com | https://www.eastbaytimes.com/2026/10/26/sober-living-home-where-cindy-crawfords-son-died-not-operating-outside-of-california-law/ | -1.09170305676856 | Sober living home where Cindy Crawford's son died not operating outside of California law – East Bay Times |
| 20261026170000 | stgeorgeutah.com | https://www.stgeorgeutah.com/life/before-you-open-your-halloween-candy-haul-read-the-fine-print/article_5bd163d9-0257-4db8-9f74-ecfbcce9f610.html | -0.497100248550125 | Before you open your Halloween candy haul, read the fine print |
| 20261026170000 | ianslive.in | https://ianslive.in/sarbananda-sonowal-invites-south-korean-firms-to-build-indias-shipyards--20261026220433 | 1.8140589569161 | Sarbananda Sonowal invites South Korean firms to build India's shipyards |

### Query matches in this one file (case-insensitive, title or URL)

| query | rows |
|---|---|
| bitcoin | 2 |
| crypto + SEC | 0 |
| stablecoin | 0 |
| Federal Reserve | 0 |

- **bitcoin**: “Baystreet.ca - Bitcoin Falls Below Key Support Level of $80,000”; “Crypto SWOT: U.S. spot Bitcoin ETFs recorded $1.08 billion in net inflows over three days”
- **crypto + SEC**: no matches
- **stablecoin**: no matches
- **Federal Reserve**: no matches

### Download estimate, 2020-09-01 → today
- GKG files since 2020-09-01: **210,641** (a gap-free 15-min series would be ~215,712)
- Exact, from master-list sizes: **1130.0 GB compressed** (≈3486 GB uncompressed at this file's ratio); median file 5.0 MB
- Extrapolated from this one file: 1428.2 GB compressed

<!-- /gdelt_bulk -->

# Phase 4

<!-- phase4_summary -->
## Phase 4 summary (written by hand, 2026-10-28; the script never touches this section)

| Area | Result | Status |
|---|---|---|
| Order book depth (`bookDepth`, daily) | From **2023-01-01**. Snapshots every ~30 s with 12 bands (±0.2, ±1, ±2, ±3, ±4, ±5% from mid), cumulative depth (BTC) and notional (USD). 4 missing days in range and 5 near-empty files (< 2 KB). Full download ≈ **0.66 GB** | ✅ usable from 2023 |
| Best bid/ask (`bookTicker`) | Only **2023-05-16 → 2024-03-30** (daily), monthly to 2024-04 (partial). **Not published since**, so it can't feed forward data or the Phase 4 folds after Mar 2024. ~53 GB for that short span | ❌ excluded |
| NFP calendar | 73 releases (Aug 2020 → Sep 2026 reference months) + 1 cancelled (Oct 2025) + 2 scheduled, added to `data/reference/macro_events.csv`. Phase 3 features and blackout are unchanged (CPI + FOMC only; tested) | ✅ done |
| Reddit live collector | `mbpe/sources/reddit_live.py` written and tested offline (fake client). **Not started**: waiting for your credentials | ⏸ waiting on you |

**Decided at review (2026-10-28):** bookTicker is excluded, bookDepth only. bookDepth files under 2 KB count as missing days. The rest is pre-registered in SCOPE.md ("Phase 4 pre-registration").

Details for each are in the sections below.
<!-- /phase4_summary -->

<!-- orderbook -->
## Phase 4: order book feasibility, BTCUSDT USD-M (2026-10-28)

_Generated 2026-10-28 15:29 UTC by `feasibility.py orderbook`_

Source: `data.binance.vision`, from the public S3 listing (file names and sizes; no data downloaded) plus **one** recent daily file per dataset. The missing-day scan uses the listing only: presence of each `.zip` and its `.CHECKSUM`, not row contents (that would mean downloading everything).

| dataset | files | first | last | missing in range | pending (last 3 days) | total size | median file | smallest file |
|---|---|---|---|---|---|---|---|---|
| bookDepth daily | 1,392 | 2023-01-01 | 2026-10-27 | 4 | 1 | 0.66 GB | 0.5 MB | 0.00 MB (2024-06-12) |
| bookTicker daily | 320 | 2023-05-16 | 2024-03-30 | 0 | – (none after 2024-03-30) | 52.58 GB | 146.0 MB | 47.82 MB (2023-09-09) |
| bookTicker monthly | 12 | 2023-05 | 2024-04 | 0 | – (none after 2024-04) | 53.55 GB | 4444.8 MB | 37.76 MB (2024-04) |

### bookDepth daily: missing / pending

- Missing between first and last file: 2023-02-08 → 2023-02-09 (2 days), 2024-04-18, 2026-10-08
- Pending: 2026-10-28
- Files under 10% of the median size: 2023-01-09 (1,645 bytes), 2023-01-15 (1,306 bytes), 2023-01-16 (1,838 bytes), 2023-01-21 (788 bytes), 2024-06-12 (613 bytes)
- Files without a `.CHECKSUM`: none

### bookDepth: `BTCUSDT-bookDepth-2026-10-27.zip` (downloaded; 0.6 MB zipped)

- Columns: `timestamp, percentage, depth, notional`
- Rows: 34,560; unique `timestamp` values: 2,880; range 2026-10-27 00:00:01 → 2026-10-27 23:59:30 UTC
- Spacing between unique timestamps (seconds: count, top 5): 30: 1,559, 29: 562, 31: 555, 32: 69, 28: 67; max gap 35 s
- First rows: `['2026-10-27 00:00:01', '-5.00', '11332.45600000', '892102221.36810000']`, `['2026-10-27 00:00:01', '-4.00', '10599.72200000', '836015564.65600000']`
- Percentage bands (rows each): -5.00: 2,880, -4.00: 2,880, -3.00: 2,880, -2.00: 2,880, -1.00: 2,880, -0.20: 2,880, 0.20: 2,880, 1.00: 2,880, 2.00: 2,880, 3.00: 2,880, 4.00: 2,880, 5.00: 2,880
- Rows per snapshot (rows: snapshots): 12: 2,880

### bookTicker daily: missing / pending

- Missing between first and last file: none
- **No files after 2024-03-30** (942 days to today): the dataset is no longer published, not a gap.
- Files under 10% of the median size: none
- Files without a `.CHECKSUM`: none

### bookTicker: `BTCUSDT-bookTicker-2024-03-30.zip` (downloaded; 87.8 MB zipped)

- Columns: `update_id, best_bid_price, best_bid_qty, best_ask_price, best_ask_qty, transaction_time, event_time`
- Rows: 7,398,592; unique `transaction_time` values: 4,666,190; range 2024-03-30 00:00:00 → 2024-03-30 23:59:59 UTC
- Spacing between unique timestamps (seconds: count, top 5): 0.001: 1,173,987, 0.002: 552,038, 0.003: 427,820, 0.004: 340,434, 0.005: 189,355; max gap 1.741 s
- First rows: `['4307082974693', '69903.60000000', '0.46200000', '69903.70000000', '4.41900000', '1711756800002', '1711756800008']`, `['4307082974976', '69903.60000000', '0.36200000', '69903.70000000', '4.41900000', '1711756800005', '1711756800011']`

### bookTicker monthly: missing / pending

- Missing between first and last file: none
- **No files after 2024-04** (30 months to today): the dataset is no longer published, not a gap.
- Files under 10% of the median size: 2024-04 (37,761,761 bytes)
- Files without a `.CHECKSUM`: none

<!-- /orderbook -->

## Phase 4: NFP calendar (US Employment Situation), written by hand 2026-10-28

Added to `data/reference/macro_events.csv` as `event = NFP`, in the same format as the CPI/FOMC rows:
- **What was added:** 76 rows. 73 were released (reference months Aug 2020 → Sep 2026), 1 was cancelled (Oct 2025) and 2 are scheduled (Oct and Nov 2026; Dec 2026's release falls on Jan 8, 2027).
- **Release times:** 08:30 ET for every release, converted to UTC with US daylight saving (12:30 or 13:30 UTC).
- **Rescheduled or cancelled** (from BLS's revised release dates page):
  - Sep 2025: Oct 3 → **Nov 20, 2025** (2025 funding lapse);
  - **Oct 2025: not published.** October establishment-survey data came out with the November release; household-survey data were never collected;
  - Nov 2025: Dec 5 → **Dec 16, 2025**;
  - Jan 2026: Feb 6 → **Feb 11, 2026** (2026 lapse in appropriations).
- **Sources:**
  - BLS Employment Situation archive, https://www.bls.gov/bls/news-release/empsit.htm. Release dates come from the archived release file names (`empsit_MMDDYYYY.htm`), read twice independently with matching results;
  - BLS revised release dates for the funding lapses, https://www.bls.gov/bls/2025-lapse-revised-release-dates.htm. It also gives 08:30 ET as the time for each affected release;
  - BLS release schedule, https://www.bls.gov/schedule/news_release/empsit.htm (scheduled rows; 08:30 AM).
- **The Phase 3 calendar is unchanged:** `load_calendar()` keeps only CPI and FOMC rows. It returns the same 126 events, byte-identical, before and after. `tests/test_news_features.py::test_phase3_calendar_ignores_other_events` pins this.
- **Same limitation as CPI:** actual release times are used, so the backtest knows the rescheduled dates (Nov 2025, Dec 2025, Feb 2026) earlier than the market did.

## Phase 4: Reddit live collector, setup only (written by hand 2026-10-28)

- **Code:** `mbpe/sources/reddit_live.py`, using the official Reddit API through PRAW 8 (added as a dependency). It is read-only, application-only access: a "script" app's client ID and secret, with no username or password.
- **Per run** (schedule every 15 min; example in `deploy/reddit-live.cron`, not installed):
  - the 100 newest posts of r/Bitcoin and r/CryptoCurrency;
  - stored fields: `post_id, subreddit, title, created_utc, fetched_at`. No scores, comment counts or other engagement data;
  - output: `data/raw/reddit_live/<date>/<HHMM>.parquet`, plus a `.json` with run metadata.
- **Availability:** a post's first `fetched_at` is when it became available to us; `load_posts()` keeps the first sighting. `possible_gap` in the run metadata flags runs where a full page of 100 posts is entirely newer than the previous run, meaning posts may have been missed.
- **Credentials:** read only from the `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` and `REDDIT_USER_AGENT` environment variables, never from repo files. `.gitignore` already covers `.env*` and credential files. The cron example sources an env file outside the repo (`~/.config/mbpe/reddit.env`).
- **Tests:** `tests/test_reddit_live.py` (3, offline): only the static fields are stored, the first sighting is kept, a gap is flagged, and missing credentials raise.
- **Not started.** It will run once you confirm the credentials are set. The first live run is also the first check against the real API (rate limits, field names).
- **Reddit's terms:** the API is free for non-commercial research at low rates, but the Data API terms apply. Collected titles should stay local (`data/` is git-ignored), as GDELT data does.
