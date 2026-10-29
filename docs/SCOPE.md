# MBPE — Locked MVP Scope (Oct 2026)

This scope replaces the original proposal PDF (2-week plan) and Sarosh's scope doc (6–7 weeks) wherever they disagree.

## Goal
Run a leak-proof test of whether the signals have an edge after costs. No live money. The targets (54–58% hit rate, Sharpe ≥ 1.8, PF ≥ 1.6, max DD < 12%) are research objectives, not deliverables.
**Kill criterion:** if the news layer doesn't improve net-of-costs results across the folds and the holdout, that is the finding.

## Market & labels
- Trade **BTCUSDT perpetual** only. Use BTCUSDT spot, mark/premium index and (optionally) ETHUSDT as features.
- 1m candles for barrier labeling, hourly decisions, features over 1h/4h/1d windows, history from ~2020.
- Symmetric triple-barrier **per side** (+2 ATR / −1 ATR, 4h expiry). Target = trade profitable **net of fees, slippage and funding**.
- Fixed 1% risk per trade, results in R-multiples.

## Data (free only)
- Binance public archive: klines, funding, 5-min OI metrics (**verify coverage on day 1**).
- News: GDELT DOC API backfill plus a vetted free crypto news archive. CryptoPanic free tier and RSS run as live collectors from day 1.
- CPI/FOMC calendar used as flags plus a blackout window.
- News is headline text and timestamps only. Lag of 15–30 min before a headline counts as available. FinBERT validated on ~200 hand-labeled headlines. Near-duplicate headlines removed.

## Validation
- Purged walk-forward with a horizon-matched embargo and overlap sample weights.
- The final 6 months are an untouched holdout.
- Baselines: always-long, random, logistic regression.
- Ablation ladder: technical → +derivatives → +news.

## Phases (~4–5 weeks)
decisions → market/derivatives data + labels → news pipeline → features → modeling/validation → holdout + shadow mode (live predictions logged, no orders)

## Stack
Python 3.11+, uv, ruff, pytest · Polars (+pandas where needed) · Parquet + DuckDB · pandera · pydantic-settings + YAML · MLflow · cron/systemd · httpx/ccxt · single VM + Docker Compose · Streamlit · Telegram/Slack alerts
Models: LightGBM per side (CatBoost as a check), isotonic/Platt calibration, Optuna with capped trials, custom purged walk-forward splitter, SHAP TreeExplainer
NLP: ProsusAI/finbert by default, compared against CryptoBERT and a small local LLM on the labeled set; sentence-transformers for dedup
Patterns: bitemporal records (`event_time` + `available_at`), as-of joins, source adapters, immutable raw → derived layers, pure feature registry shared by backtest and live, truncation leakage test in CI, holdout guard, append-only prediction log

## What changed from the source docs
| Source said | Locked |
|---|---|
| Social media (Reddit/PRAW, X API): mention velocity, viral ratio | **Out** of MVP |
| Coinglass liquidations | **Deferred** (no free history) |
| Meta-labeling filter, P ≥ 0.60 | **Deferred** |
| Fractional Kelly 0.25×, 1–1.5% risk, dynamic vol scaling | Fixed 1% risk. The ATR stop already keeps dollar risk constant |
| 3-class model / directional {−1, +1} | Binary model per side (long, short), target = net profitable |
| 15m candles (scope doc) / 1h–4h (proposal) | 1m for labeling, hourly decisions |
| 15-min sentiment lag | 15–30 min |
| Sentiment weighted by engagement and source credibility | Headline + timestamp only |
| Sentiment dispersion across platforms | Only across news sources (no social) |
| 48h embargo | Horizon-matched embargo |
| Paper-trading loop | Shadow mode: predictions logged, no orders |
| VectorBT, pandas | Custom backtest on Polars |
| 2 weeks / 6–7 weeks | ~4–5 weeks |
| Targets as thresholds | Research objectives + kill criterion |

## Still open (resolve in the "decisions" phase)
- Fee and slippage numbers (taker vs maker, bps per side).
- Entry timing: open of the next 1m bar after the hourly decision?
- ATR reference: which timeframe and lookback, and computed as of the decision time.
- Size of the CPI/FOMC blackout window (the proposal used ±30 min).
- What happens when the long and short models both fire in the same hour.

## Decided during feasibility (Oct 2026)
- **News matching rule:** case-insensitive match on page title or URL. The same rule applies to backfill and live, so news-volume features mean the same thing in both. Also check GKG theme tags for crypto themes in whichever source is used.
- **News backfill source:** GDELT GKG bulk data, not the DOC search API (250-article cap, IP throttling). **Via BigQuery** (`gdelt-bq.gdeltv2.gkg_partitioned`; dry run 6.92 GB for Jan 2024, ≈512 GB for the full range, inside the 1 TB/month free tier). Fallback is stream-and-filter on a cloud VM.
- **Backfill/live parity:** before trusting the backtest, pull one full day through both paths (backfill source and live 15-min files) and confirm matched rows, titles and tone are identical.
- **Missing GDELT 15-min batches (~2%)** mean *no data*, not *no news*. Carry a coverage flag so an outage is never read as a calm market.
- **Binance metrics duplicates (Sep 2020 – May 2021):** drop exact duplicate rows on load. Fail loudly if two rows share a timestamp but differ in any value; that is a real data problem, not a harmless duplicate.
- **BigQuery cost:** the dry-run figure is the real cost. Filtering doesn't reduce bytes scanned, and neither does a two-step query, since reading `Extras` for any row scans the whole column. If the cost is over the free allowance even spread across a few months, use the cloud-VM streaming job. No further probing.

## Gap handling (archive gaps in metrics and spot)
Gaps are holes in Binance's archive, not exchange outages: futures 1m candles have no gaps at all, including on the days metrics are short. Live mode reads OI from the REST API, so it shouldn't have the same gaps. The rule depends on the kind of value:
- **Levels** (open interest, long/short ratios, prices): carry the last known value forward only up to a staleness limit (~15–30 min for 5-min OI). Past that the feature is NaN. LightGBM handles NaN natively, so don't invent values.
- **Flows** (volume, taker buy/sell volume): never forward-fill, because that invents trading that didn't happen and skews spot-vs-perp ratios. In a gap a flow feature is NaN, or is computed over the minutes that exist plus a coverage ratio showing how much of the window was present.
- **Changes** (e.g. 1h OI change): both endpoints must be valid (not stale), otherwise NaN.
- Long gaps (2021-06-22, 2024-02-16) simply give NaN features for those hours. The final 6-month holdout (from 2026-04) has no flagged days in any dataset. The last flags were metrics on 2025-08-29 and spot on 2023-03-24.

## Feature exclusions (Phase 1, Oct 2026)
- **Top-trader long/short ratios** (`count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio`) are excluded from MVP features. They are blank for most of 2022 (2021-12-30 → 2022-12-14).
- **Taker buy/sell ratio** is computed from futures 1m kline taker volume (`taker_buy_volume` vs `volume − taker_buy_volume`), not from metrics' `sum_taker_long_short_vol_ratio` (blank 2021-12-30 → 2022-05-09). Kline volume is a flow, so the flows rule applies.

## Label and cost variants (pre-registered, 2026-10-26)
Registered before any modeling. **No further label or cost variants will be added after results are seen.** Results are reported for every combination below, not only the best one.

| Variant | Definition | File / name |
|---|---|---|
| Label set A | Current config: Wilder ATR(14) on 1h bars, +2 / −1 ATR, 4h horizon | `config/labels.yaml` → `data/labels/triple_barrier.parquet` |
| Label set B | Identical to A, except ATR = Wilder ATR(14) on **4h bars** built from 1m candles, using only 4h bars closed at or before t | `config/labels_B.yaml` → `data/labels/triple_barrier_B.parquet` |
| Cost scenario `taker` | Every fill pays the 0.05% taker fee + 2 bps slippage. **Headline result**; stored `net_R` / `label` use it | `scenario_net_r(..., "taker")` |
| Cost scenario `maker_entry_tp` | Entry and take-profit exits at a 2 bps maker fee with no slippage; stop and expiry exits stay taker fee + slippage. **Optimistic**: limit-order fills (queue position, non-fills, adverse selection) aren't modeled | `scenario_net_r(..., "maker_entry_tp")` |

## GDELT backfill query (Phase 1 step 3, 2026-10-26)
- **V2Themes excluded from the BigQuery backfill.** Dry runs for 2020-09-01 → 2026-10-26: **486.3 GB** without V2Themes and **1,254.0 GB** with it, over the 900 GB budget (1 TB/month free allowance). So theme-based matching is not part of the MVP backfill. If it's revisited, it has to be scoped separately, e.g. only for already-matched rows' days, spread across months.
- Matching rule and keyword list: `mbpe/sources/gdelt_bq.py` (`KEYWORDS`, `match_keywords`). It matches whole words on title or URL, keeps English-source rows only, and is shared with the live collector.

## Evaluation window and news coverage (decided 2026-10-27)
- **All ablation comparisons start 2021-01-01**: every rung (technical → +derivatives → +news) uses the same window. Reason: 22% of GDELT 15-min batches are missing in Sep–Dec 2020.
- Market-only models may also be reported on the full Sep 2020 range, as a **separate** result, never mixed into the ablation table.
- **News features are NaN where coverage is missing.** A batch with no GKG rows at all is an outage, not "no news".
- **Rolling news baselines skip missing batches** and stay NaN until at least **80%** of their window is covered.
- **Empty-title rows** (title tag present but empty; matched via URL) **count toward news volume but are excluded from sentiment scoring.**
- **News availability = batch label (`DATE`) + 15 min, in the backtest and in live mode alike.** The filter is on batch time, never on `fetched_at` (`gdelt_bq.news_as_of`). The live collector warns whenever a batch is published more than 15 min after its label.

## Sentiment scoring (Phase 2, decided 2026-10-27)
- **No LLM scorers.** An LLM trained on later data may "know" how a historical headline played out (historical contamination). This replaces the earlier "FinBERT vs CryptoBERT / small local LLM" comparison.
- **Scorers:** ProsusAI/finbert (**primary**) and ElKulako/cryptobert (secondary, reported only); see "Sentiment scorer decision" below.
- **Label definition:** labels are financial-news tone (positive / negative / neutral), not the expected BTC price impact (`labeling/GUIDE.md`).
- **Benchmark set:** the 200-headline sample minus the ids in `labeling/excluded.csv`. h001 and h002 are excluded because they were discussed with an LLM before labeling. The model comparison runs on the remaining **198**. Labels given to excluded headlines stay in `labels.csv` but are never scored.
- **Sentiment is scored per exact-unique normalized title** (decoded, lowercased, outlet suffix stripped), not per cluster. Scores map back to every row with that title.
- **Near-duplicate clusters are used only for counting and novelty features.** A cluster takes no new members more than 24 h after its first member, so later developments start a new cluster. A cluster becomes available at its earliest member's batch time + 15 min; later copies add volume only.

## Funding data (decided 2026-10-27)
- **Months not yet in the Binance archive come from the public REST API** (`/fapi/v1/fundingRate`). Each clean row is tagged `source` = archive / rest.
- **When the archive month appears, it must match the REST values exactly** (timestamps and rates), or the clean build fails. Checked on Sep 2026: 90/90 identical.
- **Label building fails if a scheduled funding time (8 h UTC grid) inside a trade window has no funding data.** Missing funding is never treated as zero.

## Macro event blackout (pre-registered evaluation rule, 2026-10-27)
- **No trades whose window, from entry − 30 min to exit, contains a US CPI release or an FOMC policy statement.** The rule applies to every model, baseline and ablation rung alike.
- **Event times:** `data/reference/macro_events.csv`, hand-maintained from the official BLS and Federal Reserve schedules. CPI is at 08:30 ET and FOMC statements at 14:00 ET, converted to UTC with daylight saving. It covers Sep 2020 → Dec 2026, including the scheduled upcoming events.
- **Rescheduled events are noted in the file:**
  - Sep 2025 CPI: Oct 15 → Oct 24, 2025;
  - Oct 2025 CPI: cancelled;
  - Nov 2025 CPI: Dec 10 → Dec 18, 2025;
  - Jan 2026 CPI: Feb 11 → Feb 13, 2026.

  The Aug 22, 2025 notation vote is listed but excluded (not a policy-rate statement).
- **Known limitation:** the calendar uses the *actual* release times. For rescheduled events, the backtest knows the new time from the start, while in reality it was announced only days or weeks ahead. The calendar features (hours to next / since last event, event in the next 4 h) are therefore slightly optimistic around those 4 events. This is documented here, not corrected.

## News and calendar features (Phase 2 step 3, 2026-10-27)
- **Feature groups for the ablation:** `technical`, `derivatives`, `news` and `calendar` (CPI/FOMC timing). News and calendar are separate rungs.
- **News features are computed per keyword group** (crypto, macro); a row with both counts in both.
- **The sentiment model is `default_sentiment` in `config/news.yaml`** (FinBERT). Switching models is a config change plus a rebuild, not a code change.
- **Sentiment and macro tone count each near-duplicate cluster once** (the mean of its titles available by t). Empty titles count toward volume only.

## Evaluation protocol (pre-registered 2026-10-28, before any model results)
- **Holdout:** decision times from **2026-04-26 00:00 UTC** onward. Code-level guard: loading or evaluating holdout rows raises unless an explicit `allow_holdout` flag is passed. Every such access is appended to `data/logs/holdout_access.jsonl`.
- **Evaluation window:** 2021-01-01 to the holdout start.
- **Walk-forward:**
  - test folds are consecutive 3-month blocks from 2022-01-01 to the holdout start (the last is the partial block Apr 1–25, 2026);
  - training is an expanding window of everything from 2021-01-01 before the fold;
  - purge: drop training rows whose label window (decision time → exit time) overlaps the test fold;
  - embargo: 24 h after each test fold. With forward-only folds no training row follows a test fold, so it never binds; it's kept in code for generality;
  - validation: the last 20% of each training window, purged the same way, used only for early stopping, calibration and the threshold;
  - sample weights: average uniqueness of each label over its window (1 / number of concurrent labels, per side).
- **Ablation rungs:** (1) technical, (2) technical + derivatives, (3) technical + derivatives + news + calendar.
- **Position rule:** at most one open position across both sides. While a trade is open, new signals are ignored. If long and short both signal in the same hour, neither is taken.
- **Threshold rule:** per fold and side, choose the probability threshold on the validation slice that maximizes mean net R, with ≥ 100 validation trades; otherwise that side doesn't trade in that fold.
- **Blackout** applies to every strategy, baselines included. Operational form: the exit time isn't known at entry, so an entry is blocked if a CPI or FOMC release falls in **[entry − 30 min, entry + 4 h]** (4 h = the label horizon). This blocks a superset of the "entry − 30 min to exit" rule without using the outcome.
- **Training target:** `label` (net R > 0 under taker costs), for both cost scenarios. Each cost scenario selects its own threshold and is evaluated on its own net R.
- **Reporting:** every result for both label sets (A, B) × both cost scenarios × all rungs × all models, per fold and overall. No configuration is dropped from the report.

## Phase 3 step 2 additions (pre-registered 2026-10-28, before any tuned results)
- **Blackout:** the entry-time form ([entry − 30 min, entry + 4 h]) is confirmed as *the* rule. "Entry − 30 min to exit" would look ahead.
- **Volatility-filter baseline,** in every comparison: random direction, trading only in high-ATR hours.
  - Per fold, the ATR cutoff is the quantile of `atr_1h_pct` on the **validation slice** such that the share of hours above it equals the model's combined test signal frequency (long + short). Test hours at or above the cutoff become signals, with direction long with probability q_long / (q_long + q_short).
  - 20 seeds (directions); same blackout and position rule.
- **Basis-point view:** mean net and gross bps per trade for every model and baseline, and the model's per-fold net-bps advantage over the random and volatility-filter baselines. Costs in bps are the same for every trade, so the advantage reflects direction choice.
- **Capped tuning (the only tuning in Phase 3 step 2):**
  - **Scope:** LightGBM, label set A, taker costs, rungs 1–3.
  - **Search:** Optuna TPE, **30 trials per fold and rung**, using the training and validation slices only. One shared parameter set per fold and rung for both sides.
  - **Objective:** validation mean net R under the threshold rule, i.e. the trade-weighted mean over sides that meet ≥ 100 validation trades. A trial where no side qualifies scores −10.
  - **Space:** num_leaves 7–63, min_data_in_leaf 100–1000, learning_rate 0.01–0.1 (log), feature_fraction 0.5–0.9, lambda_l2 0–10. Other settings as in step 1.
  - **Total trials:** 30 × 18 folds × 3 rungs = **1,620**, recorded for the overfitting (deflated Sharpe / PBO) estimate.
  - **Evaluation:** the best trial's parameters per fold and rung are refit and evaluated on the test fold exactly as in step 1 (calibration, threshold, simulator, baselines).

## Sentiment scorer decision (pre-registered 2026-10-28, before any CryptoBERT model results)
- **FinBERT is the primary sentiment scorer for all headline results.**
- **CryptoBERT is a secondary, reported-only comparison.** It cannot replace FinBERT based on its results (backtest, agreement or otherwise).
- **Hand labeling is optional and no longer decides the scorer.** This supersedes the earlier plan to choose the scorer by agreement with the hand labels.
- **Optional sanity check:** an LLM labels the 198 benchmark headlines for tone following `labeling/GUIDE.md`. These labels are **LLM-judged, not human**, kept separate from `labels.csv`, and used only to report each scorer's agreement as a sanity check. They decide nothing.

## Phase 3 holdout test (pre-registered 2026-10-28, before any holdout access)
The holdout is run once to conclude the study (option 1 in reports/FINDINGS.md). Until this run, `data/logs/holdout_access.jsonl` does not exist.
- **Period:** decision times from **2026-04-26 00:00 UTC** to the latest decision time with a label in both label sets (**2026-10-25 19:00 UTC** at registration; labels are not rebuilt). Reported as two test folds of about 3 months, like the walk-forward folds:
  - holdout fold 1: 2026-04-26 → 2026-07-26 (exclusive);
  - holdout fold 2: 2026-07-26 → the last labelled hour (inclusive).
- **Training:** everything from 2021-01-01 before the holdout start. The same purge (training label windows may not reach the holdout), embargo, last-20% validation slice, uniqueness weights, isotonic calibration and threshold rule as the walk-forward folds.
  - **One fit per configuration, used for both holdout folds.** Holdout fold 2 does *not* train on holdout fold 1: "everything before the holdout" is read literally, so no holdout outcome ever enters training, tuning, calibration or threshold choice.
  - **Tuned models:** the same 30-trial Optuna study (same space, objective and seed rule, fold index 18) on this final validation slice, once per tuned configuration: FinBERT rungs 1–3 and CryptoBERT rung 3 (+120 trials, so N for any later deflated Sharpe would be 2,308).
  - No other changes to code, features, labels or settings. The only code change is plumbing: the holdout fold, the `allow_holdout` flag, and reporting per holdout fold.
- **Configurations:** all 32 evaluated configurations (FinBERT: label sets A/B × both cost scenarios × logreg/LightGBM × rungs 1–3, plus tuned LightGBM A/taker rungs 1–3; CryptoBERT: LightGBM rung 3 A/B × both scenarios, plus tuned A/taker), and all baselines: always-long, always-short, random entry and the volatility filter at each model's frequency (20 seeds each), and the shuffled-label control (LightGBM rung 3, label set A). Everything is reported next to its walk-forward result; nothing is dropped.
- **Headline result:** label set A, taker costs, tuned LightGBM, rung 3 (FinBERT): mean net R over the whole holdout with its 95% block-bootstrap CI.
- **Interpretation (decided now):**
  - headline mean net R **≤ 0**: the negative finding is confirmed;
  - **> 0 with a 95% CI excluding zero:** reported as unexpected but **not acted on**. One 6-month window after 2,188 trials isn't enough evidence; at most it justifies a forward test on new live data;
  - **> 0 with a CI including zero:** inconclusive, which is consistent with no edge.
- **Run once,** with `allow_holdout=True`, so every access is logged in `data/logs/holdout_access.jsonl`. If a bug is found afterwards, it is fixed and the holdout re-run, and **both** results are reported with an explanation.

## Phase 4 pre-registration (decided 2026-10-28, before any Phase 4 feature or model)

**Question.** Does any of three checklist extensions, added to Phase 3 rung 3 (FinBERT), produce an edge after costs that Phase 3 didn't find? Each extension is tested separately against rung 3, not stacked.

**Data decisions.**
- **Order book = `bookDepth` only** (daily archive files, BTCUSDT USD-M, from 2023-01-01). `bookTicker` is excluded: it is not published after 2024-03-30.
- **A `bookDepth` file under 2 KB counts as a missing day,** like an absent file. Its hours have no order-book features (NaN).
- `bookDepth` is ingested like the Phase 1 Binance datasets: SHA-256 checked, clean layer, and a full gap scan of the snapshot timestamps.

**Rungs** (all feature values are computed from data with timestamps before the decision time t):
- **Base = rung 3:** technical + derivatives + news + calendar (CPI/FOMC), exactly as in Phase 3.
- **4a = rung 3 + MACD/EMA** (group `macd_ema`, 6 features), on 1h futures closes (bars present, in time order, as for RSI), using the bar ending at t:
  - MACD(12, 26, 9): line = EMA12 − EMA26, signal = EMA9 of the line, histogram = line − signal. **Each is divided by ATR(14, 1h).**
  - Price distance from EMA 20, 50 and 200: (close − EMA_n) / ATR(14, 1h).
  - EMAs use α = 2 / (n + 1), recursive from the first available bar (Sep 2020), so they are fully warmed up by the 2021 evaluation start. **No raw EMA levels.**
- **4b = rung 3 + NFP** (group `nfp`, 3 features, plus blackout):
  - features, mirroring CPI/FOMC: hours to the next NFP release after t, hours since the last one at or before t, NFP in the next 4 h;
  - blackout: NFP releases join CPI/FOMC in the entry-time rule [entry − 30 min, entry + 4 h], for 4b's models and all of their baselines and controls.
- **4c = rung 3 + order book** (group `orderbook`, 8 features), from `bookDepth` snapshots with timestamps **strictly before t**:
  - **Staleness:** the last snapshot before t must be at most **2 minutes** old, otherwise every order-book feature at t is NaN.
  - **Depth imbalance per band,** b ∈ {0.2, 1, 2, 3, 4, 5}%: (bid depth at −b − ask depth at +b) / (bid + ask), using cumulative depth in BTC. 6 features.
  - **Total depth** D = bid depth at −5% + ask depth at +5% (everything the file covers).
  - **Depth change over 1 h:** log(D_t / D_{t−1h}). D_{t−1h} comes from the last snapshot before t − 1h, under the same 2-minute limit.
  - **Depth vs its 7-day median:** log(D_t / median of D at the previous 168 hourly decision times). The median needs ≥ 84 non-missing values, otherwise NaN.

**Evaluation.**
- **Walk-forward only, decision times before 2026-04-26.** The holdout stays closed. The spent Phase 3 holdout is not re-used.
- **Same protocol as Phase 3:**
  - purge and embargo;
  - last-20% validation slice, uniqueness weights, isotonic calibration;
  - threshold rule (≥ 100 validation trades);
  - position rule and blackout;
  - baselines: always-trade, random and volatility filter (20 seeds each), plus the shuffled-label control (LightGBM, label set A, run with the same features and blackout as the model it controls).
- **Windows:**
  - **4a, 4b and rung 3:** the Phase 3 window and folds (training from 2021-01-01; 18 test folds from 2022-01-01).
  - **4c and rung 3 re-evaluated:** training from **2023-01-01**, 10 quarterly test folds from **2024-01-01** to 2026-04-26 (the last is Apr 1–25, 2026). Both use exactly this window and these folds.
- **Models and settings:** logistic regression and LightGBM with the fixed Phase 3 settings. **No tuning in Phase 4.**
- **Reported:** both label sets × both cost scenarios × both models, per fold and overall, for rung 3, 4a, 4b, **rung 3 with the NFP blackout only** (to separate 4b's blackout from its features) and 4c (with rung 3 on the 4c window). Nothing is dropped.

**Decision configuration:** label set A, taker costs, fixed-settings LightGBM.

**Kill criterion (per extension, against rung 3 on the same window).**
- **Pass = both of:**
  1. **Bps advantage improves by ≥ 2 bps.** Advantage = gross bps per trade of the model minus that of its shuffled-label control, each **pooled across all test trades** of the window. Required: advantage(extension) − advantage(rung 3) ≥ 2.0 bps.
  2. **Mean net R after taker costs > 0** (point estimate, pooled over all test trades). The 95% block-bootstrap CI is reported alongside.
- **Also reported:** the number of folds in which the extension's per-fold advantage exceeds rung 3's.
- **4b is decided against rung 3 with the original blackout.** Rung 3 with the NFP blackout alone is reported for interpretation only.
- **Passing only qualifies an extension for a forward test** on data after 2026-10-25, pre-registered separately before that data is examined. It is not an edge. If no extension passes, the Phase 3 finding stands.

**Social (Reddit).** Forward data only. It is collected once credentials are confirmed and **excluded from every Phase 4 evaluation**.

**Live order book.** `mbpe/sources/binance_depth_live.py` collects the same 12 bands forward from the REST order book, with a parity check against the archive on overlapping days. It is forward-only and not used in the Phase 4 evaluation.

**Multiple testing.** Phase 4 re-uses walk-forward data Phase 3 already looked at. Its evaluated configurations are added to the trial count for any deflated Sharpe estimate.

**Limitations (known now).**
- **NFP is calendar-only:** jobs and payroll news isn't in the GDELT keyword set, so there are no NFP news features.
- **The 4c window is shorter** (2.3 years of test folds against 4.3 for 4a/4b), so its estimates are noisier.
- **Archive timing:** `bookDepth` is published a day late. Live use needs our own snapshots, whose band construction may differ slightly from the archive's (see the parity check).

## Amendments
- **2026-10-28 (after the Phase 4 evaluation): live depth collector replaced by the archive.**
  - **Change:** any forward test of order-book features uses the archive `bookDepth` daily files (published about a day late), instead of the live depth collector named in the Phase 4 pre-registration.
  - **Reason:** the public REST order book returns at most 1,000 levels per side, which reach only about ±0.15% from mid. That's short of every `bookDepth` band (±0.2% to ±5%), so a REST poller can't rebuild them (FEASIBILITY.md, "Phase 4: live order book").
  - **Effect:** none on any result. It was decided after the evaluation, and no extension passed, so no forward test was triggered.
- **2026-10-28 (Phase 5, after step 1 labels and always-trade baselines, before any Phase 5 model): drift interpretation rule.**
  - **The pass criterion is unchanged.**
  - **Added reporting:** the headline (rung 3 LightGBM, taker, pooled over all test trades) is reported next to:
    - **always-long** (step 1's definition: one position at a time, no blackout);
    - a **random long-only baseline** at the headline's trade frequency (20 seeds): in each fold, random long entries with probability equal to the headline's combined test signal share (long + short), under the same position rule.
  - **Interpretation rule:** if the headline passes the criterion but its mean net R (taker, pooled) does **not exceed** always-long's, the result is reported as **"drift exposure, not predictive edge"**. The forward test is still triggered as pre-registered, but every report of the result must state this.
  - **Also reported:** the share of the model's trades that are long, for the headline and every model configuration.
  - **Reason:** step 1 showed that always-long is close to break-even after taker costs at this horizon, so a model that mostly goes long could pass without directional skill.
- **2026-10-29 (Phase 6, after the Workstream C results, before the forward model is frozen): forward-test threshold changed to the validation-chosen threshold.**
  - **Change:** the frozen system trades when the meta-model's P(success) ≥ the **validation-chosen threshold**, instead of the pre-registered fixed 0.60. The threshold is computed by the Phase 3 threshold rule on the frozen meta-model's own validation slice and stored in the artifact's `manifest.json`.
  - **Reason:** in Workstream C, the calibrated meta-model put a candidate at ≥ 0.60 only very rarely. At 0.60 the system would trade about once a month, so the pre-registered verdict condition (≥ 100 trades) would take years.
  - **Decided after seeing results, and stated plainly:** in Workstream C, the validation-threshold variant was the best LightGBM configuration but **still lost money after taker costs**. So the forward test has a negative historical expectation. It is a test of the scoped system as built, not of a candidate edge. The 0.60 variant is not forward-tested.
  - **Unchanged:** the model (LightGBM primary + meta-filter), the data, the live rules (Workstream D definitions), and the forward pass criterion (verdict only after ≥ 6 months **and** ≥ 100 trades; mean net R > 0 with the 95% CI lower bound > 0, and above always-long over the same period).
  - **Where it runs:** this Mac, via launchd. Hours missed while the Mac sleeps or is offline, and late runs, are recorded as no decision (Workstream D rules), which can delay reaching 100 trades.
- **2026-10-29 (Phase 6 Workstream A): CryptoPanic dropped.**
  - **Change:** the CryptoPanic API requires a paid plan, so under the free-only data decision it is removed from Phase 6. No collector is built.
  - **Covered instead by:** the RSS collector (CoinDesk, Cointelegraph, The Block, Decrypt, Bitcoin Magazine), which provides crypto news headlines for free and is running.
  - **Effect:** none on any result. CryptoPanic was forward-only data, in no model.
- **2026-10-29 (during the forward test): repository reorganized; no behaviour change.**
  - **Change:** files were moved into self-explanatory folders, and every comment and docstring was removed from the Python code (the owner's request). Usage notes live in `README.md` and `docs/RUNBOOK.md`. Earlier sections of this file and of `PROGRESS.md` keep the paths of their time; this is the map.
  - **Verified before the swap:**
    - all tests pass;
    - the generated tables match their sources;
    - market and news features rebuilt by the moved code equal the existing files;
    - the Phase 6 walk-forward reproduces its saved candidates exactly;
    - the frozen model (version and file hash unchanged) re-predicts the logged forward decisions exactly.
  - **Effect:** none on any result or on the forward test. The launchd jobs were updated to the new module names.

  | old | new |
  |---|---|
  | `SCOPE.md`, `PROGRESS.md`, `FEASIBILITY.md`, `REFERENCE_NOTES.md`, `reports/FINDINGS.md` | `docs/` (same names) |
  | `feasibility.py`, `feasibility_binance_files.csv` | `scripts/feasibility.py`, `reports/feasibility/binance_files.csv` |
  | `mbpe/sources/binance.py`, `gdelt_bq.py`, `gdelt_live.py`, `gdelt_parity.py` | `mbpe/data/binance_archive.py`, `gdelt_backfill.py`, `gdelt_live.py`, `gdelt_parity.py` |
  | `mbpe/sources/rss_live.py`, `liquidations_live.py`, `reddit_live.py` | `mbpe/data/rss.py`, `liquidations.py`, `reddit.py` |
  | `mbpe/storage/clean.py`, `mbpe/ingest.py`, `mbpe/live/market.py` | `mbpe/data/clean.py`, `ingest.py`, `binance_rest.py` |
  | `mbpe/features/dedup.py`, `phase4.py`, `phase6_report.py`, `mbpe/live/features.py` | `mbpe/features/news_dedup.py`, `nfp_and_orderbook.py`, `mbpe/research/phase6_data_report.py`, `mbpe/features/live.py` |
  | `mbpe/validation/harness.py`, `step2.py`, `cryptobert_compare.py`, `holdout.py` | `mbpe/research/experiment.py`, `phase3_tuned.py`, `phase3_cryptobert.py`, `phase3_holdout.py` |
  | `mbpe/validation/phase6.py`, `phase6_eval.py` | `mbpe/research/phase6_models.py`, `phase6_evaluation.py` |
  | `mbpe/validation/{walk_forward, backtest, tuning, overfitting, phase4, phase5}.py` | `mbpe/research/` (same names) |
  | `mbpe/live/run.py`, `model.py`, `paper.py`, `news.py`, `every.py` | `mbpe/live/hourly_run.py`, `frozen_model.py`, `paper_trading.py`, `news_update.py`, `scheduler.py` |
  | `mbpe/reports/progress.py`, `mbpe/labeling/label.py` | `mbpe/reporting/generated_tables.py`, `mbpe/labeling/label_tool.py` |
- **2026-10-29 (during the forward test): scheduling files moved to `ops/`; read-only dashboard added; no behaviour change.**
  - **Change:**
    - `deploy/*.cron` → `ops/cron/`; `deploy/*.service`, `*.timer` → `ops/systemd/`; `Dockerfile`, `docker-compose.yml` → `ops/docker/Dockerfile`, `compose.yml`;
    - the launchd jobs running the forward test are kept as templates in `ops/launchd/`, installed by `ops/jobs.sh`. The rendered templates equal the installed jobs byte for byte;
    - `dashboard/`: a local web page (localhost only) reading `data/live/`. It writes nothing and changes no decision.
  - **Effect:** none on any result or on the forward test.
- **2026-10-29: Test B added to the hourly run as a separate `paper_b` step writing to `data/live/paper_b/`; Test A's code, files and behaviour are unchanged.**
- **2026-10-29 (owner's decision): Tests A and B move to the BTCUSDC contract and restart.**
  - **Reason:** Binance's published regular-user fees are lower on the USDC-margined BTCUSDC perpetual (0.04% taker, 0% maker under the current promotion, against 0.05% / 0.02% on BTCUSDT). The owner chose to run both forward tests on the cheaper contract.
  - **Decisions unchanged:** the same frozen model (f4c4365501a9272b), the same threshold (0.30909969344992666), the same BTCUSDT inputs the model was trained on, the same hourly schedule and decision rules.
  - **Execution moves to BTCUSDC**, for both tests:
    - entry prices, fills, stops, targets and the 4-hour exit use BTCUSDC 1-minute candles;
    - funding uses BTCUSDC's own funding rates;
    - the stop and target distances still use BTCUSDT's 1h ATR (the model's own input), applied to the BTCUSDC price.
  - **Fees:** Binance's published regular-user BTCUSDC rates in force when a trade happens, plus 2 bps slippage on every market fill (taker entry, stop and time exit). From this amendment: 0.04% taker and 0% maker. If the published rate changes, the new rate applies from that date as a dated amendment, and every trade records the rates it paid. All other Test A and Test B rules are unchanged.
  - **Restart:** both tests restart at the first hour after this change goes live (start record below). Earlier BTCUSDT records (Test A from 13:00 UTC, Test B from 18:00 UTC on 2026-10-29) are moved to `data/live/archive/btcusdt/`, reported separately and never mixed into the restarted tests.
  - **Verdict:** unchanged rules, counted from the new start: ≥ 6 months **and** ≥ 100 closed trades per test. The always-long benchmark is computed on BTCUSDC with the same fees.
  - **Unchanged as registered:** Test B's historical replay uses BTCUSDT history and costs (BTCUSDC has too little history for folds 4–17); it is labelled that way.
  - **Parity:** the daily check of paper trades against the label code uses the BTCUSDC data.
  - **Stale BTCUSDC data:** if the BTCUSDC store's last 1m candle ends more than 5 minutes before the decision time, neither test opens a new position that hour (an alert is logged); open positions keep settling once data arrives. This matches the existing stale-input rule.

## Phase 5 pre-registration: longer horizon (decided 2026-10-28, before any Phase 5 label or model)

**Status of any historical result.** The 2021–2026 data was studied in Phases 3–4, so Phase 5's historical results are **development evidence only**. They can at most trigger a forward test; they can never establish an edge. The Phase 3 holdout (2026-04-26 → 2026-10-25) is spent and isn't used: decision times and exits stay before 2026-04-26.

**Question.** Does the Phase 3 approach work at a slower cadence, where fixed costs are a smaller share of each trade's typical move?
- **Decisions:** every 4 hours.
- **Horizon:** 24 h.
- **Barriers:** set by daily ATR.

**Decisions and labels.** One label set only: `config/labels_P5.yaml` → `data/labels/triple_barrier_P5.parquet`, built by the existing triple-barrier code. The only code changes are configurable: daily ATR bars and the decision cadence. Defaults stay hourly / `h`, so the Phase 1–4 labels are unchanged.
- **Decision times:** 00, 04, 08, 12, 16 and 20 UTC.
- **Barriers:** take-profit +2 ATR and stop −1 ATR (1 R = 1 ATR).
  - ATR = Wilder ATR(14) on UTC daily bars built from 1m futures candles, using **only bars closed at or before t**. At 00:00 that includes the bar that just closed; at 04:00–20:00 it is the same bar.
- **Horizon** 24 h. Other label rules are as in Phase 1:
  - entry at the open of the 1m candle at t + 1 min;
  - stop assumed first when both barriers fall in one candle;
  - gaps through the stop fill at the open.
- **Mostly expiries are expected.** Over 24 h the price rarely moves 2 daily ATRs, so most trades will end at the horizon, and their outcome is essentially the 24 h return net of costs.
- **Exit-reason shares** (take-profit / stop / expiry) and the always-long / always-short baselines are reported **before any model is trained** (step 1, then review).
- **Training target:** net R > 0 under taker costs, as in Phase 3.

**Costs.**
- **Taker (headline):** 0.05% fee + 2 bps slippage per side.
- **`maker_entry_tp`:** reported alongside.
- **Funding:** every settlement in (entry, exit], up to 3 per trade on the 8 h grid. Labels fail if funding data is missing (Phase 1 rule).

**Features.**
- **The existing Phase 3 feature set, unchanged,** sampled at the 4-hourly decision times: market, derivatives, FinBERT news and CPI/FOMC calendar features. **No new features.**
- **Ablation rungs 1–3,** as in Phase 3, all reported.

**Models.** Logistic regression and LightGBM with the fixed Phase 3 settings, isotonic calibration on the validation slice. **No tuning.**

**Validation.**
- **Folds:** the same walk-forward folds and dates as Phase 3 (training from 2021-01-01; 18 quarterly test folds from 2022-01-01 to 2026-04-26). Last-20% validation slice.
- **Purge:** drop training rows whose 24 h label window (decision → exit) overlaps the test fold, and the validation slice likewise.
- **Embargo:** **48 h** after each test fold. As in Phase 3, it doesn't bind with forward-only folds.
- **Sample weights:** label-overlap (average uniqueness) weights. With 24 h windows every 4 h, each label overlaps about 6 others.
- **Position rule:** one position at a time across both sides; signals during an open trade are ignored; long and short together means neither.
- **Entry rule (replaces the Phase 3 threshold search, for Phase 5 only): no search.**
  - Per fold and side, a test decision is a signal when its calibrated score is **≥ the 75th percentile** of that side's calibrated scores on the validation slice (numpy `quantile`, linear), i.e. the top 25%.
  - Isotonic calibration produces tied scores, so the realized share can exceed 25%. The realized validation and test shares are reported per fold and side.
  - The signals are the same in both cost scenarios; each scenario is evaluated on its own net R.

**Blackout: none.**
- **Reason:** a 24 h window contains a CPI, FOMC or NFP release on many days, so a blackout would remove a large, non-random share of decisions.
- **Calendar features stay in,** so the models can still condition on releases.

**Controls** (all at the model's own trade frequency, same position rule):
- always-long and always-short;
- random entry (20 seeds);
- **volatility filter** (20 seeds): random direction in high-volatility decision times. The volatility measure is the **daily ATR** (the label's ATR, divided by the futures close at t), matching the barriers. The cutoff comes from the validation slice, as in Phase 3;
- **shuffled-label control, 20 permutations:** 20 separate runs per rung, with training and validation labels permuted (seeds 0–19). The same permutations are used across rungs.

**Historical pass criterion** (rung 3 LightGBM, taker costs, pooled over all test trades). Pass = all of:
1. **Mean net R > 0,** and
2. **the 95% block-bootstrap CI lower bound > 0,** and
3. **the permutation test:** rung 3 LightGBM's mean net R exceeds that of **all 20** shuffled-label runs (p < 0.05).

Passing **only triggers a forward test.** Not passing ends Phase 5 with the Phase 3 finding unchanged. All configurations, rungs, models and controls are reported either way.

**Forward test (only if the historical criterion passes).**
- **Data:** decision times after 2026-10-25, from archive data only: the Binance archive (`data.binance.vision`) and the GDELT bulk files (15-minute GKG files), fetched after the fact. The live collectors aren't required.
- **Protocol:** models trained on data before the forward period with the Phase 5 settings above, frozen, then applied unchanged.
- **Verdict only after both** ≥ 6 months of forward decision times **and** ≥ 100 forward trades.
- **Power:** with per-trade R dispersion of roughly 1, 100 trades give a 95% CI of about ±0.2 R on the mean. **The forward test can only detect a large edge or a clear failure; it cannot confirm a small one.**
- **Pass criterion:** written and committed **before the forward test starts**, and before any forward-period outcome is looked at.

**Multiple testing.** Phase 5 adds configurations on data already used in Phases 3–4. They are added to the trial count for any deflated Sharpe estimate.

## Phase 6: delivery of the full scope doc (decided 2026-10-28, before any Phase 6 code, data or result)

**Basis (your decision, 2026-10-28).** "Done" now means every component of Sarosh's scope doc ("MBPE Project Scope, Technical Plan, Open Questions and Timeline"). Where the locked scope at the top of this file deferred or dropped a component, the scope doc now wins. Other decisions:
- **Data:** free sources only.
- **Paper trading:** an internal simulator on live data.
- **What gets paper-traded:** the final scoped system, regardless of its backtest result, as a pre-registered forward test.

**Honesty constraints (unchanged).**
- **History is development evidence only.** 2021–2026 has been studied in Phases 3–5 and the holdout is spent, so historical Phase 6 results can't establish an edge. Only the forward test can.
- **Same safeguards as before:** pre-registration before each experiment, leakage tests, generated tables, every result reported.

### Workstream A: data streams (scope doc §2)
| Component | Source (free) | History for backtests? | Status |
|---|---|---|---|
| Market: OHLCV, RSI, ATR, VWAP divergence, BB %B, realized vol | Binance archive + REST | yes, from 2020 | built |
| Derivatives: funding, open interest | Binance archive + REST | yes, from 2020 | built |
| Derivatives: mark price / premium index (basis to mark, premium z-score) | Binance archive (`markPriceKlines`, `premiumIndexKlines`) + REST `premiumIndex` | yes, from 2019 | new |
| Derivatives: liquidation volume and long/short liquidation imbalance | Binance WebSocket `btcusdt@forceOrder` (REST endpoint retired; at most one liquidation per second is pushed, so it undercounts) | **no: forward only** | new collector |
| News: sentiment, CPI/FOMC/NFP calendar | GDELT GKG + FinBERT; calendar CSV | yes, from 2020 | built |
| News: regulatory and major announcements | keyword flags on GDELT titles (SEC, ETF, ban, lawsuit, approval, …; list pre-registered with the features) | yes, from 2020 | new |
| News: CryptoPanic, RSS (CoinDesk, Cointelegraph, The Block, Decrypt, Bitcoin Magazine) | CryptoPanic free API (**your key**, env var); RSS feeds | **no: forward only** | new collectors |
| Social: mention volume and velocity, sentiment polarity and distribution, activity change | Reddit r/Bitcoin + r/CryptoCurrency via PRAW (**your credentials**, env vars); FinBERT on titles | **no: forward only** | collector built, not started; features new |
| Social: viral / engagement indicators | Reddit score and comment count **as observed at each fetch**, stored with the fetch time; a feature at t uses only values observed before t | **no: forward only** | new |
| Social: X | paid API | excluded by the free-only decision | — |

All new streams follow the existing rules: available-at timestamps, staleness limits, NaN for gaps (never zero), and a truncation leakage test per feature.

### Workstream B: models (scope doc §3)
- **B1. Primary model: three classes per side** (+1 take-profit first, −1 stop first, 0 expiry), on the Phase 3 labels (+2/−1 × ATR(14, 1h), 4 h, entry at t + 1 min). LightGBM **and** CatBoost, fixed settings, isotonic calibration per class.
  - **Decision rule** (scope doc open questions 6–7): per side, expected net R = Σ P(class) × the mean net R of that class in the training slice (taker). The candidate trade is the side with the higher expected net R, if that is > 0; otherwise no trade.
- **B2. Meta-labeling filter.**
  - **Inputs:** the primary model's class probabilities, the proposed side, and volatility features (ATR %, realized vol 1h/4h/24h). No other inputs, per the scope doc.
  - **Target:** the candidate trade's net R > 0 after taker costs (time-expiry trades count by their actual net R).
  - **Training data:** only **out-of-sample** primary predictions. For test fold k, the meta-model trains on the primary model's walk-forward test-fold predictions from folds < k (purged, with the 48 h embargo against fold k). Meta-labeling starts at the first fold with ≥ 4 earlier folds of out-of-sample predictions, and the primary-only comparison uses exactly the same folds.
  - **Threshold:** execute when P(success) ≥ **0.60** (fixed, headline). A threshold chosen on validation data is reported alongside.
- **B3. Interpretability.** SHAP TreeExplainer on the primary model and the meta-model in every fold:
  - mean |SHAP| per feature and per feature group (market, derivatives, news, calendar, regulatory);
  - stability across folds (Spearman rank correlation of feature importances between folds).
- **Configurations:** feature sets = Phase 3 rung 3 + the new historical features (mark/premium, regulatory). Label set A. Both cost scenarios. LightGBM and CatBoost. Primary-only vs primary + meta. **No tuning.**
- **Decision interval:** hourly, as in Phase 3 (the scope doc's 15-minute interval was "subject to confirmation").
- **Headline model:** **LightGBM** primary + meta-filter, label set A, taker costs. CatBoost is reported but doesn't decide which model is frozen.

### Workstream C: validation and analysis (scope doc §4, weeks 5–6)
- **Validation:** purged walk-forward, Phase 3 folds (training from 2021-01-01; 18 test folds 2022-01-01 → 2026-04-26), **48 h embargo** (scope doc §4.3), uniqueness weights, one position at a time, CPI/FOMC/NFP blackout ([entry − 30 min, entry + 4 h]).
- **Comparisons:** primary vs primary + meta (the scope doc's required comparison), plus the existing controls: always-long/short, random, volatility filter, and a 20-permutation shuffled-label control.
- **Targets table:** hit rate, Sharpe, profit factor and max drawdown for every configuration, against 54–58% / ≥ 1.8 / ≥ 1.6 / < 12%. Reported as met / not met; **they are research objectives, not pass criteria** (scope doc §4).
- **Regime analysis (reporting only):** results by volatility tercile (trailing 30-day realized vol, computed before each decision) and by trend (sign of the trailing 30-day return).
- **Failure cases:** the worst folds and worst trades, broken down by exit reason, regime and side.

### Workstream D: integration and paper-trading readiness (scope doc weeks 6–7)
- **Live inference:** every hour,
  - pull the latest data from free sources (Binance REST, GDELT live, the new collectors);
  - compute features with **the same feature code** as the backtest;
  - run the frozen primary model and meta-model;
  - append each prediction to a prediction log (inputs hash, probabilities, decision, model version, timestamps).
- **Live/archive parity:** features computed from live data must match features recomputed from the archives once those publish (tolerance pre-registered), as was done for GDELT.
- **Paper executor (internal simulator):**
  - entry at the next 1-minute open plus the backtest's taker costs;
  - barriers watched on live 1-minute candles, with the same tie and gap rules as the labels;
  - funding accrued from live settlements, one position at a time, blackout applied;
  - an append-only trade ledger.
- **Operations:** structured logs, retries, alerts when a data source goes stale or a run fails, and a daily summary file. The scheduler is cron/systemd (examples in `deploy/`), with Docker Compose for the collectors and the inference loop. CI runs ruff, the tests and the generated-table check on every push. A runbook covers setup and operational limits.
- **No real orders, ever.** No exchange keys are used.

### Workstream E: forward test (the only real test)
- **Models:** the final scoped system (B1 + B2 with **LightGBM**), trained on all data before 2026-04-26 with the settings above, then **frozen** and paper-traded.
- **Duration:** a verdict only after **both** ≥ 6 months **and** ≥ 100 paper trades.
- **Pass criterion:**
  - mean net R > 0 with the 95% CI lower bound > 0;
  - mean net R above always-long's over the same forward period.

  With per-trade dispersion of roughly 1 R, 100 trades give a CI of about ±0.2 R, so this can only detect a large edge or a clear failure.
- **Forward-only streams** (liquidations, social, CryptoPanic/RSS) aren't in the frozen model: there's no history to train on. They are collected from day 1. After 6 months, a second, separately pre-registered model including them can be trained on that forward data and forward-tested in turn.

### Order of work (each step reviewed before the next; scope doc timeline)
1. Start the forward-only collectors early, since forward data accrues in real time. This needs your credentials and confirmation to run them.
2. Workstream A historical features, then B, then C (one evaluation run, reported).
3. Workstream D, then freeze and start the forward test (E). Code and runbook delivered at the end of D.

### Decisions (2026-10-28; all proposals accepted)
1. **Frozen forward model:** LightGBM (primary + meta-filter). CatBoost is reported historically only.
2. **Decision interval:** hourly.
3. **Social engagement:** Reddit score and comment count are recorded as observed at each fetch, with the fetch time, and a feature at t uses only values observed before t. This **supersedes the Phase 4 step 0 rule** that excluded engagement data.
4. **Forward pass criterion:** as stated in Workstream E (mean net R > 0 with the 95% CI lower bound > 0, and above always-long; verdict only after ≥ 6 months and ≥ 100 trades).
5. **Meta-labeling start:** the first fold with ≥ 4 earlier folds of out-of-sample primary predictions (folds 4–17). The primary-only comparison uses the same folds.

**Dependencies (yours, before the forward-only collectors start):**
- a Reddit "script" app (client ID and secret) and a CryptoPanic API key, set as environment variables outside the repo;
- confirmation of where the collectors run (this machine or a VM).

The RSS and liquidation collectors need no credentials.

### Phase 6 Workstream A: feature definitions (fixed 2026-10-28, before any Phase 6 model)
All features below use only data with timestamps before the decision time t, keep the existing staleness and NaN rules, and are covered by the truncation leakage tests.

**`mark_premium` group** (market registry; Binance archive `markPriceKlines` / `premiumIndexKlines`, 1m, from 2020-09):
- `basis_mark_bps`: (futures last close − mark-price last close) / mark × 10⁴. Both are the last 1m bars closed by t, within the 5-min price staleness limit.
- `premium_last_bps`: premium index of the last 1m bar closed by t × 10⁴ (5-min staleness).
- `premium_mean_1h_bps`, `premium_mean_8h_bps`: mean premium index (× 10⁴) of the 1m bars opened in [t − W, t). NaN unless ≥ 80% of the window's minutes are present (`premium_min_coverage` in `config/features.yaml`).
- `premium_z_7d`: `premium_mean_1h_bps` z-scored against its previous 168 hourly values (≥ 135 present, the `vol_z_7d` rule).

**`regulatory` group** (news registry; GDELT titles of crypto-group rows, the same availability and coverage gating as the news features, each near-duplicate cluster counted once at its first copy):
- `reg_new_clusters_{1h,4h,24h}`: new clusters whose first title matches the regulatory pattern.
- `major_new_clusters_{4h,24h}`: new clusters whose first title matches the major-announcement pattern.
- `reg_share_24h`: regulatory new clusters ÷ all crypto new clusters, 24 h.
- **Regulatory pattern** (word boundaries; the acronyms are case-sensitive, everything else case-insensitive): SEC, CFTC, ETF(s); regulator(s), regulatory, regulation(s), regulate(d/s), lawsuit(s), sue(s/d), indict(ed/ment), fined, ban(s/ned), crackdown, enforcement, legislation, lawmakers, congress, senate, approv(al/e/es/ed), sanction(s/ed), licence/license(s/d), subpoena(s).
- **Major-announcement pattern** (case-insensitive): hack(s/ed), exploit(s/ed), breach(ed), stolen, halving, bankrupt(cy), insolvency/insolvent, delist(s/ed/ing), collapse(s/d), outage(s), depeg(s/ged).
- **Limitation:** keyword matching on titles is crude: false positives and misses are expected, and it's tested on fixed examples only. It only sees headlines already matched by the GDELT crypto keyword list.

**Forward-only collectors** (no features yet; their features are pre-registered with the second model, after 6 months of data):
- `mbpe/sources/rss_live.py`: CoinDesk, Cointelegraph, The Block, Decrypt and Bitcoin Magazine headlines, every 15 min. Availability = first fetch.
- `mbpe/sources/liquidations_live.py`: the BTCUSDT `forceOrder` WebSocket on the `/market/` stream path. Coverage counts only while data is flowing; the legacy `/ws/` path connects but delivers nothing (checked 2026-10-28).

### Phase 6 Workstream B: implementation definitions (fixed 2026-10-28, before the Workstream C evaluation run)
Implemented in `mbpe/validation/phase6.py`. Details the Phase 6 pre-registration left open, fixed before any Phase 6 evaluation result exists (only a 1-fold smoke run for runtime, with no metrics looked at):
- **Feature set:** rung 60 = rung 3 + `mark_premium` + `regulatory` (103 features).
- **Primary classes:** 0 = stop first, 1 = expiry, 2 = take-profit first (the label's `exit_reason`), one model per side.
- **LightGBM primary:** the fixed Phase 3 settings with `objective = multiclass`, early stopping on the validation slice (multi-logloss).
- **CatBoost primary:** MultiClass, 3,000 iterations max, learning rate 0.03, depth 4, L2 3, seed 7, 4 threads, early stopping after 100 rounds on the validation slice. The open-interest/price quadrant code is used as a numeric feature (CatBoost categoricals can't hold NaN floats).
- **Calibration:** isotonic per class (one-vs-rest) on the validation slice, with each row renormalized to sum 1.
- **Decision:**
  - class means = mean taker net R per class over the side's training slice;
  - expected net R = probabilities · class means;
  - candidate = the side with the higher expected net R, if > 0 and not blacked out; ties trade neither side;
  - candidates are formed before the position rule, which is applied after any filter.
- **Meta-model** (for both primaries): LightGBM binary with the fixed Phase 3 settings.
  - **Training data:** the out-of-sample candidates of earlier test folds whose trade exited before the current fold starts.
  - **Validation:** the last 20% of those candidates by decision time, with training candidates whose trade reaches it purged. Used for early stopping and isotonic calibration.
  - **Weights:** uniqueness weights over the candidates' trade windows.
  - **Inputs:** the proposed side's three class probabilities, the side (±1), `atr_1h_pct`, `rv_1h`, `rv_4h` and `rv_24h`.
- **Validation-chosen threshold** (reported alongside the fixed 0.60): the Phase 3 threshold rule on the meta-validation slice (maximum mean taker net R with ≥ 100 trades). If no candidate threshold qualifies, that fold has no trades.
- **SHAP:** exact TreeSHAP computed by the libraries themselves (LightGBM `pred_contrib`, CatBoost `ShapValues`), as mean |SHAP| per feature over each test fold. Multiclass values are averaged over classes.
- **Shuffled-label control:** per side, training and validation rows' (class, net R) pairs are permuted together, so the class means follow the shuffled labels. Test-fold outcomes are never shuffled, and the meta-model of a shuffled run trains on that run's own out-of-sample candidates. 20 permutations (seeds 0–19), for the headline (LightGBM).

### Phase 6 Workstream D: live system definitions (fixed 2026-10-29, before the forward test starts)
Implemented in `mbpe/live/`. These rules govern every forward-test decision and trade.

**Data and timing**
- **Decision time t:** each UTC hour boundary. The run starts at t + 3 min (`mbpe.live.run`).
- **Features** at t use only data with timestamps before t, computed by the same registries as the backtest (`mbpe.live.features`) on:
  - the archive clean layer (data.binance.vision, ingested daily; the source of record), plus a REST top-up of only closed bars newer than the archive (`mbpe.live.market`). The archive files are never modified;
  - GDELT 15-minute batches available by t (batch label + 15 min), with news dedup extended incrementally (`dedup(..., fixed=...)`, shown identical to a full run for rows processed in time order) and FinBERT scoring of new titles (`mbpe.live.news`).
- **Open-interest timestamps:** REST labels each 5-minute open-interest snapshot **5 minutes later** than the archive does. The values are identical: checked on 5 days of October 2026, every snapshot. Live rows are relabeled to the archive convention (REST time − 5 min), so live features equal the features the model was trained on, and REST and archive then match exactly.
  - **Research timing caveat.** If REST's timestamp is the true observation time, the archive's `create_time` is 5 minutes early. The backtest rule "metrics with create_time < t" then admitted the snapshot observed **at** t, not strictly before it, in every phase.
  - That snapshot is published within seconds of t, before the 1-minute entry, so the features were tradable. But the "strictly before t" convention was bent by at most the publication delay for the open-interest features (`oi_chg_*`, the OI/price quadrant).
  - This could only have helped the backtests, which found no edge. It's reported as a limitation, not corrected retroactively.
- **Staleness** (minutes from a source's last row to t, 1m bars counted from their close): futures, spot, mark and premium ≤ 5; metrics ≤ 20; funding ≤ 9 h.
  - Beyond these, or if a meta-model volatility input is missing, the hour's decision is "none" (logged with the reason, plus an alert).
  - Other missing features stay NaN, as in training. News coverage gating already turns GDELT outages into NaN.
- **Late runs:** a run starting more than 15 min after t makes **no decision** for t. It could otherwise see data that arrived after t. Missed and late hours are recorded, never backfilled.

**Model and log**
- **Frozen model:** `mbpe.live.model.freeze` (artifact in `data/models/phase6/<version>/`).
  - Version = sha256 of every fitted parameter. `load` refuses an artifact whose file hash doesn't match its manifest.
  - Predictions use the artifact's threshold; the threshold choice for the forward test is decided in Workstream E.
- **Prediction log:** `data/live/predictions.jsonl`, append-only, one record per t (a re-run for the same hour doesn't log twice). Each record has the model version, the class probabilities, expected net R, the candidate side, the meta P, the decision and its reason, per-source staleness, the missing features, and the sha256 of the feature vector.
- **Feature snapshot:** the full feature row is stored in `data/live/features/<YYYY-MM>.parquet` for the parity check.

**Paper executor** (`mbpe.live.paper`; no orders, no exchange keys)
- **Settlement:** each trade is settled by the label set A label code itself:
  - entry at the open of the 1m candle at t + 1 min; ±ATR(14, 1h) barriers;
  - stop checked first; gap fills at the open; 4 h horizon;
  - funding in (entry, exit]; taker costs, with maker_entry_tp reported.
- **Timing of settlement:** a barrier hit within the candles seen so far is final, and a full 4 h without a hit is an expiry. With a candle gap or unpublished funding, the trade waits; it is never guessed.
- **Position rule:** one position at a time; a new trade opens only at a decision time at or after the last exit, as in the backtest.
- **Ledger:** `data/live/paper/ledger.jsonl`, append-only (open / close / skip).

**Parity** (`mbpe.live.parity`, daily, on hours the archive now covers)
- **Market features:** recomputed from the archive alone. **Tolerance:** |live − archive| ≤ 1e-9 × max(1, |archive|), or both missing.
- **News, calendar and regulatory features:** recomputed with all news rows known at check time. Differences from late GDELT batches are expected and reported, not hidden.
- **Decisions:** re-made by the same model version on the archive features; the agreement rate is reported.
- **Paper trades:** must equal the label recomputed from archive candles and funding **exactly**. Any mismatch is treated as a bug: fixed, re-run, and both results reported.
- **Reporting:** parity results are reported with the forward test.

**Operations**
- **Alerts and summary:** failed steps and stale sources go to `data/live/alerts.jsonl` and the log. A daily summary is written to `data/live/daily/`. External notifications (e-mail, chat) aren't wired in: sending messages needs your setup and consent.
- **Scheduling examples (not installed):** `deploy/mbpe-live.cron`, `deploy/com.mbpe.live.plist`, `Dockerfile` + `docker-compose.yml` (now in `ops/`, amendment of 2026-10-29).
- **CI** (`.github/workflows/ci.yml`): ruff, format check, the tests and the generated-table check. Tests needing local data skip themselves.

### Phase 6 Workstream E: forward-test start record (2026-10-29, before the first forward decision)
- **Frozen model:** version `f4c4365501a9272b` (`data/models/phase6/f4c4365501a9272b/`).
  - `model.pkl` sha256 `0ca2321d0e10e3a0d6cdac733795f193f3950551622ae06742a68224af4e3780`.
  - Frozen from commit 74fd70f, trained on data before 2026-04-26.
  - The meta-model was trained on the 10,725 out-of-sample walk-forward candidates.
- **Threshold:** meta P(success) ≥ **0.30909969344992666**, the validation-chosen threshold (amendment of 2026-10-29).
- **First forward decision:** t = **2026-10-29 13:00 UTC**, run at 13:03 UTC by launchd on this Mac. Every hour from then on is a forward decision, recorded or recorded as missed or late.
- **Records:**
  - `data/live/predictions.jsonl` (decisions);
  - `data/live/paper/ledger.jsonl` (paper trades);
  - `data/live/features/` (inputs);
  - `data/live/parity/` (daily parity);
  - `data/live/alerts.jsonl`.
- **Verdict:** only after both ≥ 6 months of forward decision times (not before 2027-04-29) **and** ≥ 100 closed paper trades. Pass = mean net R (taker) > 0 with the 95% block-bootstrap CI lower bound > 0, **and** above always-long's mean net R over the same forward decision hours (computed from the archive with the label code). Anything else: the negative finding stands.
- **Interim reporting:** progress may be reported at any time (trade count, P&L so far, parity, data health), but **no verdict and no change to the model, threshold or rules before the verdict condition**. Any bug fix to the live system is made, logged in the amendments with its date, and its effect reported.

### Test B: limit-order execution (pre-registered 2026-10-29, before any Test B code or result)
- **Intent:** Test A loses about four times its raw signal to trading costs (+3.1 bps gross vs -10.8 bps net per trade, historically). Test B asks one narrower question: **would the same decisions make money if orders paid the lower maker fee, with realistic fills?** It is a second, separately pre-registered paper forward test that runs alongside Test A.
- **Only change:** the order type. No change to the model, threshold, features, barriers or horizon. No real orders and no exchange keys. Test A's code path, files and behaviour are unchanged; Test B writes only to `data/live/paper_b/`.
- **Start rule:** Test B starts at the first hour after it is wired into the hourly run. Earlier hours are never filled in, because they would be computed after their outcomes exist.
- **Notation:** t = decision time (the hour), L = Test A's entry price = open of the 1m candle starting at t + 1 min, ATR = the same decision ATR as Test A, side = +1 long / -1 short.

**Rules**
1. **Decisions:** Test B acts on every hour where Test A's prediction log says `long` or `short`. It uses the decision already logged; it never re-runs the model.
2. **Entry:** a limit order at L, placed at t + 1 min, valid for 15 minutes (the 1m candles starting t+1 … t+15).
   - **Fill:** in the first candle where the price trades strictly through L: low < L for a long, high > L for a short. The fill price is L. Touching L exactly is not a fill (others are ahead in the queue).
   - **No fill in 15 minutes:** the order is cancelled. No trade; logged as `unfilled`.
3. **Barriers:** fixed at order time from L, exactly as Test A: target = L + side·2·ATR, stop = L - side·1·ATR. 1 R = |L - stop| = 1 ATR.
4. **Exit window:** from the fill candle to the end of Test A's window (t + 1 min + 240 min). A later fill means a shorter trade; the end time never moves.
5. **Stop:** a market order. Counted from the fill candle itself (if the fill candle also reaches the stop, the stop wins). Triggered when the price touches the stop (same rule as the backtest). Exit at the stop, or at the candle open if the price gapped through it. Taker fee + slippage.
6. **Take-profit:** a resting limit order. Counted from the candle **after** the fill. Fills only when the price trades strictly through the target (high > target for a long, low < target for a short). Exit at the target. Maker fee, no slippage. If a candle reaches both the stop and the target, the stop wins (same tie rule as the backtest).
7. **Time exit:** at the close of the last candle in the window, as a market order. Taker fee + slippage.
8. **Costs:** maker fee 0.02% on the entry and on a take-profit; taker fee 0.05% + 2 bps slippage on a stop or time exit (values from `config/labels.yaml`). Funding is charged from the fill time to the exit, as in Test A.
9. **One position at a time** in Test B's own book. A pending (unfilled) order counts as a position. If Test B is busy, a new Test A decision is logged as a `skip`. Test B's position state is independent of Test A's: Test A's position state never affects Test B, and vice versa.
10. **Missed, late or stale hours:** inherited from Test A. No decision means nothing for Test B.

**Verdict**
- **When:** only after both ≥ 6 months from Test B's first decision **and** ≥ 100 closed (filled) trades.
- **Pass:** mean net R > 0 with the 95% block-bootstrap CI lower bound > 0 (same method as Test A), **and** above the always-long benchmark.
- **Always-long benchmark.** It is computed from the archive with the same function as Test B:
  - **Hours:** every hour from Test B's start where Test A logged a decision record (so missed, late and stale hours are excluded, as they are for Test B) and the hour is outside the CPI/FOMC/NFP blackout (the model's own eligibility, as in Phase 6's always-long).
  - **Each hour:** a long, with rules 2–9 applied: the same limit-order execution, its own one-position book, and a pending order blocking new ones. Funding is charged as for Test B.
  - It uses Test B's execution, not taker, so that cheaper costs alone can't beat it.
- **Also reported,** without being part of the pass rule: fill rate, and Test B against Test A on the hours both traded.
- **No change to the rules before the verdict.** A bug fix is logged as a dated amendment with its effect, as for Test A.

**Historical replay (development evidence)**
- Run once, after the live start; reported whatever it shows; rules not changed after it.
- Input: every LightGBM walk-forward candidate in folds 4–17 with meta P ≥ that fold's validation threshold, in time order, through Test B's own book (rules 2–9).
- Report:
  - decisions, attempted orders, fill rate;
  - Test B's mean net R with 95% CI;
  - the taker result (Test A's execution) on exactly the orders Test B attempted;
  - Test A's actual walk-forward result over folds 4–17, for reference;
  - the always-long benchmark over the same folds, as defined in the verdict.
- Reported as generated tables in `docs/PROGRESS.md` and `docs/FINDINGS.md`, whatever it shows.
- 2021–2026 data is spent, so this is development evidence only. It can't establish an edge.
