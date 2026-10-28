# MBPE: Multimodal Bitcoin Prediction Engine

A research MVP asking one question:

> **Can hourly signals from BTCUSDT perpetual-futures market data, derivatives data and news headlines predict trade outcomes well enough to make money after trading costs, and does news add anything over market data alone?**

**Answer: no.** Across every pre-registered test (a walk-forward evaluation, a single holdout run, three checklist extensions and a longer-horizon variant), no configuration has a tradable edge after costs. News features never improve on market data. The models find a small directional signal, a few basis points per trade in gross terms, but it is a fraction of the round-trip trading cost.

Every result number lives in [`reports/FINDINGS.md`](reports/FINDINGS.md), in tables generated from committed report files. This README describes the project and points there.

## Results by phase

| Phase | What was tested | Verdict | Tag |
|---|---|---|---|
| 3 | Hourly triple-barrier trades, two label sets × two cost scenarios × logistic regression / LightGBM × three feature rungs (technical → + derivatives → + news and calendar), purged walk-forward 2022 → Apr 2026, capped tuning, FinBERT vs CryptoBERT sentiment, deflated Sharpe ratio and PBO | No configuration profitable after costs; news adds nothing; no sign of a real edge after accounting for selection | `mbpe-research-v1` |
| 3 holdout | All 32 configurations on six unseen months (Apr–Oct 2026), run once under a pre-registered interpretation rule | Negative finding confirmed | `mbpe-research-v1` |
| 4 | Three checklist extensions, each against rung 3: MACD/EMA, NFP release calendar + blackout, order-book depth (Binance `bookDepth`) | No extension passes the kill criterion; Phase 3 finding stands | `mbpe-research-v1.1` |
| 5 | Longer horizon: 4-hourly decisions, 24 h trades, daily-ATR barriers, 20-permutation shuffled control | Pass criterion fails on all three conditions; no forward test | `mbpe-research-v1.2` |

The holdout was spent in Phase 3. Phases 4 and 5 are walk-forward only on data already studied, so their results are development evidence.

## How the study was kept honest
- **Pre-registration:** every protocol choice was written to [`SCOPE.md`](SCOPE.md) and committed before the results it governs existed. Later changes are dated amendments that state their effect.
  - Covered: holdout date, folds, rungs, thresholds and entry rules, tuning budget, kill and pass criteria, interpretation rules.
- **One holdout, spent once:** loading holdout rows raises an error unless an explicit flag is passed, and every allowed access is appended to `data/logs/holdout_access.jsonl`. The holdout was opened exactly once, for the pre-registered Phase 3 run.
- **Leakage controls:**
  - every feature uses only data timestamped before the decision time;
  - truncation tests recompute every feature on data cut at t and require identical values;
  - tests confirm that a deliberately leaky feature is caught;
  - purged walk-forward with label-overlap weights and an embargo.
- **Controls next to every model:**
  - always-long and always-short;
  - random entry and a volatility filter at the model's own trade frequency (20 seeds each);
  - a shuffled-label control (20 permutations in Phase 5).
- **Selection accounted for:** a deflated Sharpe ratio with the full trial count, and PBO via CSCV.
- **No hand-typed result numbers:** every results table in `PROGRESS.md` and `FINDINGS.md` sits between `<!-- generated:… -->` markers. It is rebuilt from `reports/` by `mbpe.reports.progress`, and `tests/test_progress.py` fails on any mismatch.
- **Reproduction checks:** later phases re-run earlier configurations through the modified code and must match the committed numbers exactly.

## Documents
| File | Contents |
|---|---|
| [`reports/FINDINGS.md`](reports/FINDINGS.md) | The write-up: question, method, results, holdout, Phases 4–5, limitations, lessons |
| [`SCOPE.md`](SCOPE.md) | Locked scope, every pre-registration, and amendments |
| [`PROGRESS.md`](PROGRESS.md) | Step-by-step log of every phase, with full generated tables (per fold, per configuration) |
| [`FEASIBILITY.md`](FEASIBILITY.md) | Data-source feasibility checks (Binance archive, GDELT, order book, NFP calendar, Reddit API) |
| [`REFERENCE_NOTES.md`](REFERENCE_NOTES.md) | Notes from the reference repositories |
| [`labeling/GUIDE.md`](labeling/GUIDE.md) | Guide for hand-labeling headline tone (optional benchmark) |

## Data (free sources only)
- **Binance public archive** ([data.binance.vision](https://data.binance.vision)), BTCUSDT USD-M:
  - 1m klines (futures and spot), 5-minute metrics (open interest), funding rates (with a REST fallback for months not yet archived, cross-checked once archived), `bookDepth` order-book snapshots from 2023;
  - every file is SHA-256 verified.
- **GDELT GKG 2.1:** headlines via BigQuery for the 2020–2026 backfill, and the 15-minute files for live collection. Parity between the two was checked on full days.
- **Macro calendar:** US CPI, FOMC and NFP release times from the official BLS and Federal Reserve schedules (`data/reference/macro_events.csv`, with a source per row).

`data/` is git-ignored, except the BigQuery run records and the reference calendar. No credentials are stored in the repository.

## Repository layout
```
mbpe/
  sources/      Binance archive, GDELT BigQuery backfill + live collector + parity check, Reddit collector
  storage/      clean layer: validated Parquet per dataset, gap reports
  labels/       triple-barrier labels (configurable bars, horizon, cadence, cost scenarios)
  features/     market + derivatives (incl. MACD/EMA), news dedup + sentiment + news/calendar, Phase 4 (NFP, order book)
  labeling/     terminal headline-labeling tool and scorer agreement
  validation/   walk-forward splitter + holdout guard, harness, backtest, tuning, overfitting (DSR, PBO),
                holdout run, Phase 4 and Phase 5 runners
  reports/      generated-table builder for PROGRESS.md / FINDINGS.md
config/         evaluation protocol, label sets (A, B, P5), features, news configs
reports/        committed results per phase (CSV / JSON), the sources of every generated table
tests/          leakage, label, harness, overfitting, report-consistency tests
deploy/         cron / systemd examples for the live collectors (not installed)
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

The pipeline, in order (each step writes under `data/` or `reports/`):

```bash
uv run python -m mbpe.ingest                                   # Binance archive -> clean Parquet + gap report
uv run python -m mbpe.sources.gdelt_bq dry-run                 # bytes the backfill will scan (free)
uv run python -m mbpe.sources.gdelt_bq run                     # GDELT headline backfill (BigQuery)
uv run python -m mbpe.labels.triple_barrier config/labels.yaml     # label set A (and config/labels_B.yaml)
uv run python -m mbpe.features.market                          # market + derivatives features
uv run python -m mbpe.features.dedup                           # near-duplicate headline clusters
uv run python -m mbpe.features.sentiment score-all             # FinBERT / CryptoBERT scores
uv run python -m mbpe.features.news                            # news + calendar features (config/news.yaml)
uv run python -m mbpe.validation.harness                       # Phase 3 step 1
uv run python -m mbpe.validation.tuning --rung 1               # capped tuning, one process per rung (1-3)
uv run python -m mbpe.validation.step2                         # Phase 3 step 2
uv run python -m mbpe.validation.overfitting                   # deflated Sharpe ratio, PBO
uv run python -m mbpe.ingest 2023-01-01 --only book_depth      # Phase 4 order-book data
uv run python -m mbpe.features.phase4                          # NFP + order-book features
uv run python -m mbpe.validation.phase4                        # Phase 4 evaluation
uv run python -m mbpe.labels.triple_barrier config/labels_P5.yaml  # Phase 5 labels
uv run python -m mbpe.validation.phase5 step2                  # Phase 5 evaluation
uv run python -m mbpe.reports.progress --check                 # generated tables match reports/
uv run pytest
```

- The CryptoBERT comparison uses `config/news_cryptobert.yaml` and `mbpe.validation.cryptobert_compare` (`tune`, then `evaluate`).
- `mbpe.validation.holdout` runs the **spent** holdout. Re-running it appends to the access log and must be reported as a second access.

## Live collectors (optional, forward data only)
- `mbpe.sources.gdelt_live`: GDELT GKG 15-minute batches with 48 h catch-up.
- `mbpe.sources.reddit_live`: r/Bitcoin and r/CryptoCurrency titles via the official API. Credentials are read from environment variables only.

Example schedules are in `deploy/` (not installed). Neither collector feeds any reported result. Any forward test would use the archives (Binance and GDELT bulk files), fetched after the fact.

## Limitations
- **Narrow scope:** one instrument (BTCUSDT perpetual) and one barrier geometry per phase.
- **Inputs:** headline-only news from a fixed keyword list, and free data only. No paid feeds, social data or full article text.
- **Costs:** a simple taker model (fixed fee + slippage), and a maker scenario that ignores queue position and fills.
- **Re-used data:** Phases 4–5 re-use data studied in Phase 3, so they can only rule ideas out or trigger a forward test.

Details are in `reports/FINDINGS.md` ("Limitations" and each phase's notes).

## Attribution
- `cscv_pbo` in `mbpe/validation/overfitting.py` is adapted from [LLM-Auto-Trader](https://github.com/Erfaniaa/LLM-Auto-Trader) (MIT, © 2026 Erfan Alimohammadi); the license is in `licenses/`.
- [freqtrade](https://github.com/freqtrade/freqtrade) (GPL) was consulted for ideas only; no code was copied.
- Sentiment models: FinBERT (primary) and CryptoBERT (secondary comparison), via Hugging Face `transformers`.
