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
