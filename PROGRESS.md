# MBPE progress

## Phase 1 step 1: Binance ingestion (2026-10-26)

`uv run python -m mbpe.ingest` downloads raw files (resumable, SHA-256 checked against Binance's `.CHECKSUM` files), then rebuilds the clean Parquet layer. Range: 2020-09-01 → 2026-10-25, the last published day; today's files and October's funding file aren't out yet.

| Dataset | Raw files | Clean rows | Range (UTC) | Gaps | Missing rows | Matches FEASIBILITY.md |
|---|---|---|---|---|---|---|
| futures_klines_1m | 2246 | 3,234,240 | 2020-09-01 00:00 → 2026-10-25 23:59 | 0 | 0 | ✅ (0 flagged days) |
| spot_klines_1m | 2246 | 3,232,795 | 2020-09-01 00:00 → 2026-10-25 23:59 | 10 | 1,445 | ✅ (10 short days, 1,445 missing) |
| funding | 73 | 6,663 | 2020-09-01 00:00 → 2026-09-30 16:00 | 0 | 0 | ✅ |
| metrics | 2246 | 646,214 | 2020-09-01 00:00 → 2026-10-25 23:55 | 156 | 634 | ✅ (634 missing rows) |

- **Gaps:** `gap_report()` returns one row per break in the timestamp series (start = last row before, end = first row after). The longest are:
  - metrics 2024-02-16 13:30 → 2024-02-17 00:00 (10h30m);
  - spot 2021-04-25 04:00 → 08:45 (4h45m).

  The metrics count is 156 gaps against 71 "short days" in FEASIBILITY.md because one day can contain several gaps.
- **Duplicates:** metrics exact duplicates from Sep 2020 to May 2021 were dropped. No conflicting duplicates in any dataset.
- **Storage:** raw 352 MB, clean 293 MB (`data/`, git-ignored).
- **Runtime:**
  - first full run: 594 s, almost all of it downloading about 6,800 files plus their checksums;
  - re-run with everything stored: 12 s (3 s rebuild per kline dataset).

### New finding: blank metric ratios (not visible in the feasibility scan)
The rows exist, but some fields are empty. The open-interest columns are complete. The ratio columns are not:

| Column | Blank rows | Main blank stretch |
|---|---|---|
| `count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio` | ~92,200 (14%) | **2021-12-30 → 2022-12-14**, almost continuous (short breaks around 2022-01-30, 2022-05-27 → 06-24, 2022-09-03) |
| `sum_taker_long_short_vol_ratio` | 37,271 | **2021-12-30 → 2022-05-09** |
| `count_long_short_ratio` | 5,797 | 2021-12-30 → 2022-01-19 |

Following "gaps stay gaps", these columns are nullable in the clean schema and the blanks are kept as nulls. `null_runs()` lists every blank stretch per column. Under the levels rule these are level features, so they will be NaN through 2022.

**Decided (SCOPE.md, "Feature exclusions"):** the top-trader ratios are excluded from MVP features, and the taker buy/sell ratio is computed from futures kline taker volume instead.

### Code
- `mbpe/sources/binance.py`: `fetch_history(dataset, start, end)` and `read_raw`/`parse_csv`. Handles both kline formats (with and without a header row) and spot's switch to µs timestamps in 2025; all times are UTC ms.
- `mbpe/storage/clean.py`: `dedupe` (raises `ConflictingDuplicatesError`), a pandera schema per dataset (strictly increasing timestamps, non-negative prices and volumes), `build_clean`, `gap_report`, `null_runs`.
- `tests/test_binance.py`: 9 tests covering duplicates, header/no-header parity, µs/ms parity, schema rejection of unsorted and duplicate timestamps, synthetic gaps and blank stretches.

## Phase 1 step 2: triple-barrier labels (2026-10-26)

`uv run python -m mbpe.labels.triple_barrier` → `data/labels/triple_barrier.parquet`. It uses only futures 1m candles and funding, with settings from `config/labels.yaml`: +2 / −1 ATR, 240 min, Wilder ATR(14) on 1h bars, taker fee 0.05%, slippage 2 bps, 1 min latency. The build runs in under 1 s.

- **Rows:** 107,772 = 53,886 decision hours × 2 sides, decision times 2020-09-01 14:00 → 2026-10-25 19:00 UTC.
- **Skipped:** 13 decision times for ATR warm-up, and 5 for missing candles. Those 5 are the last hours, whose 4h window runs past the final published candle (Oct 25 23:59), not real gaps.
- **Holding time:** 1–240 min.
- **Gap fills:** none. No stop exit filled at a gapped open, because a 1 ATR gap between consecutive 1m candles never happened.
- **Funding:** 32% of trades include a funding time.

| Year | Side | Rows | Label = 1 | TP | SL | Expiry | Avg hold (min) | Mean net_R |
|---|---|---|---|---|---|---|---|---|
| 2020 | long | 2,914 | 44.1% | 12.8% | 35.3% | 51.9% | 177 | −0.095 |
| 2020 | short | 2,914 | 35.2% | 10.4% | 38.8% | 50.7% | 177 | −0.264 |
| 2021 | long | 8,760 | 41.5% | 9.5% | 39.2% | 51.2% | 177 | −0.143 |
| 2021 | short | 8,760 | 41.5% | 12.4% | 37.2% | 50.5% | 179 | −0.097 |
| 2022 | long | 8,760 | 37.5% | 10.9% | 37.4% | 51.7% | 177 | −0.217 |
| 2022 | short | 8,760 | 38.6% | 13.1% | 35.0% | 51.9% | 178 | −0.157 |
| 2023 | long | 8,760 | 34.9% | 12.6% | 35.2% | 52.3% | 177 | −0.272 |
| 2023 | short | 8,760 | 33.8% | 12.1% | 35.3% | 52.6% | 177 | −0.298 |
| 2024 | long | 8,784 | 39.5% | 12.2% | 36.3% | 51.5% | 176 | −0.182 |
| 2024 | short | 8,784 | 36.3% | 12.4% | 37.0% | 50.7% | 176 | −0.221 |
| 2025 | long | 8,760 | 36.5% | 10.7% | 38.1% | 51.2% | 177 | −0.274 |
| 2025 | short | 8,760 | 36.1% | 12.3% | 37.1% | 50.7% | 177 | −0.263 |
| 2026 | long | 7,148 | 35.4% | 11.7% | 38.0% | 50.3% | 174 | −0.284 |
| 2026 | short | 7,148 | 35.7% | 12.5% | 37.3% | 50.3% | 174 | −0.265 |

### What the labels say before any model
- **Costs are the hurdle.** Mean gross_R is ≈ 0 for both sides (long +0.002, short −0.004), and mean fees_R is **0.218 R**. That is 14 bps round trip against a median 1h ATR of 73 bps. Funding is small (±0.004 R). A model needs roughly +0.22 R of gross edge per trade just to break even, so the always-long / random baselines will sit near −0.22 R.
- **Half the trades expire** (51%). A 2 ATR target on 1h ATR is far for a 4h window, so TP is hit only ~12% of the time. Expiries average +0.046 net_R and are label = 1 half the time, so `label` is driven largely by small expiry outcomes. Keep this in mind when reading hit rates; mean net_R is the better target to judge.
- **Label = 1 share is 34–44%** and fairly stable across years and sides, so class balance doesn't need special handling.

### Code
- `mbpe/labels/triple_barrier.py`: `LabelConfig` (pydantic, loaded from YAML), `hourly_atr`, `label_trades` (vectorised scan of the barrier window), `build_labels`, `summary`.
- `tests/test_labels.py` (15 tests):
  - TP, SL and expiry, each with an exact net_R;
  - both barriers in one candle → stop;
  - gap-through-stop fills at the open;
  - funding inside, at entry, at exit, after exit, negative, and with millisecond jitter, for both sides;
  - a missing candle skips the row;
  - leakage: candles after the exit, and ATR with candles after t;
  - short mirrors long on a flipped path.

  The suite is 24 tests in total, all passing.

## Phase 1 step 2b: pre-registered label and cost variants (2026-10-26)

The variants are registered in SCOPE.md ("Label and cost variants (pre-registered)").

**Code changes**
- Costs are now separate columns: `fee_R`, `slippage_R` and `funding_R`, next to `gross_R` and the new `exit_price`. These replace the old combined `fees_R`. `net_R` and `label` are unchanged (taker scenario); set A's means are identical to step 2.
- `scenario_net_r(labels, cfg, scenario)` recomputes net_R for `taker` or `maker_entry_tp`. It uses the same cost expression as the stored columns, and a test checks that `taker` reproduces the stored `net_R` exactly.
- The ATR timeframe is a config field (`atr_timeframe`). Each hourly decision time takes the ATR of the latest bar closed at or before t (as-of join).
- 30 tests in total. New ones:
  - the 4h ATR at t = 64h–67h uses only the bar closed at 64h, unchanged when candles from 64h on are altered, and changes once the next bar closes;
  - 4h warm-up = 56h;
  - taker scenario reproduces stored net_R exactly;
  - exact maker-scenario net_R for TP, SL and expiry;
  - unknown scenario raises.

### Label set B (4h ATR): `data/labels/triple_barrier_B.parquet`
107,688 rows; skipped 55 decision times for warm-up (14 × 4h bars) and 5 at the end of data. The median ATR is 153 bps of price, versus 73 bps for set A, so the barriers are about twice as wide.

| Year | Side | Rows | Label = 1 | TP | SL | Expiry | Avg hold (min) | Mean net_R |
|---|---|---|---|---|---|---|---|---|
| 2020 | long | 2,872 | 47.5% | 2.9% | 11.5% | 85.7% | 224 | −0.017 |
| 2020 | short | 2,872 | 36.8% | 2.7% | 12.3% | 85.0% | 224 | −0.144 |
| 2021 | long | 8,760 | 45.1% | 1.4% | 12.0% | 86.6% | 226 | −0.059 |
| 2021 | short | 8,760 | 44.5% | 2.0% | 9.4% | 88.7% | 228 | −0.045 |
| 2022 | long | 8,760 | 40.1% | 1.9% | 12.8% | 85.3% | 224 | −0.105 |
| 2022 | short | 8,760 | 41.5% | 2.1% | 10.8% | 87.1% | 226 | −0.064 |
| 2023 | long | 8,760 | 36.9% | 3.3% | 12.1% | 84.6% | 223 | −0.107 |
| 2023 | short | 8,760 | 35.9% | 2.7% | 12.5% | 84.8% | 223 | −0.139 |
| 2024 | long | 8,784 | 42.3% | 2.4% | 12.9% | 84.8% | 223 | −0.078 |
| 2024 | short | 8,784 | 38.8% | 2.8% | 12.4% | 84.8% | 224 | −0.102 |
| 2025 | long | 8,760 | 39.9% | 2.1% | 13.1% | 84.8% | 224 | −0.117 |
| 2025 | short | 8,760 | 38.9% | 2.7% | 11.4% | 85.9% | 225 | −0.112 |
| 2026 | long | 7,148 | 38.2% | 2.3% | 13.4% | 84.3% | 223 | −0.131 |
| 2026 | short | 7,148 | 38.4% | 2.2% | 12.1% | 85.7% | 225 | −0.117 |

### Baselines: mean net_R for every label set × cost scenario

| Label set | Strategy | `taker` (headline) | `maker_entry_tp` (optimistic) | gross_R | fee_R | slippage_R | funding_R |
|---|---|---|---|---|---|---|---|
| A (1h ATR) | always-long | **−0.220** | −0.132 | +0.002 | 0.156 | 0.062 | −0.004 |
| A (1h ATR) | always-short | **−0.218** | −0.130 | −0.004 | 0.156 | 0.062 | +0.004 |
| B (4h ATR) | always-long | **−0.094** | −0.058 | +0.007 | 0.070 | 0.028 | −0.003 |
| B (4h ATR) | always-short | **−0.099** | −0.063 | −0.003 | 0.070 | 0.028 | +0.003 |

Notes:
- **Set B's smaller loss is mechanical, not an edge.** Costs are fixed in bps, and the R unit doubles with the 4h ATR, so costs in R roughly halve (0.218 → 0.098). Gross_R is ≈ 0 in both sets. Compare models within a label set, never across sets by R.
- **Set B is close to a fixed-horizon label:** 85% of trades expire, and TP is hit ~2% of the time. Its `label` mostly reflects the sign of the 4h return net of costs.
- **`maker_entry_tp` cuts costs by ~40%** for both sets. It stays an upper bound, because limit-order fills aren't modeled.

## Phase 1 step 3: GDELT news backfill via BigQuery (2026-10-26/27)

`uv run python -m mbpe.sources.gdelt_bq {schema|dry-run|run|report|coverage}`. Data is in `data/raw/gdelt_bq/<year>.parquet` (140 MB). Run records are in `data/raw/gdelt_bq/<year>.run.json` (committed), each with the exact SQL, job ID, timings, bytes and row count. Credentials come from local gcloud Application Default Credentials (project `nifty-seat-461917-i4`); nothing credential-related is in the repo.

**Query:**
- **Table:** `gdelt-bq.gdeltv2.gkg_partitioned`. The schema was checked first: `DATE` is INTEGER; `SourceCommonName`, `DocumentIdentifier`, `V2Tone`, `Extras`, `V2Themes` and `TranslationInfo` are STRING.
- **Columns returned:** `DATE`, `SourceCommonName`, `DocumentIdentifier`, `V2Tone`, plus `title` extracted in SQL from `Extras`, and `keywords`, the list of keywords each row matched.
- **Filters:** English-source rows only (empty `TranslationInfo`), and a partition filter per year.
- **V2Themes is excluded.** Full-range dry runs were 486.3 GB without it and 1,254.0 GB with it, over the 900 GB budget. This is noted in SCOPE.md.

**Runs:** one job per calendar year, each run exactly once.

| Year | Range | Rows | GB processed | Job time | Download |
|---|---|---|---|---|---|
| 2020 | 2020-09-01 → 2021-01-01 | 27,718 | 21.6 | 3 s | 23 s |
| 2021 | full year | 238,422 | 72.9 | 7 s | 206 s |
| 2022 | full year | 377,705 | 64.2 | 6 s | 310 s |
| 2023 | full year | 427,415 | 103.8 | 5 s | 324 s |
| 2024 | full year | 371,799 | 89.5 | 5 s | 296 s |
| 2025 | full year | 254,312 | 77.4 | 5 s | 198 s |
| 2026 | 2026-01-01 → 2026-10-26 | 188,167 | 58.8 | 4 s | 141 s |
| **Total** | | **1,885,538** | **488.1** | | ~25 min |

Plus one coverage query (all GKG rows per 15-min batch, reading only `DATE`): 5.8 GB. **Total processed across all runs: 493.8 GB**, inside the 1 TB monthly free allowance. Billed bytes equal the estimates.

### Rows per year and keyword (a row can match several keywords; 1.16 on average)
| Year | bitcoin | btc | crypto | cryptocurrency | stablecoin | tether | binance | coinbase | spot_etf | sec_crypto | federal_reserve | fomc | cpi | inflation | interest_rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2020 | 8,436 | 1,169 | 3,791 | 3,632 | 288 | 150 | 306 | 458 | 0 | 234 | 1,622 | 221 | 1,085 | 6,617 | 3,114 |
| 2021 | 64,996 | 4,099 | 44,888 | 38,791 | 1,605 | 568 | 3,634 | 4,954 | 78 | 1,564 | 5,890 | 1,404 | 5,360 | 82,781 | 12,392 |
| 2022 | 35,564 | 2,890 | 70,135 | 30,119 | 2,269 | 806 | 5,143 | 4,310 | 174 | 2,407 | 10,004 | 1,615 | 8,616 | 207,112 | 38,306 |
| 2023 | 64,058 | 10,968 | 87,815 | 26,532 | 4,673 | 2,297 | 15,649 | 10,825 | 3,266 | 8,071 | 14,799 | 2,127 | 13,853 | 173,147 | 62,837 |
| 2024 | 83,995 | 12,802 | 69,188 | 24,326 | 3,627 | 2,160 | 7,946 | 5,997 | 5,030 | 5,600 | 16,724 | 1,885 | 13,222 | 139,093 | 53,111 |
| 2025 | 48,489 | 10,221 | 62,124 | 19,854 | 8,804 | 1,366 | 3,307 | 4,563 | 434 | 2,336 | 19,743 | 1,579 | 9,572 | 79,955 | 31,662 |
| 2026 | 28,325 | 5,225 | 42,553 | 10,200 | 5,231 | 837 | 2,493 | 2,609 | 218 | 855 | 13,883 | 981 | 8,826 | 77,274 | 22,442 |

`inflation` is the largest bucket: 2022 alone has 207k rows. Macro keywords make up most of the rows, so features should be built per keyword group (crypto vs macro), not on the pooled count.

**Empty titles:** 0.06–0.36% of rows per year (highest in 2025). Title extraction works.

### `bitcoin` per day vs the DOC API (FEASIBILITY.md)
| | BigQuery (title/URL, English sources) | DOC API (full text, `sourcelang:english`) |
|---|---|---|
| Median per day | 120 | 305 |
| Max per day | 1,597 (2024-12-05, BTC crossing $100k) | 1,955 (2021-05-13) |
| Days > 250 | 313 | 1,318 |

These are lower, as expected: title/URL matching versus full text.

### GDELT coverage and outages
The coverage query counts all GKG rows per 15-min batch. A batch with zero rows is a **GDELT outage**; a batch with rows but no keyword match is just **quiet**.

| Year | 15-min slots | Outage (no GKG rows) | Present, no keyword match |
|---|---|---|---|
| 2020 (Sep–Dec) | 11,711 | **22.3%** | 14.1% |
| 2021 | 35,040 | 1.9% | 6.1% |
| 2022 | 35,040 | 0.08% | 2.1% |
| 2023 | 35,040 | 0.5% | 2.1% |
| 2024 | 35,136 | 0.01% | 3.2% |
| 2025 | 35,040 | **4.8%** | 4.9% |
| 2026 | 28,513 | 0.02% | 6.3% |

- **Days with zero matched rows:** 2025-06-15 → 2025-07-01 (17 days). This is a real GDELT outage: **2025-06-14 18:00 → 2025-07-02 02:00 UTC, 1,665 consecutive empty batches**.
- **Other long outages:**
  - 2023-03-22 04:45 → 03-23 14:30 (136 batches);
  - **Oct–Nov 2020:** almost nightly outages ending around 07:00 UTC, the longest 88 batches; 63% of batches missing between Oct 15 and Nov 17;
  - mid-August 2021.

  In total there are 562 outage runs, 85 of them ≥ 2 h.
- **The final 6-month holdout (from 2026-04-26) is effectively clean** (0.02% outage in 2026).
- **Implications for the feature step:**
  - Coverage comes from `coverage.parquet`, per the SCOPE.md rule: outage batches mean no data, not no news.
  - 2020 has 22% outages, so news features there are unreliable. **Decision needed:** start the news ablation from 2021-01-01, or keep 2020 with the coverage flag.

## Phase 1 step 4: GDELT live collector and backfill/live parity (2026-10-27)

**Live collector** (`mbpe/sources/gdelt_live.py`, schedule examples in `deploy/`; not installed):
- Each run reads `lastupdate.txt` and downloads the newest English GKG file, verifying size and MD5 against the listing. It then catches up any unsaved batch from the last 48 h by file name; those are checked by zip CRC, since the listing has no MD5 for them.
- Rows are parsed with the same code path as the parity check and the same generated keyword matcher as the backfill. Each batch writes `data/raw/gdelt_live/<date>/<HHMM>.parquet` (matched rows + `fetched_at`) and `<HHMM>.json` (batch label, total English articles `n_rows` for live coverage, publish time, MD5). Batches older than 48 h that were never saved go to `gaps.json`.
- **Availability = batch label (`DATE`) + 15 min**, in backtest and live, via `gdelt_bq.news_as_of`; `fetched_at` is never used. The collector logs a warning when a batch is published more than 15 min after its label.
- **Bug found and fixed in the first live run:** GDELT sends the header as lowercase `last-modified`, and the test fake had used `Last-Modified`. The lookup is now case-insensitive and the test uses the real casing.

**Live runs** (2026-10-27, minutes 2/17/32/47, 12:36–14:32 UTC):
- The first run backfilled 192 batches (48 h) with no missing files and no gaps.
- Each of the 8 later runs saved the 1 new batch.

### Arrival lag (newest-batch fetches)
| | n | min | median | max |
|---|---|---|---|---|
| publish time − batch label (from `lastupdate.txt` Last-Modified) | 8 | **−8.9 min** | **−8.8 min** | **−8.5 min** |
| fetched_at − batch label, scheduled runs | 7 | 2.03 min | 2.03 min | 2.05 min |
| fetched_at − batch label, incl. the unscheduled first run | 8 | −8.65 min | 2.03 min | 2.05 min |

- **GDELT publishes each batch ~9 min *before* its label.** `DATE` names the 15-min window the batch closes into, not its publication time.
- So "available = label + 15 min" is conservative by ~24 min and never leaks. **Late publishes (> 15 min after the label): 0 of 8.**
- Live mode fetches each batch ~2 min after its label, well before it becomes usable at label + 15 min.

### Backfill/live parity (`mbpe/sources/gdelt_parity.py`)
The 96 English GKG files of a day went through the live parser and were compared with the BigQuery rows on article set (URL + DATE), title, tone, source and keywords.

| Day | Why | Files | English rows | Live matched | BigQuery matched | Only live | Only BQ | Field differences |
|---|---|---|---|---|---|---|---|---|
| 2026-10-25 (Sun) | most recent fully covered backfill day | 96/96 | 53,112 | 205 | 205 | 0 | 0 | 0 |
| 2025-07-15 (Tue) | June US CPI release; top CPI day, 3rd-busiest day of 2025–26 | 96/96 | 148,656 | 2,235 | 2,235 | 0 | 0 | 0 |

- **Identical on both days.**
- **Edge cases covered:** 79 + 14 titles with HTML entities, 1 empty title (matched via its URL) and 460 multi-keyword rows.
- **Edge cases that never occur:** across all 1.89 M backfill rows there are no non-ASCII titles or URLs and no missing title tags, because GDELT writes special characters as entities.
- **SQL rule vs Python matcher:** the generated SQL keyword rule, run in BigQuery on 13 literal sample titles (0 bytes scanned), matched the Python matcher exactly.

**Rules added to SCOPE.md:**
- all ablation comparisons start 2021-01-01;
- news features are NaN where coverage is missing; rolling baselines skip missing batches and need ≥ 80% coverage;
- empty-title rows count toward volume but not sentiment;
- availability is label + 15 min.

## Phase 2 step 1: headline dedup and sentiment scoring setup (2026-10-27)

Nothing in the full backfill has been scored yet.

### Hand-labeling sample (`labeling/`)
- **`sample.csv`:** 200 distinct headlines, stratified 2021–2026 × keyword group (crypto = any crypto keyword, else macro), 16–17 per stratum, seed 20261027, shuffled. Columns are `id, title, label`. Titles are decoded and stripped of outlet names, and there are no dates, sources or keywords.
- **The key is kept out of the repo:** ids → DATE / URL / source / keywords are in `data/labeling/sample_key.parquet`, and the model scores are in `data/labeling/sample_scores.parquet`. Both are git-ignored, so neither can bias the labels.
- **`GUIDE.md`:** label financial-news tone (positive / negative / neutral), not the expected BTC price impact; 3 invented examples per label.

### Dedup (`mbpe/features/dedup.py`, `config/news.yaml`)
- **Title normalization:** HTML entities decoded (twice, for `&amp;amp;`), whitespace collapsed, a trailing outlet segment stripped (` | `, ` - `, ` – `, ` — ` followed by ≤ 4 words, with ≥ 3 words left), lowercased.
- **Matching:** online and causal; rows go in batch-time order. A row joins the cluster of its most similar earlier row within 24 h (MiniLM-L6-v2 cosine ≥ **0.85**), but **only if that cluster's first member is ≤ 24 h older**, so later developments start a new cluster. Exact duplicates share an embedding (similarity 1) and always join first. Empty titles stay singletons.
- **A cluster's availability** is its earliest member's batch time + 15 min. Later copies add volume only. **Clusters are used only for counting and novelty features.**

**Full-backfill result** (8.5 min on the Mac GPU), with the 24 h cap:

| | |
|---|---|
| Rows → clusters | 1,885,538 → **979,493** (1.93 per cluster) |
| Size 1 / 2 / 3–5 / 6–10 / 11–50 / > 50 | 759,512 / 121,474 / 66,319 / 16,965 / 12,145 / 3,078 |
| Largest cluster | 644 rows |
| **Longest span (first → last member)** | **24.00 h** (the cap); 44,276 clusters span > 12 h |

Before the cap (uncapped chaining): 966,155 clusters, the largest 762 rows (El Salvador bitcoin law) spanning 139 h, and 26 clusters longer than 7 days.

`labeling/dedup_review.md` lists 20 random clusters with a join at similarity 0.85–0.88 for review. All 20 looked like the same story in the first run.

### Sentiment scoring (`mbpe/features/sentiment.py`)
- **What gets scored:** **every exact-unique normalized title is scored once** (the clean title of its earliest row), and scores map back to all rows with that title. Empty titles are never scored. There are 1,099,612 unique titles.
- **Output:** `p_pos`, `p_neg`, `p_neu`, `score = p_pos − p_neg`. Weights load as safetensors only.
- **Models:** ProsusAI/finbert (default) and ElKulako/cryptobert, whose classes Bullish / Bearish / Neutral map to pos / neg / neu.

| Model | Device | Titles/s (200-headline sample) | Full run estimate (1,099,612 titles) |
|---|---|---|---|
| FinBERT | MPS (Apple GPU) | 219 | ~84 min |
| CryptoBERT | MPS | 301 | ~61 min |
| FinBERT | CPU | 31 | ~10 h |

- **CPU vs GPU, FinBERT, 200 headlines:** max |Δ score| = **6.0 × 10⁻⁶** (max |Δp| 5.2 × 10⁻⁶), no change in the top label.
- **Determinism:** two runs on the GPU give identical outputs for both models (tested).

### Model licenses (report only; no switch)
| Model | Model license | Training data (declared license) | Notes |
|---|---|---|---|
| ProsusAI/finbert (in use) | **CC-BY-NC-SA-4.0** (non-commercial) | Financial PhraseBank (CC-BY-NC-SA-3.0) | Fine for research; not for commercial use |
| ElKulako/cryptobert (in use) | **MIT** | `ElKulako/stocktwits-crypto` (MIT tag) for the classifier head; pretraining also on Telegram, Reddit and Twitter posts; base `vinai/bertweet-base` (MIT) | The data are scraped StockTwits/Reddit/Twitter posts (Nov 2021 – Jun 2022); the MIT tag is the uploader's, and the platforms' terms may still apply. Trained on bullish/bearish *social posts*, not news tone |
| StephanAkkerman/FinTwitBERT-sentiment | **MIT** | Fine-tune: `TimKoornstra/financial-tweets-sentiment` + `…/synthetic-financial-tweets-sentiment` (both MIT). Pretraining: StephanAkkerman tweet datasets (CC-BY-4.0, MIT) | **Base `yiyanghkust/finbert-pretrain` declares no license**, so the chain isn't fully clear. Tweets, not headlines |
| mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis | **Apache-2.0** | Financial PhraseBank (**CC-BY-NC-SA-3.0**) | Closest domain (financial news sentences), but the training data is non-commercial |

No model was found where the model, base model and training data are all clearly permissive.
- **Checked and ruled out:**
  - `yiyanghkust/finbert-tone` and `ahmedrachid/FinancialBERT-Sentiment-Analysis` declare no license;
  - the `zeroshot/twitter-financial-news-sentiment` dataset is MIT, but every model fine-tuned on it is built on ProsusAI/finbert (non-commercial) or mixes in undeclared datasets.
- **A fully clean route, if needed later:** fine-tune a permissively licensed base (e.g. an Apache-2.0 / MIT encoder) on `zeroshot/twitter-financial-news-sentiment` (MIT) plus our own labels.

## Phase 2 step 2: market and derivatives features (2026-10-27)

`uv run python -m mbpe.features.market` → `data/features/market.parquet`: **53,904 hourly decision times × 46 features** (37 technical, 9 derivatives), built in ~5 s. Settings are in `config/features.yaml`.

**Rules applied:**
- **Cutoff at t:** a feature at t uses only 1m candles that opened before t (closed ≤ t), metrics with `create_time` < t, and funding with `calc_time` < t.
- **Relative values only.**
- **Levels:** last value within a staleness limit (1m price 5 min, open interest **15 min**, funding 8 h + 60 min), else NaN.
- **Flows:** summed over the minutes present, never forward-filled, with coverage features alongside.
- **Changes:** NaN if either endpoint is stale.
- **Registry:** every feature is a pure function registered with name, group (= ablation rung) and input datasets.
- **ATR:** uses the same `atr_bars` as the labels.

### Features by group
**Technical** (futures 1m unless noted):
- **Returns and volatility:**
  - log returns `ret_1h/4h/24h/7d`;
  - realized vol from 1m returns `rv_1h/4h/24h`;
  - `atr_1h_pct`, `atr_4h_pct` (Wilder ATR(14) / price).
- **Oscillators and range:**
  - `rsi_14_1h`, `bb_pctb_20_1h`;
  - `dist_high_24h_atr`, `dist_low_24h_atr`.
- **Volume and taker flow:**
  - `vol_z_7d` (log volume per minute vs the previous 168 h, needs ≥ 135 h);
  - `taker_buy_ratio_1h/4h/24h`;
  - `cvd_norm_1h/4h/24h`: (taker buy − taker sell) / (7-day volume per minute × window minutes). Dividing by the window's own volume would just repeat the taker ratio;
  - coverage `fut_cov_1h/4h/24h/7d`.
- **Volume profile 24h / 7d:**
  - `vp_poc_dist_*` (distance from the point of control, ATR units);
  - `vp_va_pos_*` (position in the value area: 0 = low edge, 1 = high edge, outside is < 0 or > 1). Volume goes to typical-price bins of 0.1% of price. **The value area is the classic contiguous one,** grown from the point of control toward the higher-volume neighbouring bin until it holds 70%.
- **Anchored VWAP:** `vwap_day_dist_atr`, `vwap_week_dist_atr` (anchors at 00:00 UTC and Monday 00:00 UTC; at the anchor itself, the previous session).
- **Spot vs perp** (futures + spot 1m):
  - `spot_share_1h/4h`, over minutes where both markets traded;
  - coverage `spot_cov_1h/4h`;
  - `basis_spot_perp`, at the latest minute both closed.
- **Calendar:** `hour_of_day`, `day_of_week` (1 = Monday).

**Derivatives:**
- **Funding** (archive + REST fallback): `funding_last`, `funding_z_30d` (vs settlements in the last 30 days, needs ≥ 72 of 90), `hours_to_next_funding`.
- **Open interest:**
  - `oi_chg_1h/4h/24h` (% change of `sum_open_interest`);
  - **`oi_price_quadrant_1h/4h/24h`**, a category: 1 = price ↑ OI ↑, 2 = price ↑ OI ↓, 3 = price ↓ OI ↑, 4 = price ↓ OI ↓ (zero counts as up). It replaces the OI/price ratio. Since 2021, the 4h quadrant splits 13.7k / 12.1k / 12.3k / 12.8k. **Treat it as categorical when modeling.**

### Data issues handled in the feature layer
- **Zero open interest:** 481 metrics rows report OI = 0 (e.g. 102 on 2022-03-07). These are placeholders for missing snapshots. They're skipped, and the 15-min staleness limit decides whether the previous snapshot counts. The clean layer keeps them as delivered.
- **Funding for October 2026:** it is not in the archive yet and now comes from the REST fallback. Before that, the funding features were 8.4% missing in 2026.

### Leakage test (`tests/test_features.py`, all registered features)
- **Method:** features computed on data truncated at t must equal features from the full data at t, for every feature, exactly (or both missing). Failures name the feature and the time.
- **Synthetic data:** 70 days with missing futures minutes, a 3 h spot outage, a 2.5 h metrics gap, zero-OI placeholders and ms funding jitter. 200 random t plus 11 points near the gaps.
- **Real slice:** Mar–May 2021, with real spot gaps (04-20, 04-25), a metrics gap (04-27) and zero OI (05-22). 200 random t plus 7 points near the gaps.
- **Subset check:** evaluating at selected times equals the full table.
- **The harness catches leakage:** a deliberately leaky feature (1 h look-ahead) makes it fail, naming that feature.
- **Quadrant check:** the quadrant agrees with the signs of the price and OI changes.
- **Result:** all pass after the quadrant and value-area changes.

### Missing share per feature per year
| feature | group | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | since 2021 |
|---|---|---|---|---|---|---|---|---|---|
| `ret_1h` | technical | 0.03% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `ret_4h` | technical | 0.14% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `ret_24h` | technical | 0.82% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `ret_7d` | technical | 5.74% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `rv_1h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `rv_4h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `rv_24h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `atr_1h_pct` | technical | 0.44% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `atr_4h_pct` | technical | 1.88% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `rsi_14_1h` | technical | 0.48% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `bb_pctb_20_1h` | technical | 0.65% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `dist_high_24h_atr` | technical | 0.44% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `dist_low_24h_atr` | technical | 0.44% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `fut_cov_1h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `fut_cov_4h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `fut_cov_24h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `fut_cov_7d` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `vol_z_7d` | technical | 4.61% | 0 | 0 | 0 | 0.01% | 0 | 0 | <0.01% |
| `taker_buy_ratio_1h` | technical | 0 | 0 | 0 | 0 | 0.01% | 0 | 0 | <0.01% |
| `cvd_norm_1h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `taker_buy_ratio_4h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `cvd_norm_4h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `taker_buy_ratio_24h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `cvd_norm_24h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `vp_poc_dist_24h` | technical | 0.44% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `vp_va_pos_24h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `vp_poc_dist_7d` | technical | 0.44% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `vp_va_pos_7d` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `vwap_day_dist_atr` | technical | 0.44% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `vwap_week_dist_atr` | technical | 0.44% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `spot_share_1h` | technical | 0.20% | 0.15% | 0 | 0.01% | 0 | 0 | 0 | 0.03% |
| `spot_cov_1h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `spot_share_4h` | technical | 0.03% | 0.01% | 0 | 0 | 0 | 0 | 0 | <0.01% |
| `spot_cov_4h` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `basis_spot_perp` | technical | 0.24% | 0.17% | 0 | 0.02% | 0 | 0 | 0 | 0.03% |
| `hour_of_day` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `day_of_week` | technical | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `funding_last` | derivatives | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `funding_z_30d` | derivatives | 19.41% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `hours_to_next_funding` | derivatives | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `oi_chg_1h` | derivatives | 0.17% | 0.41% | 0.13% | 0 | 0.14% | 0.02% | 0 | 0.12% |
| `oi_price_quadrant_1h` | derivatives | 0.17% | 0.41% | 0.13% | 0 | 0.14% | 0.02% | 0 | 0.12% |
| `oi_chg_4h` | derivatives | 0.27% | 0.49% | 0.16% | 0 | 0.17% | 0.02% | 0 | 0.15% |
| `oi_price_quadrant_4h` | derivatives | 0.27% | 0.49% | 0.16% | 0 | 0.17% | 0.02% | 0 | 0.15% |
| `oi_chg_24h` | derivatives | 0.96% | 0.50% | 0.23% | 0 | 0.25% | 0.02% | 0 | 0.17% |
| `oi_price_quadrant_24h` | derivatives | 0.96% | 0.50% | 0.23% | 0 | 0.25% | 0.02% | 0 | 0.17% |
**No feature is more than 5% missing since 2021-01-01** (largest: `oi_chg_24h` 0.17%). The 2020 values are warm-up (7-day returns, 30-day funding z-score, 7-day volume baseline).

### Funding fallback and label rebuild
- **Funding source:** clean funding = 6,663 archive rows (to 2026-09-30) + 80 REST rows (2026-10-01 → 10-27, `source = rest`). REST vs archive for Sep 2026: 90/90 identical.
- **Labels fail on missing funding.** Label building now fails if a scheduled funding time inside a trade window has no data, so Oct 2026 can no longer get zero funding silently.
- **What the rebuild changed:** only trades with decision times from 2026-09-30 20:00 to 2026-10-25 15:00 whose window includes a funding time. All of them previously had `funding_R` = 0; no other column or row changed.

| Label set | Rows with funding_R changed | Mean / max change (R) | Label flips |
|---|---|---|---|
| A (1h ATR) | 383 (193 long, 190 short) | 0.009 / 0.025 | 3 (short) |
| B (4h ATR) | 530 (259 long, 271 short) | 0.004 / 0.011 | 3 (short) |

## Phase 2 step 3: news and calendar features (2026-10-27)

`uv run python -m mbpe.features.news` → `data/features/news.parquet`: **53,942 hourly decision times × 46 features** (43 `news`, 3 `calendar`). It uses FinBERT (`default_sentiment` in `config/news.yaml`) and builds in 75 s.

### Inputs
- **News rows:** backfill + live (`load_news_rows`), 1,886,217 rows.
- **Dedup:** rerun on backfill + live (979,912 clusters, longest span 24.00 h).
- **Sentiment:** FinBERT scores for **all 1,100,036 exact-unique titles** in `data/features/sentiment_finbert.parquet` (1 h 45 min on the Mac GPU, at times sharing it with dedup). CryptoBERT scoring runs into `sentiment_cryptobert.parquet`. The 200-sample scores stay in `data/labeling/`, out of view.
- **Coverage table:** BigQuery + live batch metadata.
- **Calendar:** `data/reference/macro_events.csv`.

### Rules
- **Availability:** a news row or coverage batch counts at t only if label + 15 min ≤ t. Windows are (t − W, t].
- **Groups:** crypto / macro by keywords; a row with both counts in both.
- **Coverage gate:** a window below 80% batch coverage makes all its features NaN, never 0. Rolling baselines skip missing values and need ≥ 80% (135 of 168 hourly values).
- **Sentiment counts each cluster once:** the mean of its titles available by t, for clusters with a title in the window. Strongly negative means score < −0.5 (config). Empty titles count toward volume only.

### Features
- **`news` group, per keyword group G ∈ {crypto, macro}:**
  - **Volume:** `news_G_articles_{1h,4h,24h}`, `news_G_new_clusters_{1h,4h,24h}`, and `news_G_count_z_4h` (4h count scaled to full coverage, vs the previous 168 hourly values).
  - **Novelty:** `news_G_novelty_{1h,4h}`, the share of articles that start a new cluster.
  - **Sentiment:** `news_G_sent_mean_W`, `news_G_sent_std_W`, `news_G_sent_neg_share_W` for W ∈ {1h, 4h, 24h}, plus `news_G_sent_change_4h_24h`.
  - **Macro only:** `news_macro_tone_{4h,24h}` (GDELT tone, each cluster once).
  - **Coverage:** `news_cov_{1h,4h,24h}`.
- **`calendar` group:** `cal_hours_to_next_event`, `cal_hours_since_last_event`, `cal_event_next_4h` (US CPI or FOMC).

**Average values since 2021:**
- **Volume:** 16.3 crypto and 20.8 macro articles per hour.
- **Novelty:** 69% of 4h crypto articles start a new cluster.
- **4h sentiment:** crypto −0.003, macro −0.12.
- **Strongly negative crypto clusters (4h):** 19%.
- **Macro tone (4h):** −1.2.

### Calendar (`data/reference/macro_events.csv`, committed)
- **Contents:** 75 CPI releases (08:30 ET) and 51 FOMC statements (14:00 ET), Sep 2020 → Dec 2026 (upcoming ones marked `scheduled`), in UTC with daylight saving.
- **Sources:** BLS CPI archive and schedule, the BLS 2025 lapse revised-dates page, and the Federal Reserve FOMC calendars.
- **Rescheduled events (noted in the file):**
  - Sep 2025 CPI: Oct 15 → **Oct 24**;
  - Oct 2025 CPI: **cancelled**;
  - Nov 2025 CPI: Dec 10 → **Dec 18**;
  - Jan 2026 CPI: Feb 11 → **Feb 13**.

  The Aug 22, 2025 notation vote is listed but excluded.
- **Independent check:** every one of the 73 past CPI dates shows a ≥ 1.5× spike in GDELT "cpi" articles against the surrounding ±4 days, and 71 are the busiest day of that window.
- **SCOPE.md (pre-registered):** no trades whose window from entry − 30 min to exit contains a CPI or FOMC release. Known limitation: actual (not originally announced) times are used.

### Tests (`tests/test_news_features.py`, 9)
- **Truncation:** at 200 random times + 7 around a synthetic outage, the full pipeline (causal dedup → prepare → features) on data truncated at t equals the full-data result, for every news and calendar feature.
- **Late articles:** inserting articles labeled t and t + 15 min (copies of live stories and a new story) changes nothing at t.
- **Synthetic outage:** NaN, not 0, with coverage reported as 0. Recovery follows the 80% rule; a 1-day outage leaves a 7-day baseline valid.
- **Real mid-2025 outage:** all news features NaN, coverage 0; three days later the 1h–24h windows are back and the 7-day baseline is still NaN.
- **Cluster-once sentiment:** 10 copies at +1 and 1 story at −1 give a mean of 0.
- **Calendar:** feature values; the file is sorted and in UTC.
- **Harness check:** a leaky feature (next-hour count) is caught and named.

### How the outages show up
- **2025-06-14 18:00 → 07-02 02:00 (17 days, no GKG at all):**
  - 1h features NaN from 2025-06-12 19:00 (smaller gaps just before) to 07-02 03:00;
  - 24h features NaN from 06-14 23:00 to 07-02 21:00;
  - the 7-day count z-score NaN until **07-07 20:00**, when its baseline is 80% valid again;
  - coverage is 0 throughout.
- **2020:**

  | 2020 | mean 24h coverage | 1h counts NaN | 24h counts NaN | 4h count z NaN |
  |---|---|---|---|---|
  | Sep | 0.94 | 7% | 12% | 33% (baseline warm-up) |
  | Oct | 0.58 | 54% | 91% | 100% |
  | Nov | 0.58 | 47% | 60% | 75% |
  | Dec | 1.00 | 1% | 0% | 0% |

  Per SCOPE, ablations start 2021-01-01.
- **Since 2021:** count features are missing in 1.4% of hours. Genuinely quiet hours (covered, zero articles) are 0.6% (crypto) and 1.5% (macro) at 1h; they show as 0, not NaN.

### Missing share per feature per year
| feature | group | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | since 2021 |
|---|---|---|---|---|---|---|---|---|---|
| `news_cov_1h` | news | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `news_cov_4h` | news | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `news_cov_24h` | news | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `news_crypto_articles_1h` | news | 27.3% | 2.8% | 0.2% | 0.8% | <0.1% | 4.9% | <0.1% | 1.5% |
| `news_crypto_new_clusters_1h` | news | 27.3% | 2.8% | 0.2% | 0.8% | <0.1% | 4.9% | <0.1% | 1.5% |
| `news_crypto_sent_mean_1h` | news | 30.6% | 3.2% | 1.0% | 1.3% | 0.4% | 5.5% | 1.3% | 2.1% |
| `news_crypto_sent_std_1h` | news | 36.0% | 5.1% | 3.2% | 2.5% | 1.5% | 7.2% | 4.5% | 4.0% |
| `news_crypto_sent_neg_share_1h` | news | 30.6% | 3.2% | 1.0% | 1.3% | 0.4% | 5.5% | 1.3% | 2.1% |
| `news_crypto_articles_4h` | news | 28.3% | 2.5% | 0.2% | 0.5% | 0 | 4.8% | 0 | 1.4% |
| `news_crypto_new_clusters_4h` | news | 28.3% | 2.5% | 0.2% | 0.5% | 0 | 4.8% | 0 | 1.4% |
| `news_crypto_sent_mean_4h` | news | 28.3% | 2.5% | 0.2% | 0.5% | 0 | 4.8% | <0.1% | 1.4% |
| `news_crypto_sent_std_4h` | news | 28.4% | 2.5% | 0.2% | 0.5% | 0 | 4.8% | <0.1% | 1.4% |
| `news_crypto_sent_neg_share_4h` | news | 28.3% | 2.5% | 0.2% | 0.5% | 0 | 4.8% | <0.1% | 1.4% |
| `news_crypto_articles_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_crypto_new_clusters_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_crypto_sent_mean_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_crypto_sent_std_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_crypto_sent_neg_share_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_crypto_novelty_1h` | news | 30.6% | 3.2% | 1.0% | 1.3% | 0.4% | 5.5% | 1.3% | 2.1% |
| `news_crypto_novelty_4h` | news | 28.3% | 2.5% | 0.2% | 0.5% | 0 | 4.8% | <0.1% | 1.4% |
| `news_crypto_sent_change_4h_24h` | news | 42.3% | 4.2% | 0.2% | 0.6% | 0 | 5.0% | <0.1% | 1.7% |
| `news_crypto_count_z_4h` | news | 51.9% | 6.1% | 0.2% | 2.0% | 0 | 6.4% | 0 | 2.5% |
| `news_macro_articles_1h` | news | 27.3% | 2.8% | 0.2% | 0.8% | <0.1% | 4.9% | <0.1% | 1.5% |
| `news_macro_new_clusters_1h` | news | 27.3% | 2.8% | 0.2% | 0.8% | <0.1% | 4.9% | <0.1% | 1.5% |
| `news_macro_sent_mean_1h` | news | 34.9% | 7.0% | 0.4% | 1.1% | 0.6% | 6.7% | 2.3% | 3.0% |
| `news_macro_sent_std_1h` | news | 47.1% | 14.7% | 1.0% | 2.0% | 2.5% | 11.3% | 7.5% | 6.5% |
| `news_macro_sent_neg_share_1h` | news | 34.9% | 7.0% | 0.4% | 1.1% | 0.6% | 6.7% | 2.3% | 3.0% |
| `news_macro_articles_4h` | news | 28.3% | 2.5% | 0.2% | 0.5% | 0 | 4.8% | 0 | 1.4% |
| `news_macro_new_clusters_4h` | news | 28.3% | 2.5% | 0.2% | 0.5% | 0 | 4.8% | 0 | 1.4% |
| `news_macro_sent_mean_4h` | news | 28.5% | 2.6% | 0.2% | 0.5% | 0 | 4.9% | <0.1% | 1.4% |
| `news_macro_sent_std_4h` | news | 29.6% | 3.1% | 0.2% | 0.5% | 0 | 5.0% | <0.1% | 1.5% |
| `news_macro_sent_neg_share_4h` | news | 28.5% | 2.6% | 0.2% | 0.5% | 0 | 4.9% | <0.1% | 1.4% |
| `news_macro_articles_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_macro_new_clusters_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_macro_sent_mean_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_macro_sent_std_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_macro_sent_neg_share_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `news_macro_novelty_1h` | news | 34.9% | 7.0% | 0.4% | 1.1% | 0.6% | 6.7% | 2.2% | 3.0% |
| `news_macro_novelty_4h` | news | 28.5% | 2.6% | 0.2% | 0.5% | 0 | 4.9% | <0.1% | 1.4% |
| `news_macro_sent_change_4h_24h` | news | 42.3% | 4.4% | 0.2% | 0.6% | 0 | 5.0% | <0.1% | 1.8% |
| `news_macro_count_z_4h` | news | 51.9% | 6.1% | 0.2% | 2.0% | 0 | 6.4% | 0 | 2.5% |
| `news_macro_tone_4h` | news | 28.5% | 2.6% | 0.2% | 0.5% | 0 | 4.9% | <0.1% | 1.4% |
| `news_macro_tone_24h` | news | 40.6% | 3.6% | <0.1% | 0.6% | 0 | 4.9% | 0 | 1.6% |
| `cal_hours_to_next_event` | calendar | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `cal_hours_since_last_event` | calendar | 8.6% | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `cal_event_next_4h` | calendar | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
**Only one feature is more than 5% missing since 2021-01-01: `news_macro_sent_std_1h` (6.5%).** A standard deviation needs ≥ 2 scored macro clusters in the hour, and quiet hours (mostly 2021 nights) often have 0–1; the coverage gaps add to this. The 1h macro mean, negative share and novelty are 2–7% missing in some years for the same reason (no scored macro cluster in the hour). The 2020 values reflect the outages above.

## Phase 3 step 1: evaluation harness, baselines and first models (2026-10-28)

`uv run python -m mbpe.validation.harness`, run in 6 min. The protocol is pre-registered in SCOPE.md ("Evaluation protocol") and set in `config/eval.yaml`. Outputs:
- `reports/phase3_step1/` (`report.md`, `overall_metrics.csv`, `fold_metrics.csv`, `thresholds.csv`)
- MLflow in `mlruns/` (34 runs)
- trades in `data/features/phase3_step1_trades.parquet`

**The holdout (from 2026-04-26) was not touched:** no access log exists, and the last evaluated trade exits at 2026-04-25 23:01 UTC.

**Setup:**
- **Folds:** 18 test folds (quarters 2022Q1 → 2026Q1, plus Apr 1–25, 2026). Expanding training from 2021-01-01, purged on label windows, last 20% = validation (purged against training).
- **Weights:** average uniqueness per side.
- **Models:** one per side.
  - **Logistic regression:** median imputation + missing indicators, standardized, one-hot quadrants.
  - **LightGBM:** num_leaves 15, min_data_in_leaf 200, learning rate 0.03, feature_fraction 0.7, early stopping (100) on validation, quadrants categorical, no tuning.
  - **Calibration:** isotonic on validation.
- **Thresholds:** per fold, side and cost scenario, on validation (max mean net R, ≥ 100 trades).
- **Simulator:** one position across both sides; conflicting signals → no trade; blackout [entry − 30 min, entry + 4 h] around CPI/FOMC for every strategy.
- **Baselines:** always-long and always-short (position rule within the side), and random entry at the model's per-side signal frequency (20 seeds per fold).
- **Sanity run:** LightGBM rung 3 on label set A with shuffled training labels.
- **Training target:** `label` (taker); each cost scenario chooses its own threshold.
- **Metrics:** at 1% risk per trade. `return/yr` is the simple sum of 1% × R per year, so it can fall below −100%. Max drawdown compounds 1% per trade, so thousands of losing trades approach 100%. Only compare these between strategies.

### Findings
1. **No configuration makes money after costs.** Every model in every label set × cost scenario × rung has a mean net R whose 95% block-bootstrap CI lies entirely below 0. The best are LightGBM rung 3 on A with `maker_entry_tp` (−0.055 R [−0.086, −0.024]) and LightGBM rung 2 on B with `maker_entry_tp` (−0.048 R [−0.068, −0.028]). Taker results, the headline, are worse: A −0.127 to −0.181 R, B −0.076 to −0.096 R.
2. **The models do select better trades than the baselines, mostly in label set A.** A/taker LightGBM (−0.13 R) vs random entry at the same frequency (median −0.22 R) and always-trade (−0.23 R): it beats the random median in **16–18 of 18 folds** and the random 95th percentile in 10–13 of 18. In label set B the gain is smaller (−0.077 vs random −0.092 to −0.106; 11–14/18 folds above the median, 2–5/18 above p95). So the models reduce losses but don't clear the ~0.2 R (A) / ~0.1 R (B) cost hurdle.
3. **Adding news + calendar (rung 3) doesn't improve on market data alone at this stage.**
   - A/taker LightGBM: rung 1 −0.130, rung 2 −0.135, rung 3 −0.127 R (CIs fully overlap).
   - Logistic regression gets slightly *worse* with rung 3 in every configuration (more features, no gain).
   - Not a final verdict, since there's no tuning yet and FinBERT may be replaced after the hand-labeling. But it's the first reading against the kill criterion.
4. **The shuffled-label sanity run shows no edge:** −0.208 R [−0.235, −0.180] vs its random baseline median −0.220 (5–95%: −0.228 to −0.207). It beats the random 95th percentile in 1 of 18 folds (A/taker), and its CI contains the random median. This is consistent with no leakage through the harness. It sits at the top of the random band, slightly above the median in 12/18 folds; likely because the threshold search still prefers the less costly hours (e.g. wider-ATR hours, smaller cost in R) even without label information.
5. **Label set B's smaller losses are mechanical,** as in Phase 1: costs in R are halved by the wider 4h ATR. Compare models within a label set only.

### Per-label-set results (from `reports/phase3_step1/report.md`)
#### Label set A, cost scenario `taker`

| strategy | rung | trades | mean R [95% CI] | bps/trade | return/yr | hit | PF | Sharpe | max DD | random: median mean R [5–95%] | folds > random median | folds > random p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| always_long | – | 10,699 | -0.239 [-0.260, -0.217] | -14.7 | -593.0% | 37.6% | +0.59 | -9.82 | 100.0% | – | – | – |
| always_short | – | 10,613 | -0.227 [-0.249, -0.204] | -13.7 | -558.0% | 36.9% | +0.61 | -9.45 | 100.0% | – | – | – |
| logreg | 1 | 4,462 | -0.172 [-0.204, -0.138] | -10.7 | -177.5% | 39.7% | +0.68 | -4.95 | 100.0% | -0.224 [-0.239, -0.212] | 15/18 | 9/18 |
| logreg | 2 | 4,606 | -0.162 [-0.192, -0.129] | -9.5 | -172.6% | 40.4% | +0.69 | -4.62 | 100.0% | -0.228 [-0.237, -0.211] | 15/18 | 10/18 |
| logreg | 3 | 5,610 | -0.181 [-0.210, -0.153] | -10.6 | -235.2% | 40.0% | +0.67 | -5.74 | 100.0% | -0.223 [-0.241, -0.215] | 14/18 | 4/18 |
| lgbm | 1 | 4,020 | -0.130 [-0.162, -0.098] | -10.2 | -120.8% | 41.4% | +0.75 | -3.64 | 99.6% | -0.222 [-0.240, -0.207] | 16/18 | 10/18 |
| lgbm | 2 | 4,155 | -0.135 [-0.169, -0.103] | -10.9 | -129.8% | 41.0% | +0.74 | -3.81 | 99.7% | -0.223 [-0.243, -0.205] | 18/18 | 11/18 |
| lgbm | 3 | 3,946 | -0.127 [-0.158, -0.096] | -9.2 | -115.9% | 41.6% | +0.75 | -3.48 | 99.5% | -0.225 [-0.239, -0.206] | 16/18 | 13/18 |
| lgbm_shuffled | 3 | 6,938 | -0.208 [-0.235, -0.180] | -11.4 | -334.1% | 37.6% | +0.64 | -7.11 | 100.0% | -0.220 [-0.228, -0.207] | 12/18 | 1/18 |

Per fold: mean net R (trades). `·` = no trades (threshold rule not met or no signals).

| fold | test period | always_long | always_short | logreg r1 | logreg r2 | logreg r3 | lgbm r1 | lgbm r2 | lgbm r3 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2022-01-01 | -0.15 (602) | -0.13 (612) | -0.13 (563) | -0.12 (535) | -0.11 (569) | -0.16 (216) | -0.11 (451) | -0.11 (514) |
| 1 | 2022-04-01 | -0.24 (627) | -0.09 (599) | -0.13 (359) | -0.11 (297) | -0.11 (414) | +0.03 (188) | -0.17 (478) | -0.10 (415) |
| 2 | 2022-07-01 | -0.16 (617) | -0.15 (613) | -0.13 (594) | -0.14 (593) | -0.12 (517) | -0.12 (552) | -0.09 (339) | -0.05 (255) |
| 3 | 2022-10-01 | -0.35 (600) | -0.25 (593) | -0.45 (314) | -0.38 (450) | -0.41 (261) | -0.27 (100) | -0.27 (211) | -0.29 (96) |
| 4 | 2023-01-01 | -0.19 (620) | -0.31 (618) | -0.18 (302) | -0.10 (381) | -0.11 (431) | -0.19 (337) | -0.15 (185) | -0.15 (248) |
| 5 | 2023-04-01 | -0.25 (613) | -0.26 (617) | -0.19 (526) | -0.13 (206) | -0.17 (217) | -0.19 (292) | -0.17 (393) | -0.18 (514) |
| 6 | 2023-07-01 | -0.44 (630) | -0.33 (627) | -0.28 (338) | -0.38 (208) | -0.35 (215) | -0.15 (230) | -0.01 (62) | -0.21 (99) |
| 7 | 2023-10-01 | -0.20 (615) | -0.30 (611) | -0.17 (199) | -0.17 (312) | -0.13 (298) | -0.16 (215) | -0.18 (151) | -0.20 (319) |
| 8 | 2024-01-01 | -0.14 (620) | -0.24 (628) | -0.05 (185) | -0.07 (168) | -0.16 (156) | -0.05 (396) | -0.13 (311) | -0.13 (200) |
| 9 | 2024-04-01 | -0.29 (620) | -0.14 (604) | -0.23 (133) | -0.10 (155) | -0.19 (138) | -0.17 (233) | -0.03 (237) | -0.10 (273) |
| 10 | 2024-07-01 | -0.21 (627) | -0.16 (631) | -0.04 (154) | -0.08 (162) | -0.03 (170) | -0.12 (227) | -0.13 (346) | -0.12 (213) |
| 11 | 2024-10-01 | -0.17 (625) | -0.25 (634) | -0.05 (65) | -0.02 (76) | -0.12 (300) | +0.00 (142) | +0.06 (102) | -0.06 (169) |
| 12 | 2025-01-01 | -0.24 (614) | -0.16 (612) | -0.11 (114) | -0.09 (126) | -0.14 (140) | -0.10 (375) | -0.20 (334) | +0.00 (114) |
| 13 | 2025-04-01 | -0.23 (618) | -0.31 (628) | -0.08 (100) | -0.16 (309) | -0.26 (560) | +0.04 (74) | -0.12 (68) | -0.08 (154) |
| 14 | 2025-07-01 | -0.34 (620) | -0.33 (597) | -0.39 (45) | -0.30 (180) | -0.34 (121) | -0.25 (29) | -0.27 (47) | -0.37 (65) |
| 15 | 2025-10-01 | -0.23 (635) | -0.20 (611) | +0.03 (112) | -0.11 (278) | -0.18 (536) | -0.19 (181) | -0.15 (51) | -0.11 (134) |
| 16 | 2026-01-01 | -0.25 (630) | -0.19 (615) | -0.17 (319) | +0.02 (131) | -0.24 (522) | -0.20 (204) | -0.09 (344) | -0.04 (137) |
| 17 | 2026-04-01 | -0.21 (166) | -0.40 (163) | -0.09 (40) | -0.28 (39) | -0.34 (45) | -0.31 (29) | -0.02 (45) | -0.08 (27) |

#### Label set A, cost scenario `maker_entry_tp`

| strategy | rung | trades | mean R [95% CI] | bps/trade | return/yr | hit | PF | Sharpe | max DD | random: median mean R [5–95%] | folds > random median | folds > random p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| always_long | – | 10,699 | -0.145 [-0.165, -0.122] | -9.0 | -358.8% | 40.2% | +0.73 | -6.06 | 100.0% | – | – | – |
| always_short | – | 10,613 | -0.132 [-0.154, -0.110] | -8.1 | -325.0% | 39.5% | +0.75 | -5.62 | 100.0% | – | – | – |
| logreg | 1 | 4,539 | -0.092 [-0.123, -0.061] | -5.1 | -96.9% | 42.4% | +0.81 | -2.74 | 98.9% | -0.134 [-0.149, -0.122] | 15/18 | 8/18 |
| logreg | 2 | 4,967 | -0.090 [-0.120, -0.061] | -4.5 | -103.9% | 42.7% | +0.81 | -2.71 | 99.2% | -0.136 [-0.148, -0.121] | 14/18 | 8/18 |
| logreg | 3 | 5,771 | -0.095 [-0.122, -0.069] | -4.9 | -127.4% | 42.5% | +0.81 | -3.16 | 99.7% | -0.131 [-0.151, -0.121] | 14/18 | 4/18 |
| lgbm | 1 | 4,216 | -0.065 [-0.098, -0.032] | -4.8 | -63.8% | 42.8% | +0.86 | -1.89 | 95.1% | -0.133 [-0.149, -0.123] | 13/18 | 8/18 |
| lgbm | 2 | 4,287 | -0.072 [-0.106, -0.040] | -5.7 | -71.9% | 42.5% | +0.85 | -2.09 | 97.2% | -0.141 [-0.161, -0.122] | 17/18 | 9/18 |
| lgbm | 3 | 3,946 | -0.055 [-0.086, -0.024] | -3.5 | -50.5% | 43.7% | +0.88 | -1.53 | 92.0% | -0.137 [-0.151, -0.118] | 16/18 | 12/18 |
| lgbm_shuffled | 3 | 6,742 | -0.104 [-0.130, -0.077] | -5.3 | -162.0% | 40.4% | +0.80 | -3.59 | 99.9% | -0.119 [-0.129, -0.109] | 12/18 | 2/18 |

Per fold: mean net R (trades). `·` = no trades (threshold rule not met or no signals).

| fold | test period | always_long | always_short | logreg r1 | logreg r2 | logreg r3 | lgbm r1 | lgbm r2 | lgbm r3 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2022-01-01 | -0.09 (602) | -0.07 (612) | -0.07 (563) | -0.06 (535) | -0.05 (557) | -0.11 (216) | -0.05 (451) | -0.05 (514) |
| 1 | 2022-04-01 | -0.18 (627) | -0.03 (599) | -0.07 (359) | -0.06 (297) | -0.06 (414) | +0.08 (188) | -0.12 (478) | -0.04 (415) |
| 2 | 2022-07-01 | -0.10 (617) | -0.08 (613) | -0.07 (594) | -0.08 (593) | -0.05 (517) | -0.06 (552) | -0.03 (339) | +0.01 (255) |
| 3 | 2022-10-01 | -0.23 (600) | -0.13 (593) | -0.34 (314) | -0.26 (450) | -0.29 (261) | -0.18 (100) | -0.18 (211) | -0.19 (96) |
| 4 | 2023-01-01 | -0.09 (620) | -0.22 (618) | -0.10 (302) | +0.00 (381) | -0.01 (431) | -0.13 (435) | -0.16 (317) | -0.08 (248) |
| 5 | 2023-04-01 | -0.15 (613) | -0.16 (617) | -0.09 (526) | -0.12 (514) | -0.08 (217) | -0.13 (388) | -0.08 (393) | -0.08 (514) |
| 6 | 2023-07-01 | -0.28 (630) | -0.16 (627) | -0.12 (338) | -0.24 (208) | -0.18 (388) | -0.05 (230) | +0.09 (62) | -0.11 (99) |
| 7 | 2023-10-01 | -0.09 (615) | -0.20 (611) | -0.08 (199) | -0.08 (312) | -0.04 (298) | -0.08 (215) | -0.11 (151) | -0.12 (319) |
| 8 | 2024-01-01 | -0.06 (620) | -0.17 (628) | +0.01 (185) | -0.01 (168) | -0.09 (156) | +0.03 (396) | -0.07 (311) | -0.07 (200) |
| 9 | 2024-04-01 | -0.20 (620) | -0.05 (604) | -0.16 (133) | -0.03 (155) | -0.11 (138) | -0.11 (233) | +0.04 (237) | -0.02 (273) |
| 10 | 2024-07-01 | -0.14 (627) | -0.08 (631) | +0.02 (154) | -0.01 (162) | +0.04 (170) | -0.06 (227) | -0.07 (346) | -0.07 (213) |
| 11 | 2024-10-01 | -0.08 (625) | -0.17 (634) | -0.02 (130) | +0.04 (76) | -0.04 (300) | +0.07 (142) | +0.12 (102) | +0.01 (169) |
| 12 | 2025-01-01 | -0.15 (614) | -0.07 (612) | -0.07 (114) | -0.04 (126) | -0.08 (140) | -0.03 (375) | -0.13 (334) | +0.05 (114) |
| 13 | 2025-04-01 | -0.12 (618) | -0.20 (628) | +0.01 (100) | -0.13 (362) | -0.15 (560) | +0.12 (76) | -0.05 (68) | +0.00 (154) |
| 14 | 2025-07-01 | -0.20 (620) | -0.19 (597) | -0.30 (45) | -0.20 (180) | -0.21 (121) | -0.14 (29) | -0.18 (47) | -0.28 (65) |
| 15 | 2025-10-01 | -0.14 (635) | -0.11 (611) | +0.10 (112) | -0.03 (278) | -0.09 (536) | -0.12 (181) | -0.09 (51) | -0.04 (134) |
| 16 | 2026-01-01 | -0.17 (630) | -0.10 (615) | -0.12 (331) | +0.08 (131) | -0.16 (522) | -0.14 (204) | -0.03 (344) | +0.01 (137) |
| 17 | 2026-04-01 | -0.11 (166) | -0.31 (163) | -0.01 (40) | -0.20 (39) | -0.25 (45) | -0.24 (29) | +0.07 (45) | +0.00 (27) |

#### Label set B, cost scenario `taker`

| strategy | rung | trades | mean R [95% CI] | bps/trade | return/yr | hit | PF | Sharpe | max DD | random: median mean R [5–95%] | folds > random median | folds > random p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| always_long | – | 8,179 | -0.105 [-0.120, -0.091] | -14.4 | -198.7% | 40.3% | +0.64 | -6.50 | 100.0% | – | – | – |
| always_short | – | 8,156 | -0.107 [-0.121, -0.092] | -14.4 | -201.9% | 39.1% | +0.64 | -6.57 | 100.0% | – | – | – |
| logreg | 1 | 4,817 | -0.083 [-0.102, -0.066] | -10.8 | -92.8% | 42.6% | +0.70 | -4.16 | 98.4% | -0.108 [-0.120, -0.090] | 15/18 | 7/18 |
| logreg | 2 | 4,634 | -0.091 [-0.109, -0.072] | -12.4 | -97.2% | 42.1% | +0.68 | -4.48 | 98.7% | -0.100 [-0.110, -0.094] | 14/18 | 3/18 |
| logreg | 3 | 5,606 | -0.096 [-0.114, -0.078] | -12.0 | -124.3% | 41.5% | +0.67 | -4.93 | 99.6% | -0.102 [-0.113, -0.096] | 9/18 | 5/18 |
| lgbm | 1 | 3,756 | -0.076 [-0.096, -0.057] | -11.8 | -66.3% | 44.9% | +0.72 | -3.36 | 94.9% | -0.106 [-0.113, -0.096] | 14/18 | 5/18 |
| lgbm | 2 | 3,186 | -0.079 [-0.099, -0.060] | -11.8 | -58.5% | 46.4% | +0.72 | -3.36 | 92.6% | -0.097 [-0.106, -0.084] | 12/18 | 2/18 |
| lgbm | 3 | 3,793 | -0.077 [-0.097, -0.056] | -11.3 | -67.7% | 44.5% | +0.72 | -3.45 | 95.1% | -0.092 [-0.102, -0.081] | 11/18 | 4/18 |

Per fold: mean net R (trades). `·` = no trades (threshold rule not met or no signals).

| fold | test period | always_long | always_short | logreg r1 | logreg r2 | logreg r3 | lgbm r1 | lgbm r2 | lgbm r3 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2022-01-01 | -0.07 (463) | -0.09 (461) | -0.01 (325) | -0.07 (300) | -0.06 (458) | -0.08 (312) | -0.08 (316) | -0.07 (423) |
| 1 | 2022-04-01 | -0.12 (468) | +0.00 (460) | -0.06 (269) | -0.07 (383) | -0.08 (448) | -0.02 (344) | -0.06 (241) | +0.01 (420) |
| 2 | 2022-07-01 | -0.06 (467) | -0.11 (472) | -0.05 (456) | -0.05 (449) | -0.05 (371) | -0.03 (310) | -0.01 (329) | -0.02 (245) |
| 3 | 2022-10-01 | -0.15 (471) | -0.11 (471) | -0.15 (452) | -0.19 (402) | -0.16 (426) | -0.14 (311) | -0.14 (156) | -0.11 (298) |
| 4 | 2023-01-01 | -0.06 (471) | -0.15 (478) | -0.04 (199) | -0.04 (363) | -0.04 (452) | +0.01 (166) | -0.11 (103) | -0.04 (152) |
| 5 | 2023-04-01 | -0.09 (470) | -0.14 (481) | -0.10 (167) | -0.11 (407) | -0.12 (183) | -0.11 (301) | -0.09 (224) | -0.13 (253) |
| 6 | 2023-07-01 | -0.20 (486) | -0.14 (487) | -0.16 (467) | -0.14 (248) | -0.17 (426) | -0.14 (181) | -0.13 (99) | -0.17 (167) |
| 7 | 2023-10-01 | -0.10 (480) | -0.16 (479) | -0.10 (388) | -0.14 (195) | -0.07 (251) | -0.15 (406) | +0.02 (163) | -0.06 (249) |
| 8 | 2024-01-01 | -0.07 (479) | -0.14 (476) | -0.06 (378) | -0.07 (267) | +0.06 (81) | -0.00 (239) | -0.09 (308) | -0.05 (266) |
| 9 | 2024-04-01 | -0.13 (474) | -0.07 (466) | -0.11 (412) | -0.18 (285) | -0.14 (281) | -0.11 (182) | -0.05 (160) | -0.03 (214) |
| 10 | 2024-07-01 | -0.09 (479) | -0.07 (477) | -0.08 (401) | -0.05 (173) | -0.06 (347) | -0.06 (161) | -0.11 (234) | -0.16 (157) |
| 11 | 2024-10-01 | -0.06 (481) | -0.13 (479) | -0.07 (163) | -0.07 (225) | -0.12 (448) | -0.09 (210) | -0.02 (134) | -0.13 (186) |
| 12 | 2025-01-01 | -0.13 (470) | -0.05 (456) | -0.03 (154) | -0.07 (266) | -0.07 (343) | -0.06 (127) | -0.07 (166) | -0.06 (154) |
| 13 | 2025-04-01 | -0.04 (468) | -0.14 (472) | -0.02 (133) | -0.05 (147) | -0.10 (426) | +0.02 (70) | -0.05 (37) | -0.07 (81) |
| 14 | 2025-07-01 | -0.15 (472) | -0.15 (471) | -0.14 (104) | -0.11 (135) | -0.15 (184) | -0.05 (66) | -0.21 (76) | -0.22 (23) |
| 15 | 2025-10-01 | -0.14 (486) | -0.06 (475) | -0.06 (167) | -0.01 (145) | -0.14 (219) | -0.02 (52) | -0.11 (89) | -0.15 (109) |
| 16 | 2026-01-01 | -0.13 (471) | -0.09 (466) | -0.05 (102) | -0.07 (205) | -0.07 (177) | -0.09 (217) | -0.10 (245) | -0.11 (357) |
| 17 | 2026-04-01 | -0.08 (123) | -0.18 (129) | -0.09 (80) | -0.04 (39) | -0.13 (85) | -0.05 (101) | -0.11 (106) | -0.16 (39) |

#### Label set B, cost scenario `maker_entry_tp`

| strategy | rung | trades | mean R [95% CI] | bps/trade | return/yr | hit | PF | Sharpe | max DD | random: median mean R [5–95%] | folds > random median | folds > random p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| always_long | – | 8,179 | -0.066 [-0.081, -0.052] | -9.3 | -125.4% | 44.0% | +0.76 | -4.12 | 99.6% | – | – | – |
| always_short | – | 8,156 | -0.068 [-0.082, -0.054] | -9.2 | -128.6% | 42.3% | +0.75 | -4.22 | 99.7% | – | – | – |
| logreg | 1 | 5,110 | -0.049 [-0.066, -0.032] | -5.9 | -57.8% | 45.6% | +0.81 | -2.53 | 93.1% | -0.071 [-0.084, -0.055] | 15/18 | 7/18 |
| logreg | 2 | 4,649 | -0.055 [-0.074, -0.037] | -7.4 | -59.2% | 45.7% | +0.79 | -2.75 | 93.5% | -0.063 [-0.074, -0.057] | 14/18 | 2/18 |
| logreg | 3 | 5,606 | -0.058 [-0.076, -0.040] | -6.9 | -74.9% | 45.2% | +0.78 | -3.01 | 96.6% | -0.064 [-0.075, -0.058] | 9/18 | 5/18 |
| lgbm | 1 | 4,227 | -0.051 [-0.071, -0.032] | -8.0 | -50.4% | 47.2% | +0.80 | -2.39 | 90.2% | -0.069 [-0.078, -0.062] | 12/18 | 5/18 |
| lgbm | 2 | 3,186 | -0.048 [-0.068, -0.028] | -6.7 | -35.1% | 49.4% | +0.82 | -2.03 | 79.9% | -0.061 [-0.070, -0.048] | 10/18 | 2/18 |
| lgbm | 3 | 4,220 | -0.051 [-0.071, -0.032] | -7.1 | -50.1% | 46.7% | +0.81 | -2.34 | 89.9% | -0.060 [-0.071, -0.052] | 11/18 | 4/18 |

Per fold: mean net R (trades). `·` = no trades (threshold rule not met or no signals).

| fold | test period | always_long | always_short | logreg r1 | logreg r2 | logreg r3 | lgbm r1 | lgbm r2 | lgbm r3 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2022-01-01 | -0.05 (463) | -0.06 (461) | +0.01 (325) | -0.04 (300) | -0.04 (458) | -0.06 (312) | -0.06 (316) | -0.04 (423) |
| 1 | 2022-04-01 | -0.09 (468) | +0.03 (460) | -0.04 (269) | -0.04 (383) | -0.06 (448) | +0.01 (344) | -0.03 (241) | +0.04 (420) |
| 2 | 2022-07-01 | -0.04 (467) | -0.08 (472) | -0.02 (456) | -0.03 (449) | -0.02 (371) | -0.04 (433) | +0.01 (329) | +0.01 (245) |
| 3 | 2022-10-01 | -0.11 (471) | -0.07 (471) | -0.11 (447) | -0.14 (402) | -0.11 (426) | -0.09 (311) | -0.10 (156) | -0.06 (298) |
| 4 | 2023-01-01 | -0.02 (471) | -0.11 (478) | -0.01 (199) | -0.01 (378) | +0.00 (452) | +0.04 (166) | -0.07 (103) | -0.01 (152) |
| 5 | 2023-04-01 | -0.04 (470) | -0.10 (481) | -0.08 (465) | -0.07 (407) | -0.08 (183) | -0.07 (301) | -0.05 (224) | -0.07 (436) |
| 6 | 2023-07-01 | -0.14 (486) | -0.08 (487) | -0.10 (467) | -0.08 (248) | -0.10 (426) | -0.09 (181) | -0.08 (99) | -0.12 (167) |
| 7 | 2023-10-01 | -0.06 (480) | -0.12 (479) | -0.06 (388) | -0.10 (195) | -0.03 (251) | -0.11 (406) | +0.05 (163) | -0.02 (249) |
| 8 | 2024-01-01 | -0.04 (479) | -0.11 (476) | -0.03 (378) | -0.04 (267) | +0.09 (81) | +0.03 (239) | -0.07 (308) | -0.02 (266) |
| 9 | 2024-04-01 | -0.09 (474) | -0.03 (466) | -0.07 (412) | -0.14 (285) | -0.11 (281) | -0.08 (182) | -0.02 (160) | -0.08 (437) |
| 10 | 2024-07-01 | -0.06 (479) | -0.03 (477) | -0.04 (401) | -0.02 (173) | -0.03 (347) | -0.03 (161) | -0.09 (234) | -0.13 (157) |
| 11 | 2024-10-01 | -0.02 (481) | -0.09 (479) | -0.03 (163) | -0.04 (225) | -0.09 (448) | -0.11 (454) | +0.01 (134) | -0.09 (186) |
| 12 | 2025-01-01 | -0.10 (470) | -0.02 (456) | -0.00 (154) | -0.03 (266) | -0.04 (343) | -0.03 (231) | -0.04 (166) | -0.03 (154) |
| 13 | 2025-04-01 | -0.00 (468) | -0.10 (472) | +0.02 (133) | -0.02 (147) | -0.06 (426) | +0.05 (70) | -0.01 (37) | -0.03 (81) |
| 14 | 2025-07-01 | -0.09 (472) | -0.09 (471) | -0.08 (104) | -0.06 (135) | -0.09 (184) | -0.00 (66) | -0.16 (76) | -0.22 (44) |
| 15 | 2025-10-01 | -0.10 (486) | -0.02 (475) | -0.02 (167) | +0.03 (145) | -0.11 (219) | +0.01 (52) | -0.08 (89) | -0.12 (109) |
| 16 | 2026-01-01 | -0.09 (471) | -0.05 (466) | -0.02 (102) | -0.04 (205) | -0.04 (177) | -0.06 (217) | -0.08 (245) | -0.08 (357) |
| 17 | 2026-04-01 | -0.04 (123) | -0.14 (129) | -0.05 (80) | +0.00 (39) | -0.09 (85) | -0.01 (101) | -0.07 (106) | -0.12 (39) |

### Tests (`tests/test_validation.py`, 8)
- **Splitter:** no training or validation label window overlaps its test fold or the 24 h embargo after it, for all 18 folds; training is purged against validation. Folds are contiguous up to the holdout.
- **Weights:** uniqueness on a hand-built example (A [0,4), B [2,6), C [0,2) → 0.5, 0.75, 0.5).
- **Holdout guard:** raises on decision *or* exit times in the holdout, logs allowed access, passes pre-holdout rows.
- **Position rule:** never two open trades, conflicting signals never taken, every skipped signal falls inside an open trade.
- **Blackout:** window edges.
- **Thresholds:** unchanged when the test fold's features, labels and net R are altered (logreg and LightGBM).
- **Harness sanity on synthetic data:** with a real signal the model's CI lies above always-trade; with shuffled labels there's no edge.
