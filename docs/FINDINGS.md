# MBPE findings

> Historical results final; forward test running. Tag `mbpe-research-v1` covers the walk-forward evaluation (2022-01-01 to 2026-04-26) and the single pre-registered holdout run (2026-04-26 onward). Tag `mbpe-research-v1.1` adds Phase 4, the checklist extensions, and `mbpe-research-v1.2` adds Phase 5, the longer horizon (both walk-forward only; the holdout stays closed). Phase 6, the full scope doc with a live paper-trading forward test, follows below; its forward test is still running. Every result number below sits in a generated block, rebuilt from committed files in `reports/` by `uv run python -m mbpe.reporting.generated_tables` and checked by `tests/test_progress.py`. The surrounding text only interprets them.

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
- **The pre-registered holdout confirms the negative finding:** the headline configuration loses money on the six unseen months too (holdout section).

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

## Holdout (run once, pre-registered)
The holdout (decision times from 2026-04-26 00:00 UTC to the last labelled hour) was spent once, under the protocol pre-registered in `SCOPE.md` ("Phase 3 holdout test") and committed before any access:
- **Fit:** every configuration was fit once on everything before the holdout, with the same purge, validation slice, calibration, threshold rule and baselines as the walk-forward folds. Tuned models got a fresh 30-trial study on that final validation slice.
- **Configurations:** all 32 evaluated configurations, plus every baseline and the shuffled-label control, are reported in `PROGRESS.md` ("Phase 3 holdout"). Each holdout result sits next to its walk-forward result.
- **Access:** every holdout load is logged in `data/logs/holdout_access.jsonl`. Nothing else touched it.

<!-- generated:holdout_headline -->
| | |
|---|---|
| headline configuration | label set A, taker costs, tuned LightGBM, rung 3 (FinBERT) |
| walk-forward (2022-01-01 → 2026-04-26): mean net R [95% CI] (trades) | -0.122 [-0.160, -0.085] (2,760) |
| **holdout: mean net R [95% CI] (trades)** | **-0.091 [-0.325, +0.115]** (82) |
| holdout: net / gross bps per trade | -13.9 / -0.0 |
| **pre-registered interpretation** | mean net R ≤ 0: **the negative finding is confirmed** |
| configurations with holdout mean net R > 0 (of 32) | 2 |
| of those, with a 95% CI excluding zero | 0 |
<!-- /generated:holdout_headline -->

**Outcome under the pre-registered interpretation: the headline mean net R is at or below zero, so the negative finding is confirmed.**
- **The headline holdout CI is wide,** because the tuned model trades rarely over six months. Taken alone, the holdout couldn't separate a small edge from none. It doesn't need to: it agrees with the walk-forward, which already ruled out an edge after costs at much tighter precision.
- **The few configurations with a positive holdout mean** are counted in the headline table and listed in `PROGRESS.md` ("Phase 3 holdout"). All have CIs that include zero, which is the pattern expected from chance across 32 configurations. They are reported, not acted on; any such configuration that is CryptoBERT-based can't replace FinBERT by pre-registration anyway.

## Conclusion
Under free data, hourly decisions on BTCUSDT perpetuals and this barrier geometry, **there is no tradable edge after costs.** The models pick up a few basis points of gross directional signal, a fraction of the round-trip cost. **News headlines (GDELT, FinBERT sentiment) add nothing over market and derivatives data,** so the pre-registered kill criterion on the news layer is met: across the walk-forward folds and on the holdout. The study is closed at this point. Any new question (horizon, barrier geometry, execution model, data sources) needs its own pre-registration and fresh unseen data.

## Phase 4: checklist extensions
After the conclusion above, three extensions from the trading checklist were each tested once against rung 3 (FinBERT), under a protocol pre-registered in `SCOPE.md` ("Phase 4 pre-registration") before any feature was built:
- **4a: MACD/EMA,** in ATR units;
- **4b: NFP** release-calendar features plus an NFP blackout;
- **4c: order-book depth** from Binance's `bookDepth` archive.

**Evaluation:**
- walk-forward folds only, before the holdout. The spent holdout wasn't re-used;
- no tuning;
- the decision configuration is label set A, taker costs, fixed-settings LightGBM.

**To pass, an extension needs both:**
- a gain of at least 2 bps in gross-bps advantage over the shuffled-label control, compared with rung 3 on the same window, pooled over all test trades;
- a positive mean net R after taker costs.

Passing would only have qualified an extension for a forward test.

<!-- generated:phase4_kill -->
| extension | vs | trades | gross bps | shuffled control gross bps | advantage | rung 3 advantage | **gain (≥ +2.0 to pass)** | folds improved | **mean net R [95% CI]** (> 0 to pass) | passes |
|---|---|---|---|---|---|---|---|---|---|---|
| 4a MACD/EMA | rung 3, Phase 3 window | 3,954 | +3.7 | +0.7 | +3.0 | +2.2 | **+0.8** | 13/18 | **-0.137** [-0.168, -0.104] | no |
| 4b NFP | rung 3, Phase 3 window | 4,340 | +2.4 | -0.0 | +2.4 | +2.2 | **+0.2** | 9/18 | **-0.160** [-0.188, -0.129] | no |
| 4c order book | rung 3, 4c window (2023+) | 2,750 | +2.5 | -1.8 | +4.3 | +3.1 | **+1.2** | 5/10 | **-0.162** [-0.201, -0.121] | no |
| rung 3 + NFP blackout only (not a decision) | rung 3, Phase 3 window | 3,931 | +4.8 | +2.9 | +1.9 | +2.2 | **-0.3** | 7/18 | **-0.127** [-0.158, -0.095] | – (reported only) |
<!-- /generated:phase4_kill -->

**Verdict: no extension passes; the Phase 3 finding stands.** None reaches the bps gain, and each loses money after costs with a 95% CI entirely below zero. No forward test is triggered. Full tables for every window, label set, cost scenario and model are in `PROGRESS.md` ("Phase 4 step 1").

<!-- generated:phase4_window_facts -->
| | |
|---|---|
| Phase 3 window: training from / test folds | 2021-01-01 / 18 folds, 2022-01-01 → 2026-04-26 |
| 4c window (2023+): training from / test folds | 2023-01-01 / 10 folds, 2024-01-01 → 2026-04-26 |
| ±0.2% band (book_imbalance_20bp) first present | 2026-01-15 |
| latest end of any 4c training slice (validation starts there) | 2025-08-06 |
| ±0.2% band present in any training slice | no |
| shuffled-control gross bps, 4 Phase 3-window feature sets (A, taker): min / max | -0.0 / +2.9 (spread 2.9 bps; threshold 2.0) |
<!-- /generated:phase4_window_facts -->

- **NFP blackout alone:** rung 3 with only the NFP blackout added (last row of the kill table) trades about as before, with a slightly lower advantage. So 4b's result isn't driven by the blackout either way. It is reported for interpretation and decides nothing.
- **The 4c window is shorter:** order-book data start in 2023, so 4c and its rung-3 comparison train from 2023 and are tested on fewer folds (table above). Its estimates are noisier than 4a's and 4b's.
- **The ±0.2% band appears only from 2026-01-15:** Binance's `bookDepth` files carried only ±1–5% before that, so `book_imbalance_20bp` is missing for most of the 4c window.
  - **No effect on the result:** the band never appears in any training slice (table above). It appears only in the last fold's validation slice and in test data, so no model could learn from it.
  - **Not dropped:** it stayed in, as pre-registered, rather than being dropped after the fact.
- **NFP is calendar-only:** jobs and payroll news isn't in the GDELT keyword set. 4b tests the release schedule, not news about the release.
- **Live order book:** the planned live depth collector was replaced by the archive files for any future forward test (`SCOPE.md`, "Amendments"). The public REST order book doesn't reach the archive's bands.

**Lesson for the criterion's design: average the shuffled control over several label permutations when a bps threshold decides.**
- **The noise was as large as the threshold.** Each rung had a single shuffled-label control, and that control's gross bps moved between feature sets by about the size of the 2 bps threshold (table above). Some of each extension's measured gain came from its control moving, not from the model; for example, 4a's advantage rose while its own gross bps fell.
- **It didn't change this verdict:** every extension also failed the net-R condition by a wide margin.
- **The fix:** with a threshold this small, the control should be averaged over many permutations, with the same permutations across feature sets, so its noise is small next to the threshold.

## Phase 5: longer horizon
**Question:** does the Phase 3 approach work at a slower cadence, where fixed costs are a smaller share of each trade's typical move?

**Protocol** (pre-registered in `SCOPE.md`, "Phase 5 pre-registration", with one reporting amendment committed before any model):
- **Decisions and barriers:** decisions every 4 hours, a 24 h horizon, and +2 / −1 × daily ATR(14) barriers. One label set.
- **Features and models:** the unchanged Phase 3 features (rungs 1–3); logistic regression and fixed-settings LightGBM, with no tuning.
- **Validation:** the Phase 3 walk-forward folds, with a 48 h embargo and no blackout.
- **Entry rule:** a top-25% rule on validation scores instead of a threshold search.

**Status:** the 2021–2026 data had already been studied in Phases 3–4, so any Phase 5 historical result is development evidence only. Passing could only have triggered a forward test.

**Labels:** at this horizon most trades end at expiry (table below), so a trade's outcome is essentially its 24 h return net of costs. Costs are a much smaller share of the R unit than in Phase 3, and always-long is close to break-even after taker costs (`PROGRESS.md`, "Phase 5 step 1").

<!-- generated:phase5_exit_reasons -->
| period | side | labels | tp | sl | expiry | label = 1 (net R > 0, taker) | mean hold (h) | mean gross R | mean net R (taker) | sd of net R per label (taker) |
|---|---|---|---|---|---|---|---|---|---|---|
| evaluation window 2021-01-01 → holdout | long | 11,640 | 1.9% | 12.0% | 86.1% | 46.4% | 22.5 | +0.017 | -0.024 | 0.63 |
| evaluation window 2021-01-01 → holdout | short | 11,640 | 2.1% | 12.1% | 85.8% | 45.6% | 22.5 | -0.009 | -0.038 | 0.62 |
| test folds 2022-01-01 → holdout | long | 9,450 | 2.1% | 12.1% | 85.9% | 45.9% | 22.5 | +0.016 | -0.026 | 0.63 |
| test folds 2022-01-01 → holdout | short | 9,450 | 1.9% | 12.4% | 85.7% | 45.2% | 22.5 | -0.009 | -0.043 | 0.62 |
<!-- /generated:phase5_exit_reasons -->

<!-- generated:phase5_verdict -->
| | |
|---|---|
| headline | rung 3 LightGBM, taker, pooled over all test trades: 1,209 trades |
| 1. mean net R > 0 | -0.059: no |
| 2. 95% CI lower bound > 0 | [-0.093, -0.022]: no |
| 3. beats all 20 shuffled-label runs (p < 0.05) | beats 5 of 20 (their mean net R: min -0.081, median -0.037, max -0.006): no |
| **historical pass criterion** | **FAIL** |
<!-- /generated:phase5_verdict -->

**Verdict: the pass criterion fails on all three conditions; no forward test; the Phase 3 finding stands at the longer horizon too.**

<!-- generated:phase5_interpretation -->
| | |
|---|---|
| headline mean net R (taker) [95% CI] | -0.059 [-0.093, -0.022] |
| always-long mean net R (taker) [95% CI] | -0.023 [-0.057, +0.015] (1,460 trades; identical to step 1: **yes**) |
| random long-only at the headline's frequency, 20 seeds: median [5%, 95%] | -0.026 [-0.035, -0.019] |
| share of the headline's trades that are long | 46.7% |
| headline mean net R exceeds always-long's | no |
| **interpretation rule outcome** | **not applicable (criterion not passed)** |
<!-- /generated:phase5_interpretation -->

**Interpretation rule (amendment):** it doesn't apply, since the criterion failed. The headline also does worse than always-long and than random long-only entries at its own frequency, and its trades are split between long and short. So there's no drift exposure to explain away; the model simply has no edge.

<!-- generated:phase5_controls -->
| scenario | rung | LightGBM: mean net R | shuffled-label runs: min / median / max mean net R | runs beaten | shuffled: median gross bps |
|---|---|---|---|---|---|
| taker | 1 | -0.028 | -0.090 / -0.035 / +0.007 | 13/20 | +0.6 |
| taker | 2 | -0.016 | -0.085 / -0.039 / -0.003 | 19/20 | -1.5 |
| taker | 3 | -0.059 | -0.081 / -0.037 / -0.006 | 5/20 | -0.8 |
| maker_entry_tp | 1 | -0.014 | -0.076 / -0.021 / +0.021 | 13/20 | +0.6 |
| maker_entry_tp | 2 | -0.002 | -0.071 / -0.025 / +0.011 | 19/20 | -1.5 |
| maker_entry_tp | 3 | -0.045 | -0.067 / -0.023 / +0.007 | 5/20 | -0.8 |

| baseline | scenario | trades | mean net R [95% CI] | net / gross bps |
|---|---|---|---|---|
| always_long | taker | 1,460 | -0.023 [-0.057, +0.015] | -10.3 / +5.4 |
| always_long | maker_entry_tp | 1,460 | -0.009 [-0.043, +0.029] | -5.2 / +5.4 |
| always_short | taker | 1,475 | -0.055 [-0.092, -0.020] | -18.1 / -5.8 |
| always_short | maker_entry_tp | 1,475 | -0.041 [-0.078, -0.006] | -13.0 / -5.8 |
<!-- /generated:phase5_controls -->

- **News doesn't help here either.** Rung 3 (with news) is the weakest LightGBM rung.
- **No configuration has a 95% CI lower bound above zero.** A few logistic-regression configurations sit near break-even, with positive point estimates under maker costs. They aren't the pre-registered decision configuration, and choosing one now would be the kind of post-hoc selection the protocol rules out (full table in `PROGRESS.md`).
- **The 20 shuffled-label runs spread widely.** Requiring the headline to beat all of them, not a single control, is what keeps chance from passing. This applies the Phase 4 lesson.

**Lesson: a quantile entry rule needs untied scores.**
- The rule "score ≥ the 75th percentile of the validation scores" assumed distinct scores. Isotonic calibration produces ties, so it usually selected far more than 25% of decisions.
- Where the calibrated scores were constant, it selected every decision. The rule was applied exactly as pre-registered and the realized shares are reported:

<!-- generated:phase5_entry_shares -->
| model | rung | side | validation share selected: median [min, max] | test share selected: median | fold-sides selecting ≥ 99% of validation (constant scores) |
|---|---|---|---|---|---|
| lgbm | 1 | long | 39% [27%, 100%] | 40% | 1/18 |
| lgbm | 1 | short | 57% [26%, 96%] | 54% | 0/18 |
| lgbm | 2 | long | 38% [25%, 100%] | 43% | 3/18 |
| lgbm | 2 | short | 43% [26%, 81%] | 40% | 0/18 |
| lgbm | 3 | long | 38% [27%, 100%] | 38% | 1/18 |
| lgbm | 3 | short | 44% [25%, 100%] | 45% | 1/18 |
| logreg | 1 | long | 45% [26%, 100%] | 47% | 2/18 |
| logreg | 1 | short | 46% [26%, 100%] | 42% | 1/18 |
| logreg | 2 | long | 44% [25%, 100%] | 53% | 1/18 |
| logreg | 2 | short | 54% [25%, 100%] | 45% | 1/18 |
| logreg | 3 | long | 45% [26%, 97%] | 50% | 0/18 |
| logreg | 3 | short | 53% [25%, 93%] | 60% | 0/18 |
<!-- /generated:phase5_entry_shares -->

- **Fix for any future use:** rank on the uncalibrated model score, or break ties explicitly, so the rule selects the intended share.

## Phase 6: the full scope doc, and a forward test
**Why:** after Phase 5, the remaining work was to deliver every component of the project's scope doc rather than stop at a negative finding. Each piece was pre-registered in `SCOPE.md` ("Phase 6") before it was built or run:
- **Data:** mark-price and premium-index features, and regulatory and major-announcement news flags; plus forward-only collectors for liquidations, RSS and Reddit.
- **Models:** the scope doc's three-class primary model (stop / expiry / take-profit first; LightGBM, with CatBoost alongside) and a meta-labeling filter trained only on out-of-sample predictions (P ≥ 0.60).
- **Analysis:** SHAP, a targets table, regimes and failure cases.
- **Live system:** a live system and an internal paper-trading simulator.

**Status:** 2021–2026 has been studied since Phase 3 and the holdout is spent, so every historical Phase 6 result is **development evidence**. Only the forward test can establish an edge.

### Historical evaluation (one pre-registered run)

<!-- generated:phase6_c_headline -->
| | |
|---|---|
| headline | LightGBM three-class primary + meta-filter (P ≥ 0.60), label set A, taker, folds 4-17 |
| **trades / mean net R [95% CI]** | 13 / **-0.172 [-0.412, +0.007]** |
| net / gross bps per trade | -10.8 / +3.1 |
| primary only, same folds: trades / mean net R [95% CI] | 3,165 / -0.222 [-0.261, -0.181] |
| shuffled-label runs (meta-filtered): runs with trades / min / median / max mean net R | 19 / -0.680 / -0.071 / +1.865 |
| headline beats how many shuffled runs | 5 of 19 |
| random entry / volatility filter at its frequency: median mean net R | -0.259 / -0.154 |
| always-long, same folds: mean net R [95% CI] | -0.244 [-0.269, -0.217] |
<!-- /generated:phase6_c_headline -->

- **The pre-registered system barely trades,** and what it trades doesn't make money after costs.
- **The meta-model's probabilities are well calibrated**, but a candidate's realistic chance of success sits far below 0.60. So the scope doc's fixed threshold selects only a rare tail:

<!-- generated:phase6_c_meta_p -->
| primary | candidates scored by the meta-model | actual success rate (net R > 0) | P(success): median | 90th pct | 99th pct | share ≥ 0.60 |
|---|---|---|---|---|---|---|
| lgbm | 7,352 | 34.4% | 0.36 | 0.41 | 0.55 | 0.20% |
| catboost | 6,005 | 35.2% | 0.35 | 0.42 | 0.57 | 0.87% |
<!-- /generated:phase6_c_meta_p -->

- **Primary vs meta-filter:** at the validation-chosen threshold the meta-filter improves on the primary model and beats every shuffled-label run that traded, but it still loses after taker costs. No configuration, LightGBM or CatBoost, has a 95% CI entirely above zero (full tables in `PROGRESS.md`, "Phase 6 Workstream C").
- **Targets** (research objectives from the scope doc, not pass criteria):

<!-- generated:phase6_c_targets -->
| model | strategy | period | trades | hit rate (net R > 0) — target 54–58% | direction right (gross R > 0) | Sharpe (annualized, 1% risk) — target ≥ 1.8 | profit factor — target ≥ 1.6 | max drawdown — target < 12% | targets met |
|---|---|---|---|---|---|---|---|---|---|
| lgbm | primary only | folds 0-17 | 4,522 | 36.1% | 44.4% | -5.60 | 0.65 | 100.0% | 0 of 4 |
| lgbm | primary + meta (P ≥ 0.60) | folds 4-17 | 13 | 30.8% | 38.5% | -0.29 | 0.66 | 4.3% | 1 of 4 |
| lgbm | primary + meta (validation threshold) | folds 4-17 | 1,578 | 38.5% | 46.4% | -2.69 | 0.75 | 88.7% | 0 of 4 |
| catboost | primary only | folds 0-17 | 3,988 | 37.3% | 44.4% | -4.15 | 0.72 | 99.8% | 0 of 4 |
| catboost | primary + meta (P ≥ 0.60) | folds 4-17 | 41 | 43.9% | 43.9% | -0.11 | 0.94 | 8.9% | 1 of 4 |
| catboost | primary + meta (validation threshold) | folds 4-17 | 1,552 | 37.6% | 45.7% | -2.35 | 0.78 | 85.4% | 0 of 4 |
<!-- /generated:phase6_c_targets -->

**What the models use (SHAP):**
- **The primary models lean mostly on time of day, day of week and volatility.** News carries a sizeable share in total, spread thinly over many features, and the new mark/premium and regulatory features carry little.
- **The meta-filter is mostly a volatility filter.**
- **Importances are stable from fold to fold.**
- SHAP shows what a model uses, not that it profits:

<!-- generated:phase6_c_shap -->
Top 10 features by mean |SHAP| (LightGBM; share of the model's total):

- **primary_long:** hour_of_day (technical, 9.6%), atr_1h_pct (technical, 6.4%), day_of_week (technical, 3.6%), rv_24h (technical, 3.4%), vol_z_7d (technical, 3.1%), vwap_day_dist_atr (technical, 3.1%), oi_chg_24h (derivatives, 2.5%), cvd_norm_24h (technical, 2.3%), premium_z_7d (mark_premium, 2.1%), ret_1h (technical, 1.9%)
- **primary_short:** hour_of_day (technical, 10.1%), atr_1h_pct (technical, 6.7%), day_of_week (technical, 3.8%), dist_low_24h_atr (technical, 3.6%), vol_z_7d (technical, 3.4%), rsi_14_1h (technical, 2.6%), oi_chg_24h (derivatives, 2.6%), premium_z_7d (mark_premium, 2.5%), news_macro_new_clusters_24h (news, 2.4%), rv_24h (technical, 2.0%)
- **meta:** rv_1h (technical, 23.9%), atr_1h_pct (technical, 23.5%), rv_24h (technical, 18.2%), p_expiry (meta input, 9.7%), rv_4h (technical, 8.6%), p_sl (meta input, 6.5%), p_tp (meta input, 5.4%), side (meta input, 4.3%)

Share of total |SHAP| by feature group:

| model | technical | derivatives | news | calendar | mark_premium | regulatory |
|---|---|---|---|---|---|---|
| lgbm primary_long | 56.1% | 6.7% | 25.9% | 2.3% | 5.0% | 4.0% |
| lgbm primary_short | 54.3% | 6.4% | 27.3% | 2.5% | 5.9% | 3.6% |
| catboost primary_long | 56.8% | 8.0% | 23.5% | 2.1% | 5.5% | 4.1% |
| catboost primary_short | 55.9% | 7.3% | 25.0% | 2.3% | 6.7% | 2.9% |

Fold-to-fold stability (Spearman rank correlation of feature importances between folds):

| model | folds | mean ρ | min ρ |
|---|---|---|---|
| catboost meta | 14 | 0.63 | 0.02 |
| catboost primary_long | 18 | 0.80 | 0.66 |
| catboost primary_short | 18 | 0.86 | 0.72 |
| lgbm meta | 14 | 0.70 | 0.00 |
| lgbm primary_long | 18 | 0.79 | 0.59 |
| lgbm primary_short | 18 | 0.86 | 0.71 |
<!-- /generated:phase6_c_shap -->

### The live system
Built to the scope doc's integration and paper-trading-readiness stage (`mbpe/live/`, operated via `docs/RUNBOOK.md`). Each hour it:
1. tops up market data from Binance's free REST API (the archive stays the source of record);
2. extends the news clusters and sentiment;
3. computes every feature with the backtest's code;
4. applies the frozen model;
5. paper-trades with the backtest's own label code and position rule.

**No orders and no exchange keys.** Missed, late and stale hours mean no decision.

**Checks before it went live:**
- the live paper loop, run hour by hour, reproduces the backtest's labels and position rule exactly;
- the incremental news processing equals a full run;
- the frozen model's predictions equal the research code's.
- On archived windows, the REST API returns rows identical to the archive:

<!-- generated:phase6_d_rest_archive -->
| dataset | 6 h windows | archive rows | REST rows | rows identical (every compared column) |
|---|---|---|---|---|
| futures_klines_1m | 5 | 1,800 | 1,800 | 1,800 |
| spot_klines_1m | 5 | 1,800 | 1,800 | 1,800 |
| metrics | 5 | 355 | 360 | 355 |
| funding | 5 | 15 | 15 | 15 |
| mark_price_1m | 5 | 1,800 | 1,800 | 1,800 |
| premium_index_1m | 5 | 1,800 | 1,800 | 1,800 |
<!-- /generated:phase6_d_rest_archive -->

**One finding from that comparison:** REST labels each open-interest snapshot 5 minutes later than the archive, with identical values.
- **Live fix:** live rows are relabeled to the archive convention, so live features equal the training features.
- **Research caveat:** the backtests' "before t" rule may have admitted the open-interest snapshot taken at the decision time itself. It's available within seconds, before the 1-minute entry, so it's tradable, and it could only have flattered results that found no edge. It's recorded as a limitation, not corrected retroactively.

### Forward test (running)
- **The system:** the frozen LightGBM primary + meta-filter, trained on data before 2026-04-26, paper-traded from 2026-10-29 13:00 UTC. The model version and threshold are fixed in a start record committed before the first decision.
- **Threshold:** the meta-filter's validation-chosen threshold, not 0.60. At 0.60 the system would trade only rarely (meta-probability table above), so 100 trades would take years.
  - This was a dated amendment, **made after seeing the historical results**, in which this variant still lost money after costs. The forward test therefore has a negative historical expectation: it tests the scoped system as built, not a candidate edge.
- **Verdict** (pre-registered): only after ≥ 6 months **and** ≥ 100 closed paper trades.
  - Pass = mean net R after taker costs > 0 with the 95% CI lower bound > 0, **and** above always-long over the same hours.
  - No change to the model, threshold or rules before then. A daily parity check compares the live system with the archive.
- **Until the verdict, the finding stands:** no edge after costs.
