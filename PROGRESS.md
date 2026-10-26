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
