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
