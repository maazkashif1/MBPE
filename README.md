# MBPE: Multimodal Bitcoin Prediction Engine

A research MVP asking one question:

> **Can hourly signals from BTCUSDT perpetual-futures market data, derivatives data and news headlines predict trade outcomes well enough to make money after trading costs, and does news add anything over market data alone?**

**Answer so far: no.** Across every pre-registered historical test (a walk-forward evaluation, a single holdout run, three checklist extensions, a longer-horizon variant, and the full scope-doc system with a three-class model and meta-labeling), no configuration has a tradable edge after costs. News features never improve on market data. The models find a small directional signal, a few basis points per trade in gross terms, but it is a fraction of the round-trip trading cost.

**The full scoped system is now being forward-tested** on live data with paper trading (no real orders). This is the only test that can still establish an edge. The verdict comes after at least 6 months and 100 trades.

Every result number lives in [`reports/FINDINGS.md`](reports/FINDINGS.md) and [`PROGRESS.md`](PROGRESS.md), in tables generated from committed report files. This README describes the project and points there.

## Results by phase

| Phase | What was tested | Verdict | Tag |
|---|---|---|---|
| 3 | Hourly triple-barrier trades, two label sets × two cost scenarios × logistic regression / LightGBM × three feature rungs (technical → + derivatives → + news and calendar), purged walk-forward 2022 → Apr 2026, capped tuning, FinBERT vs CryptoBERT sentiment, deflated Sharpe ratio and PBO | No configuration profitable after costs; news adds nothing; no sign of a real edge after accounting for selection | `mbpe-research-v1` |
| 3 holdout | All 32 configurations on six unseen months (Apr–Oct 2026), run once under a pre-registered interpretation rule | Negative finding confirmed | `mbpe-research-v1` |
| 4 | Three checklist extensions, each against rung 3: MACD/EMA, NFP release calendar + blackout, order-book depth (Binance `bookDepth`) | No extension passes the kill criterion; Phase 3 finding stands | `mbpe-research-v1.1` |
| 5 | Longer horizon: 4-hourly decisions, 24 h trades, daily-ATR barriers, 20-permutation shuffled control | Pass criterion fails on all three conditions; no forward test | `mbpe-research-v1.2` |
| 6 | The full scope doc: mark-price / premium-index and regulatory-news features; three-class primary model (LightGBM and CatBoost); meta-labeling filter on out-of-sample predictions; SHAP; targets table; regimes; failure cases; a live system and a paper-trading simulator | Historically, no configuration profitable after costs. The meta-filter helps a little, not enough to pay costs. Development evidence only | — |
| Forward test | The frozen Phase 6 system, paper-traded live from **2026-10-29 13:00 UTC** | **Running.** No verdict before ≥ 6 months **and** ≥ 100 trades | — |

- **The holdout was spent in Phase 3.** Phases 4–6 are walk-forward only, on data already studied, so their historical results are development evidence.
- **Only the forward test is new, unseen data.**

## Live system and forward test

**What runs** (on this Mac, via launchd; [`docs/RUNBOOK.md`](docs/RUNBOOK.md) has setup, checks and how to stop it):

| Job | When |
|---|---|
| `mbpe.live.run`: the decision for the hour that just closed, then the paper executor | minute 3 of every hour (UTC) |
| `mbpe.ingest` then `mbpe.live.parity`: archive ingest, then the live-vs-archive parity check | daily |
| `mbpe.sources.rss_live`: crypto news headlines (forward-only data) | every 15 min |
| `mbpe.sources.liquidations_live`: Binance liquidation stream (forward-only data) | continuously |

**Each hourly run:**
1. Tops up Binance market data from the free REST API, into a separate live store. The archive is the source of record and is never modified.
2. Collects the newest GDELT batches, extends the news clusters, and scores new titles with FinBERT.
3. Computes all 103 features at the decision time with **the same feature code as the backtest**.
4. Applies the **frozen** model and logs the decision.
5. Paper-trades it.

**The frozen system:**
- **Model:** version `f4c4365501a9272b`. That's a LightGBM three-class primary model per side plus a meta-labeling filter, trained only on data before 2026-04-26.
- **Threshold:** the meta filter's validation-chosen threshold (0.3091). This is a dated amendment made after seeing the historical results, where that variant still lost money after costs; it's stated as such in `SCOPE.md`.
- **Pinned:** the model version and threshold are fixed in a start record committed before the first decision.

**Rules** (fixed in `SCOPE.md`, "Phase 6 Workstream D/E"):
- **No orders and no exchange keys.** Every paper trade is settled by the backtest's own label code (entry at t + 1 min, ATR barriers, 4 h horizon, funding and costs), with the backtest's one-position rule.
- **No decision on bad inputs:** stale inputs, a run starting more than 15 min late, or a missed hour (machine asleep) all mean no decision. Nothing is backfilled with hindsight.
- **Daily parity check:** live features, decisions and paper trades are recomputed from the archive and compared. Paper trades must equal the archive labels exactly.
- **Verdict:** only after ≥ 6 months (not before 2027-04-29) **and** ≥ 100 closed trades. Pass = mean net R after taker costs > 0 with the 95% CI lower bound > 0, **and** above always-long over the same hours. No changes to the model, threshold or rules before then.

**Records** (`data/live/`, git-ignored):
- `predictions.jsonl`: one record per hour;
- `paper/ledger.jsonl`: paper trades, append-only;
- `features/`: the inputs behind each decision;
- `parity/`, `alerts.jsonl`, `daily/`.

## How the study was kept honest
- **Pre-registration:** every protocol choice was written to [`SCOPE.md`](SCOPE.md) and committed before the results it governs existed. Later changes are dated amendments that state their effect and whether they followed any result.
  - Covered: holdout date, folds, rungs, thresholds and entry rules, tuning budget, kill and pass criteria, interpretation rules, live-system rules and the forward-test start.
- **One holdout, spent once:** loading holdout rows raises an error unless an explicit flag is passed, and every allowed access is appended to `data/logs/holdout_access.jsonl`. The holdout was opened exactly once, for the pre-registered Phase 3 run.
- **Leakage controls:**
  - every feature uses only data timestamped before the decision time;
  - truncation tests recompute every feature on data cut at t and require identical values;
  - tests confirm that a deliberately leaky feature is caught;
  - purged walk-forward with label-overlap weights and an embargo.
- **Controls next to every model:**
  - always-long and always-short;
  - random entry and a volatility filter at the model's own trade frequency (20 seeds each);
  - shuffled-label controls (20 permutations from Phase 5 on).
- **Selection accounted for:** a deflated Sharpe ratio with the full trial count, and PBO via CSCV.
- **No hand-typed result numbers:** every results table in `PROGRESS.md` and `FINDINGS.md` sits between `<!-- generated:… -->` markers. It is rebuilt from `reports/` by `mbpe.reports.progress`, and `tests/test_progress.py` fails on any mismatch.
- **Reproduction checks:** later phases re-run earlier configurations through the modified code and must match the committed numbers exactly.
- **The live system is held to the backtest:**
  - the live paper loop, run hour by hour, reproduces the backtest's labels and position rule exactly (test);
  - on archived windows, Binance's REST API returns rows identical to the archive;
  - the frozen model's predictions equal the research code's.
- **CI** (`.github/workflows/ci.yml`): ruff, the format check, the tests and the generated-table check on every push.

## Documents
| File | Contents |
|---|---|
| [`reports/FINDINGS.md`](reports/FINDINGS.md) | The write-up: question, method, results, holdout, Phases 4–5, limitations, lessons |
| [`PROGRESS.md`](PROGRESS.md) | Step-by-step log of every phase incl. Phase 6 (Workstreams A–D), with full generated tables |
| [`SCOPE.md`](SCOPE.md) | Locked scope, every pre-registration, amendments, and the forward-test start record |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Operating the live system: setup, schedules, daily checks, troubleshooting, stopping |
| [`FEASIBILITY.md`](FEASIBILITY.md) | Data-source feasibility checks (Binance archive, GDELT, order book, NFP calendar, Reddit API, live depth) |
| [`REFERENCE_NOTES.md`](REFERENCE_NOTES.md) | Notes from the reference repositories |
| [`labeling/GUIDE.md`](labeling/GUIDE.md) | Guide for hand-labeling headline tone (optional benchmark) |

## Data (free sources only)
- **Binance public archive** ([data.binance.vision](https://data.binance.vision)), BTCUSDT USD-M, every file SHA-256 verified:
  - 1m klines (futures and spot), 5-minute metrics (open interest), funding rates;
  - 1m mark-price and premium-index klines;
  - `bookDepth` order-book snapshots from 2023.
- **Binance REST API** (public, no keys): the live top-up of everything the archive hasn't published yet.
  - REST labels open-interest snapshots 5 minutes later than the archive, with identical values. Live rows are relabeled to the archive convention.
- **GDELT GKG 2.1:** headlines via BigQuery for the 2020–2026 backfill, and the 15-minute files for live collection. Parity between the two was checked on full days.
- **Macro calendar:** US CPI, FOMC and NFP release times from the official BLS and Federal Reserve schedules (`data/reference/macro_events.csv`, with a source per row).
- **Forward-only collectors:** RSS headlines (CoinDesk, Cointelegraph, The Block, Decrypt, Bitcoin Magazine), Binance liquidations, and Reddit (once credentials are set). None has free history, so none is in the frozen model; they're collected for a later, separately pre-registered model.

`data/` is git-ignored, except the BigQuery run records and the reference calendar. No credentials are stored in the repository.

## Repository layout
```
mbpe/
  sources/      Binance archive, GDELT BigQuery backfill + live collector + parity check, RSS, liquidations, Reddit
  storage/      clean layer: validated Parquet per dataset, gap reports
  labels/       triple-barrier labels (configurable bars, horizon, cadence, cost scenarios)
  features/     market + derivatives (incl. MACD/EMA, mark/premium), news dedup + sentiment + news/calendar/
                regulatory, Phase 4 (NFP, order book)
  labeling/     terminal headline-labeling tool and scorer agreement
  validation/   walk-forward splitter + holdout guard, harness, backtest, tuning, overfitting (DSR, PBO),
                holdout run, Phase 4-6 runners (three-class primary, meta-labeling, SHAP)
  live/         live market top-up, incremental news, live features, frozen model, paper executor,
                hourly orchestrator, parity check, scheduler
  reports/      generated-table builder for PROGRESS.md / FINDINGS.md
config/         evaluation protocol, label sets (A, B, P5), features, news configs
reports/        committed results per phase (CSV / JSON), the sources of every generated table
tests/          leakage, label, harness, overfitting, live-system and report-consistency tests
deploy/         cron / systemd / launchd examples (generic paths)
docs/           RUNBOOK.md
Dockerfile, docker-compose.yml   container setup for the live system
feasibility.py  read-only data-source checks behind FEASIBILITY.md
```

## Reproducing
Requirements:
- Python 3.12 with [uv](https://docs.astral.sh/uv/);
- on macOS, `brew install libomp` for LightGBM;
- for the news backfill, a Google Cloud project with BigQuery and local application-default credentials (`gcloud auth application-default login`). The backfill query reads several hundred GB, so dry-run it first.

```bash
uv sync
```

The research pipeline, in order (each step writes under `data/` or `reports/`):

```bash
uv run python -m mbpe.ingest                                   # Binance archive -> clean Parquet + gap report
uv run python -m mbpe.sources.gdelt_bq dry-run                 # bytes the backfill will scan (free)
uv run python -m mbpe.sources.gdelt_bq run                     # GDELT headline backfill (BigQuery)
uv run python -m mbpe.labels.triple_barrier config/labels.yaml     # label set A (and config/labels_B.yaml)
uv run python -m mbpe.features.market                          # market, derivatives, MACD/EMA, mark/premium features
uv run python -m mbpe.features.dedup                           # near-duplicate headline clusters
uv run python -m mbpe.features.sentiment score-all             # FinBERT / CryptoBERT scores
uv run python -m mbpe.features.news                            # news, calendar and regulatory features
uv run python -m mbpe.validation.harness                       # Phase 3 step 1
uv run python -m mbpe.validation.tuning --rung 1               # capped tuning, one process per rung (1-3)
uv run python -m mbpe.validation.step2                         # Phase 3 step 2
uv run python -m mbpe.validation.overfitting                   # deflated Sharpe ratio, PBO
uv run python -m mbpe.ingest 2023-01-01 --only book_depth      # Phase 4 order-book data
uv run python -m mbpe.features.phase4                          # NFP + order-book features
uv run python -m mbpe.validation.phase4                        # Phase 4 evaluation
uv run python -m mbpe.labels.triple_barrier config/labels_P5.yaml  # Phase 5 labels
uv run python -m mbpe.validation.phase5 step2                  # Phase 5 evaluation
uv run python -m mbpe.validation.phase6_eval                   # Phase 6 evaluation (three-class + meta + SHAP)
uv run python -m mbpe.reports.progress --check                 # generated tables match reports/
uv run pytest
```

The mark-price and premium-index data come from `mbpe.ingest` (`--only mark_price_1m` / `premium_index_1m` from 2020-09-01).

The live system:

```bash
uv run python -m mbpe.live.model freeze --threshold val        # frozen model -> data/models/phase6/<version>/
uv run python -m mbpe.live.run --model data/models/phase6/<version>   # one hourly cycle
uv run python -m mbpe.live.parity                              # live vs archive
```

- The CryptoBERT comparison uses `config/news_cryptobert.yaml` and `mbpe.validation.cryptobert_compare` (`tune`, then `evaluate`).
- `mbpe.validation.holdout` runs the **spent** holdout. Re-running it appends to the access log and must be reported as a second access.

## Limitations
- **Narrow scope:** one instrument (BTCUSDT perpetual) and one barrier geometry per phase.
- **Inputs:** headline-only news from a fixed keyword list, and free data only. No paid feeds or full article text. Social, liquidation and other forward-only data aren't in any historical test.
- **Costs:** a simple taker model (fixed fee + slippage), and a maker scenario that ignores queue position and fills.
- **Re-used data:** Phases 4–6 re-use data studied in Phase 3, so they can only rule ideas out or feed a forward test.
- **Open-interest timing:** the archive labels open-interest snapshots 5 minutes earlier than the live API. The backtests may have used the snapshot taken at the decision time rather than strictly before it. It's available within seconds, before the 1-minute entry, so it's tradable, and it could only have flattered results that showed no edge.
- **The forward test runs on a laptop:** hours missed while it sleeps count as no decision, which slows the path to 100 trades.

Details are in `reports/FINDINGS.md` ("Limitations" and each phase's notes) and `SCOPE.md`.

## Attribution
- `cscv_pbo` in `mbpe/validation/overfitting.py` is adapted from [LLM-Auto-Trader](https://github.com/Erfaniaa/LLM-Auto-Trader) (MIT, © 2026 Erfan Alimohammadi); the license is in `licenses/`.
- [freqtrade](https://github.com/freqtrade/freqtrade) (GPL) was consulted for ideas only; no code was copied.
- Sentiment models: FinBERT (primary) and CryptoBERT (secondary comparison), via Hugging Face `transformers`.
