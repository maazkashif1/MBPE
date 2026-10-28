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
- **Scorers:** ProsusAI/finbert (default) vs one crypto-specific BERT, compared on the hand-labeled sample.
- **Label definition:** labels are financial-news tone (positive / negative / neutral), not the expected BTC price impact (`labeling/GUIDE.md`).
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
