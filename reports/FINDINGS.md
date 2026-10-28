# MBPE findings: DRAFT

> **DRAFT, not final.** It covers the walk-forward evaluation up to the holdout start (2026-04-26 00:00 UTC). **The holdout has not been run.** Every result number below sits in a generated block, rebuilt from committed files in `reports/` by `uv run python -m mbpe.reports.progress` and checked by `tests/test_progress.py`. The surrounding text only interprets them.

## Question
Can hourly signals from BTCUSDT perpetual market data, derivatives data and news headlines predict triple-barrier trade outcomes well enough to make money **after costs**? In particular, does adding news improve on market data alone? The pre-registered kill criterion: if the news layer doesn't improve net-of-costs results across folds and the holdout, that is the finding.

## Headline numbers
<!-- generated:findings_headline -->
| | |
|---|---|
| highest gross edge (A, taker) | lgbm_tuned rung 2: +8.3 bps per trade |
| shuffled-label control's gross edge (same threshold procedure) | +2.5 bps |
| gross edge beyond the shuffled control | +5.8 bps |
| costs of that configuration (gross − net) | 13.9 bps per trade |
| best mean net R of any model configuration [95% CI] | -0.048 [-0.068, -0.028] (B, maker_entry_tp, lgbm rung 2) |
| always-trade baseline, A taker (mean of long and short) | -0.233 R |
| news ablation, A taker, untuned LightGBM rung 1 / 2 / 3 | -0.130 / -0.135 / -0.127 R |
| news ablation, A taker, tuned LightGBM rung 1 / 2 / 3 | -0.126 / -0.093 / -0.122 R |
| deflated Sharpe ratio of the best configuration | 0.0000 (N = 2,188 trials) |
| probability of backtest overfitting (CSCV) | 0.086 across 32 configurations |
<!-- /generated:findings_headline -->

**Short version:**
- **The models do find a small directional signal,** a few basis points per trade in gross terms beyond what the threshold procedure alone produces.
- **It is a fraction of the round-trip trading cost,** so no configuration makes money after costs.
- **News features don't improve on market data.**
- **The overfitting analysis leaves no sign of a real profitable edge.**

## What was tested
- **Market:** BTCUSDT USD-M perpetual. Decisions every hour, with one open position at most.
- **Labels:** symmetric triple-barrier per side (long, short): take-profit +2 ATR, stop −1 ATR, expiry 4 h. Entry at the open of the 1-minute candle after the decision (1 min latency). The stop is assumed hit first when both barriers fall in one candle, and gaps through the stop fill at the open.
  - **Label set A:** Wilder ATR(14) on 1h bars (the headline set).
  - **Label set B:** the same on 4h bars, with wider barriers.
- **Costs:**
  - **`taker` (headline):** 0.05% fee + 2 bps slippage per side, plus funding for every settlement inside the trade.
  - **`maker_entry_tp` (optimistic):** 2 bps maker fee and no slippage on entry and take-profit exits. Limit-order fills aren't modeled.
  - **Results:** in R (net PnL ÷ stop distance) and in basis points.
- **Features**, organized as ablation rungs:
  - (1) **technical:** returns, realized volatility, ATR, RSI, %B, 24 h range, volume z-score, taker flow, volume profile, anchored VWAP, spot share and spot–perp basis, calendar time;
  - (2) **+ derivatives:** funding level, z-score and timing; open-interest change and the open-interest/price quadrant;
  - (3) **+ news and calendar:** GDELT headline volume, novelty and sentiment per crypto/macro keyword group, macro tone, and timing relative to US CPI and FOMC releases.
- **Models:** logistic regression and LightGBM (fixed conservative settings), one per side, with isotonic calibration. LightGBM was also tuned with a capped Optuna search on label set A / taker.

## How (data, leakage controls, validation)
- **Data (free sources only):**
  - Binance public archive: 1m klines (futures, spot), 5-min metrics, funding, with a REST fallback for months not yet archived, cross-checked against the archive;
  - GDELT GKG headlines via BigQuery for the backfill, and the 15-minute files live. Backfill and live parsing were checked identical on two full days.
- **Leakage controls:**
  - every feature uses only data with timestamps before the decision time;
  - news counts as available 15 minutes after its GDELT batch label. GDELT publishes about 9 minutes *before* the label, so this rule is conservative;
  - truncation tests on every market, news and calendar feature: computing at t from data cut at t gives the same value as from full data;
  - tests confirm a deliberately leaky feature is caught;
  - near-duplicate news clusters are causal and never make a story available earlier than its first copy;
  - data gaps and GDELT outages give NaN, never zero.
- **Validation:**
  - **folds:** purged walk-forward with expanding training from 2021-01-01, quarterly test folds from 2022-01-01 to the holdout start;
  - **purging:** training labels whose window reaches the test fold are purged;
  - **validation slice:** the last 20% of each training window, used only for early stopping, calibration and the threshold;
  - **thresholds:** chosen on validation (max mean net R with ≥ 100 trades);
  - **trading rules:** one position at a time, conflicting signals skipped, and entries blocked around CPI/FOMC releases using only information known at entry.
- **Baselines** for every model, all at the model's own trade frequency per fold:
  - always-long and always-short;
  - random entry (20 seeds);
  - a volatility filter: random direction in high-ATR hours, with the cutoff from validation (20 seeds);
  - a shuffled-label control.
- **Pre-registration:** every protocol choice was written to `SCOPE.md` before the corresponding results existed. That covers the holdout date, the rungs, the position and threshold rules, the tuning budget, and FinBERT as the primary sentiment scorer.

## Results
1. **No configuration is profitable after costs.** Every model's mean net R has a 95% block-bootstrap interval entirely below zero, in both label sets and both cost scenarios. Full per-fold tables are in `PROGRESS.md` (Phase 3 steps 1–2).
2. **The signal is small and cost-dominated.** The models beat random entry in most folds. In R, much of that advantage comes from trading high-volatility hours, where fixed costs are smaller in R; the volatility-filter baseline captures most of it. In basis points, where costs are identical for every trade, the best configuration's gross edge beyond the shuffled control is a few bps, against a round-trip cost several times larger (headline table).
3. **Tuning doesn't change the conclusion.** The best validation trials look positive, but test results remain negative. The shrinkage is what selecting the best of many trials on the same slice produces:

<!-- generated:phase3_step2_tuning -->
| rung | untuned LightGBM: test mean net R [95% CI] | tuned: test mean net R [95% CI] | tuned: trades | tuning trials | validation, best trial per fold (mean) | folds with best validation > 0 | validation, median trial |
|---|---|---|---|---|---|---|---|
| 1 | -0.130 [-0.162, -0.098] | -0.126 [-0.164, -0.088] | 2,705 | 540 | +0.025 | 11/18 | -0.062 |
| 2 | -0.135 [-0.169, -0.103] | -0.093 [-0.136, -0.048] | 2,433 | 540 | +0.044 | 16/18 | -0.047 |
| 3 | -0.127 [-0.158, -0.096] | -0.122 [-0.160, -0.085] | 2,760 | 540 | +0.020 | 11/18 | -0.069 |
<!-- /generated:phase3_step2_tuning -->

4. **News ablation: rung 3 doesn't improve on rungs 1–2,** untuned or tuned (headline table). That's with FinBERT sentiment. CryptoBERT, a secondary comparison that can't replace FinBERT by pre-registration, is worse in every configuration:

<!-- generated:phase3_step2b_cryptobert -->
| label set | scenario | model | scorer | trades | mean net R [95% CI] | net bps | gross bps | random: net R (median) | vol filter: net R (median) | folds won vs random | vs vol filter |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | taker | lgbm r3 | finbert | 3,946 | -0.127 [-0.158, -0.096] | -9.2 | +4.7 | -0.225 | -0.160 | 16/18 | 12/18 |
| A | taker | lgbm r3 | cryptobert | 4,916 | -0.151 [-0.180, -0.121] | -10.6 | +3.3 | -0.225 | -0.179 | 16/18 | 11/18 |
| A | maker_entry_tp | lgbm r3 | finbert | 3,946 | -0.055 [-0.086, -0.024] | -3.5 | +4.7 | -0.137 | -0.102 | 16/18 | 15/18 |
| A | maker_entry_tp | lgbm r3 | cryptobert | 5,021 | -0.077 [-0.107, -0.047] | -5.2 | +3.1 | -0.134 | -0.116 | 16/18 | 12/18 |
| B | taker | lgbm r3 | finbert | 3,793 | -0.077 [-0.097, -0.056] | -11.3 | +2.6 | -0.092 | -0.078 | 11/18 | 11/18 |
| B | taker | lgbm r3 | cryptobert | 4,184 | -0.093 [-0.113, -0.073] | -14.1 | -0.1 | -0.099 | -0.086 | 9/18 | 9/18 |
| B | maker_entry_tp | lgbm r3 | finbert | 4,220 | -0.051 [-0.071, -0.032] | -7.1 | +1.8 | -0.060 | -0.057 | 11/18 | 12/18 |
| B | maker_entry_tp | lgbm r3 | cryptobert | 4,211 | -0.060 [-0.080, -0.040] | -9.0 | -0.2 | -0.063 | -0.058 | 10/18 | 9/18 |
| A | taker | lgbm_tuned r3 | finbert | 2,760 | -0.122 [-0.160, -0.085] | -8.8 | +5.1 | -0.211 | -0.137 | 15/18 | 10/18 |
| A | taker | lgbm_tuned r3 | cryptobert | 2,698 | -0.140 [-0.178, -0.099] | -11.1 | +2.8 | -0.233 | -0.145 | 14/18 | 10/18 |
<!-- /generated:phase3_step2b_cryptobert -->

5. **The overfitting analysis leaves no sign of a real profitable edge.** The deflated Sharpe ratio of the best configuration is essentially zero. Its Sharpe is negative even before deflation, and the luck benchmark at this trial count is far higher. PBO across configurations is low, but only because their ranking is driven by cost structure. The in-sample winner loses money out of sample in every CSCV split:

<!-- generated:phase3_step3_dsr -->
| quantity | value |
|---|---|
| best configuration (highest daily Sharpe in the test folds) | A/maker_entry_tp/lgbm/r3/finbert |
| trading days in the test folds | 1,576 |
| daily Sharpe (not annualized) / annualized | -0.0802 / -1.53 |
| skewness / kurtosis of daily returns | +0.02 / 5.34 |
| trials counted (N) | 2,188 = 2,160 tuning trials + 28 untuned evaluated configurations |
| variance of daily Sharpe across evaluated configurations (V) | 0.00289 |
| expected maximum daily Sharpe under no skill, SR* (annualized) | +0.1866 (+3.57) |
| PSR vs 0: P(true Sharpe > 0) | 0.0008 |
| **Deflated Sharpe ratio: P(true Sharpe > SR*)** | **0.0000** |
<!-- /generated:phase3_step3_dsr -->

<!-- generated:phase3_step3_pbo -->
| quantity | value |
|---|---|
| configurations (strategies) | 32 |
| days / sub-groups S / days per group | 1,576 / 16 / 98 |
| CSCV splits C(S, S/2) | 12,870 |
| **PBO: P(in-sample best ranks in the bottom half out of sample)** | **0.086** |
| median logit of the in-sample winner's out-of-sample rank | +1.723 |
| in-sample winner: mean daily Sharpe in sample / out of sample | -0.0708 / -0.1171 |
| share of splits where the in-sample winner loses money out of sample | 1.000 |
| most frequent in-sample winner (share of splits) | A/maker_entry_tp/lgbm/r3/finbert (0.48) |

Within each label set x cost scenario (same costs, so the configurations are alternatives):

| group | configurations | PBO | in-sample winner loses money out of sample (share of splits) |
|---|---|---|---|
| A/maker_entry_tp | 7 | 0.272 | 1.000 |
| A/taker | 11 | 0.031 | 1.000 |
| B/maker_entry_tp | 7 | 0.590 | 1.000 |
| B/taker | 7 | 0.329 | 1.000 |
<!-- /generated:phase3_step3_pbo -->

## Limitations
- **Costs:** the taker cost model is simple (fixed fee and slippage per side). The maker scenario is an upper bound, since queue position, non-fills and adverse selection aren't modeled. Either way the result doesn't hinge on a few bps: the gap between signal and cost is large.
- **News coverage:** GDELT's English GKG has outages, heavy in late 2020 and a 17-day gap in mid-2025. Ablations start in 2021 and outages are NaN.
- **Headlines only,** matched on title or URL with a fixed keyword list. No article bodies, no social media, and no paid sources such as Bloomberg or Reuters wires.
- **Sentiment:** FinBERT is a general financial-tone model, not trained on crypto headlines. Its agreement with LLM-judged labels is only moderate. The hand-labeled benchmark is not done; it is optional and doesn't decide the scorer.
- **The macro calendar uses actual release times,** so the few rescheduled CPI releases are known slightly early in the backtest.
- **One market and one barrier geometry:** BTCUSDT, +2/−1 ATR, 4 h horizon. Other horizons, barrier shapes or instruments weren't explored, by design.
- **The overfitting statistics are themselves estimates.** The DSR variance comes from the evaluated configurations, not the tuning trials. PBO from one dataset is noisy (see `PROGRESS.md`, Phase 3 step 3).

## Open decision: the holdout
The holdout (decision times from 2026-04-26 00:00 UTC) is untouched; the guard logs any access and none has occurred. It can be spent once. Options:
- **(a) Run the pre-registered evaluation on the holdout as it stands,** to confirm the negative result on unseen data. This closes the study under the kill criterion.
- **(b) Keep the holdout unspent** and record the walk-forward result as the finding. It's already decisive, and spending the holdout on a configuration with no edge adds little.
- **(c) Change the question** (e.g. horizon, barrier geometry, cost model or data sources) and pre-register that change first. The holdout would then belong to the new question, and its results would count only once.

This draft makes no recommendation between them. It's your call.
