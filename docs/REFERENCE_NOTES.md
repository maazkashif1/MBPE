# Reference notes: LLM-Auto-Trader and freqtrade

Read-only study. Nothing from either repo has been installed, run, or copied into MBPE.

| Repo | Commit read | License | Status for MBPE |
|---|---|---|---|
| `reference/LLM-Auto-Trader` | `dfa93ea` (2026-07-21) | MIT, © 2026 Erfan Alimohammadi | **Reusable with attribution** (keep the copyright + MIT notice in any copied file) |
| `reference/freqtrade` | `f81a494` (2026-10-26) | GPL-3.0 | **Ideas only.** Never copy code; re-implement from the description |

`reference/` is git-ignored.

---

## 1. LLM-Auto-Trader: `src/backtest/cpcv.py` (MIT)

**What it does.** Implements Combinatorial Purged Cross-Validation (AFML Ch. 12). It splits `T` observations into `N` contiguous groups and makes one train/test split for every combination of `K` test groups.

- `generate_paths(n_observations, n_groups, k_test, purge=0, embargo=0) -> list[CPCVPath]`
  - `CPCVPath` holds `test_groups` (tuple), `test_indices` and `train_indices` (integer positions).
  - Returns C(N, K) items, one per combination.
- `_split_groups(n_obs, n_groups)`: contiguous, near-equal `(start, end)` ranges.
- `summarize_paths(sharpes) -> {median, p5, p95, mean, std}` across items.
- `compute_pbo(sharpes[n_paths, n_configs]) -> float`: a PBO estimate (details below).

**Purge and embargo: fixed bar counts only.**
- For each test group `[start, end)`, it forbids train indices `[start - purge, start)` (purge) and `[end, end + embargo)` (embargo). Test indices are never purged.
- Nothing looks at timestamps or label end times. The units are row positions.
- The repo's scripts call it with `purge=2, embargo=2` (`scripts/run_portfolio_cpcv.py:226`, sweep script defaults) or `14/14` (`scripts/sweep_fast.py` docstring).

**Gaps vs MBPE.**
- **No label-end-time purge.** MBPE needs to drop each train sample whose label window `[t0, t1]` overlaps any test sample's window. With triple barriers, `t1` is the first barrier touch, at most 4h after entry, so windows vary in length. A bar count is only equivalent when samples are evenly spaced with no gaps, and MBPE will drop hours (CPI/FOMC blackouts, data gaps).
- **No sample-uniqueness / overlap weights** in this file.
- **Terminology.** Each C(N, K) *split* is called a "path". In AFML, paths are assembled from splits, φ = K/N · C(N, K) of them. Per-"path" Sharpe here is per-split Sharpe over K non-adjacent groups.
- **`compute_pbo` is not the Bailey/LdP CSCV estimator.** For each row it picks the best config, takes that config's median Sharpe over the *other* rows, and checks whether it is below the median of all configs over those rows. The "OOS" rows are other CPCV splits, which share groups with the in-sample row, so they aren't disjoint data. Use `cscv.py` instead (§2).

## 2. LLM-Auto-Trader: `src/analysis/cscv.py` (MIT)

**What it does.** Standard CSCV estimate of the Probability of Backtest Overfitting (AFML Ch. 11.4, Bailey et al. 2014).

- `cscv_pbo(returns_matrix[n_periods, n_strategies], n_groups=None, max_n_groups=16) -> {pbo, logit_lambdas, num_trials, sr_is_oos}`
  1. Trims rows so they divide evenly into `S` groups (even, capped at 16, i.e. 12,870 splits).
  2. For every half/half split, computes per-strategy Sharpe in-sample and out-of-sample.
  3. Takes the in-sample winner's OOS relative rank `w = (rank+1)/(N+1)`, then the logit `λ = ln(w/(1−w))`.
  4. PBO = mean(λ ≤ 0).
  - `_sharpe` is mean/std with `ddof=1`, not annualized. That's fine because only ranks matter.
- `variants_from_realized_trades(trades, min_p_grid, ...)`: builds the matrix by re-scoring one trade log under several confidence cutoffs. A row is a trade; a variant gets 0 where it skips.

**Behaviours to know.**
- Degenerate inputs (empty, fewer than 2 strategies, fewer than 2 groups) silently return `pbo=0.5`.
- Tail rows that don't fill a group are dropped.
- Rank ties are broken arbitrarily (`argsort().argsort()`).
- No purge between the in-sample and OOS halves. Standard CSCV doesn't purge either.

**MBPE fit.** The strategy columns can be the Optuna trials or threshold grid of one ablation step, with rows = net R per decision hour or per day. A low PBO is evidence that the tuning didn't just pick noise.

## 3. LLM-Auto-Trader: `src/backtest/walk_forward.py` (MIT)

**What it does.**
- `generate_walk_forward_windows(n_observations, train_window, test_window, step) -> list[WalkForwardWindow(train_indices, test_indices)]`
- Rolling, fixed-length train window, with test immediately after. `start` advances by `step`.

**Purge and embargo: none.** There is no purge, no embargo, and no expanding-window option. Units are row positions.

**Gap vs MBPE.** The last ~4h of train samples have labels that resolve inside the test window. MBPE's splitter must drop train samples with `t1 ≥ first test t0`, plus the horizon-matched embargo. This file only provides the index-window loop.

## 4. LLM-Auto-Trader: README statistics + their implementations (MIT)

I read the README "Backtest harness → Statistics produced" section and the code behind it: `src/analysis/backtest_stats.py`, `src/analysis/bootstrap.py`, `src/backtest/report.py`.

- **PSR** (`probabilistic_sharpe_ratio(sr_hat, n, skew, kurt, sr_benchmark=0)`)
  - Implements Bailey & LdP: `Φ((SR−SR*)·√(n−1) / √(1 − γ₃·SR + (γ₄−1)/4·SR²))` with non-excess kurtosis.
  - `deflated_sharpe_ratio` and `expected_max_sharpe` are also there.
  - The formulas match the paper.
- **PSR as used in `report.py:54-61` is miscalibrated.** It is fed:
  - the **annualized** median per-path Sharpe (`sharpe_annualized` × √52), where the formula needs per-period SR;
  - `n` = all per-period returns **pooled across overlapping paths**;
  - `skew=0, kurt=3` hardcoded, so the non-normality correction is switched off.

  All three inflate PSR, so `psr_on_median_sharpe` shouldn't be trusted as written.
- **Block-bootstrap Sharpe CI** (`block_bootstrap_sharpe_ci(returns, periods_per_year, n_resamples=1000, confidence=0.95, block_size=4, seed)`)
  - A **moving-block** bootstrap: fixed block length with uniform random start positions, and a percentile CI.
  - The docstring cites Politis & Romano's *stationary* bootstrap, but block lengths here are fixed, not geometric.
  - `report.py` runs it on returns **concatenated across all paths**, so overlapping data is repeated and blocks span path joins.
- **PBO**: the README points to `compute_pbo` in `cpcv.py` (see §1 caveat), not `cscv.py`.

## 5. freqtrade: `lookahead.py` + `lookahead_helpers.py` (GPL, ideas only)

**What it does.**
- `LookaheadAnalysis.start()` backtests the full timerange once.
- Then, for each trade (up to `targeted_trade_amount`; it gives up below `minimum_trade_amount`), it **re-runs the whole data preparation, indicators and signals on data truncated** to:
  - `open_date + 1 candle` (entry check);
  - `close_date + 1 candle` (exit check).
- `report_signal(result, column, ts) -> bool`: the trade must still appear at the same `open_date` / `close_date` in the truncated backtest. If not, it is counted in `false_entry_signals` / `false_exit_signals`.
- `analyze_indicators(full, cut, pair)`:
  - Aligns the full-history indicator frame to the truncated frame's index (`full_df.loc[cut_df.index]`) and runs `DataFrame.compare`.
  - Any indicator column with a difference on any row of the truncated range goes into `false_indicators`.
  - Comparison is exact; there is no float tolerance.
- The verdict is a bias flag per strategy.
- Trades closed by force-exit (end of data) are skipped to avoid false positives.

**Config overrides (`lookahead_helpers.calculate_config_overrides`).** These remove other reasons a truncated run could differ:
- protections off;
- market orders only (unless `lookahead_allow_limit_orders`);
- `max_open_trades=-1`;
- a 1e9 dry-run wallet and fixed 10k stake, so position sizing never blocks a trade;
- backtest cache disabled.

The helpers also handle a results table and CSV export.

**Comparison with MBPE's planned truncation test** (features on full history vs. history cut at `t`; values at `t` must match):
- **Same core idea:** cut the data, recompute, compare.
- **freqtrade compares more:** every row up to the cut, not just the value at `t`, and it also checks end-to-end signals.
- **freqtrade samples the cut points** at its own trade times, so coverage depends on how often the strategy trades. MBPE can choose cut points directly: random decision hours plus edge cases.
- **The cut-off convention matters.** freqtrade cuts at `open_date + 1 candle` because its signals act on the next candle. MBPE must define `t` as "all bars with close ≤ t and all news with `available_at` ≤ t" and cut both the market and the bitemporal news tables at the same `t`.
- **Exact equality:** fine for deterministic Polars code, but use a tight tolerance if any feature goes through float reductions whose order can vary.

## 6. freqtrade: `recursive.py` + `recursive_helpers.py` (GPL, ideas only)

**What it does.** It runs two checks on indicators only (no trades).

1. **Warm-up sensitivity (`analyze_indicators`).**
   - Recomputes indicators for the **last candle** with several startup lengths (defaults `[199, 399, 499, 999, 1999]` plus the strategy's `startup_candle_count`).
   - Compares each with the full-history run and records the relative difference per indicator per startup length (`dict_recursive[indicator][startup] = (other−self)/self`).
   - `recursive_helpers` prints this as a % table.
   - This catches recursive indicators such as EMA/Wilder ATR/RSI, which only converge with enough history.
   - It also raises if the strategy declares `startup_candle_count < 1`.
2. **Indicator-only look-ahead (`analyze_indicators_lookahead`).** Computes indicators on data ending 10 candles after the range start and compares that last row with the full-history row at the same timestamp.

`recursive_helpers` also forces a timerange to be set and disables the backtest cache.

**Relevance to MBPE.** The truncation test checks *future* leakage. This checks *past-length* dependence, a separate failure mode that matters for MBPE because:
- backtest features are computed over 2020+ history;
- the live/shadow feature run only loads a buffer.

If an ATR/RSI/EWM value at `t` depends on the buffer length, live and backtest will disagree.

---

## What MBPE should adopt / adapt / ignore

| Item | Verdict | Reason | License |
|---|---|---|---|
| `cscv.py` `cscv_pbo` | **Adopt** (copy with attribution) | Correct CSCV PBO; gives a direct overfitting measure for the capped Optuna trials. Change the degenerate-input `0.5` return to raise | Reusable with attribution |
| `backtest_stats.py` PSR / DSR / expected-max-SR formulas | **Adopt** | Match the paper. Feed per-period (per-trade) SR, real skew/kurtosis, and `n` = independent trades | Reusable with attribution |
| `report.py` PSR / bootstrap wiring | **Ignore** | Annualized SR, pooled overlapping `n`, hardcoded normal moments → inflated PSR | Reusable with attribution (but don't) |
| `bootstrap.py` block bootstrap | **Adapt** | Useful for a Sharpe CI on the holdout. Choose the block length from trade autocorrelation (4h holds, hourly decisions), and don't concatenate overlapping paths | Reusable with attribution |
| `cpcv.py` `generate_paths` | **Adapt (ideas)** | The combination loop is fine. Purge/embargo must be rewritten on label `t0/t1` times, not bar counts | Reusable with attribution |
| `cpcv.py` `compute_pbo` | **Ignore** | Not the CSCV estimator; "OOS" rows overlap the in-sample data | Reusable with attribution |
| `walk_forward.py` | **Ignore** (write our own) | No purge/embargo, rolling only. MBPE's locked splitter is purged walk-forward with horizon-matched embargo | Reusable with attribution |
| freqtrade truncate-and-recompute look-ahead test | **Adapt** | Extend MBPE's planned test: compare all rows ≤ t, cut market and news tables at the same `t`, and add an end-to-end check that the prediction at `t` is unchanged | Ideas only |
| freqtrade config neutralisation (sizing, cache, protections) | **Adapt** | Make sure nothing besides the data cut (caches, sizing state) can change the truncated run's output | Ideas only |
| freqtrade recursive / warm-up check | **Adopt (idea)** | Add a CI test: each feature at `t` computed with the live buffer length must match full history within a tolerance. This sets the live buffer size | Ideas only |
| LLM-Auto-Trader `SnapshotBuilder` / LLM decision pipeline | **Ignore** | Out of MBPE scope (README only; code not read) | — |

## Unclear / not verified
- I didn't read LLM-Auto-Trader's tests (`tests/backtest/test_cpcv.py`) or how its CPCV runner aggregates splits into the `summary.json` paths beyond `report.py`.
- freqtrade's `lookahead_helpers` table/CSV code (lines ~26–147) was skimmed for structure only.
- The meaning of the `minimum_trade_amount` / `targeted_trade_amount` defaults comes from the config, which I didn't read.
