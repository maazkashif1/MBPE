# MBPE live system runbook (paper trading only)

The live system makes one decision per UTC hour with the frozen Phase 6 model and paper-trades it with the backtest's own rules. **It never places orders and uses no exchange keys.** The rules it follows are fixed in `SCOPE.md` ("Phase 6 Workstream D: live system definitions").

## What runs

| Job | Command | When |
|---|---|---|
| Hourly decision | `uv run python -m mbpe.live.hourly_run` | minute 3 of every hour (UTC) |
| Archive ingest | `uv run python -m mbpe.data.ingest` | daily (the archive publishes each day ~a day late) |
| Parity check | `uv run python -m mbpe.live.parity` | daily, after the ingest |
| RSS headlines (forward-only data) | `uv run python -m mbpe.data.rss` | every 15 min |
| Liquidations (forward-only data) | `uv run python -m mbpe.data.liquidations` | long-running |
| Reddit (forward-only data) | `uv run python -m mbpe.data.reddit` | every 15 min, **only once credentials are set** |
| Dashboard (read-only) | `uv run python -m dashboard.server` | long-running, http://localhost:8765 |

The hourly run itself does the market top-up (Binance REST), the GDELT live collection, news dedup and FinBERT scoring, features, the decision, the logs and the paper executor.

## First-time setup
1. **Install:** `uv sync`. On macOS also run `brew install libomp`.
2. **Data:**
   - Run the archive ingest (`uv run python -m mbpe.data.ingest`). Include `--only mark_price_1m` / `premium_index_1m` / `book_depth` from their start dates if they're missing.
   - The GDELT history comes from the BigQuery backfill (`mbpe.data.gdelt_backfill`, needs your gcloud login).
   - Then build the labels and features as in the README.
3. **Freeze the model:** `uv run python -m mbpe.live.frozen_model freeze [--threshold X]`. It writes `data/models/phase6/<version>/` with `manifest.json`. The forward test's threshold is decided in Workstream E; the default is the pre-registered 0.60.
4. **Credentials** (only for the optional collectors): put `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` in an env file **outside the repo** (e.g. `~/.config/mbpe/reddit.env`, `chmod 600`). Never commit them; `.gitignore` covers `.env*`.
5. **Schedule**, choosing one of:
   - macOS launchd (what runs the forward test): `ops/jobs.sh install`. It renders `ops/launchd/*.plist` with this repo's path, your `uv` and the model (`MODEL=...` to override), writes them to `~/Library/LaunchAgents`, and loads them. The daily ingest is set to 02:30 UTC in local time;
   - cron: `ops/cron/mbpe-live.cron`, `ops/cron/rss-live.cron`;
   - systemd: `ops/systemd/`;
   - Docker: `docker compose -f ops/docker/compose.yml up -d`, with `data/` mounted. The dashboard is published on 127.0.0.1:8765 only.

   **A sleeping laptop misses hours.** Missed and late hours are recorded as "no decision", never backfilled. An always-on machine or VM avoids this.

## Daily checks
- **`data/live/daily/<date>.json`:** predictions, decisions, alerts, and paper P&L so far.
- **`data/live/alerts.jsonl`:** failed steps, stale sources, late runs. A run of `stale_source` alerts usually means Binance or GDELT is lagging. If it persists, check network access.
- **`data/live/parity/<date>.json`:** live vs archive.
  - Market features must be within tolerance.
  - Paper trades must equal the archive labels **exactly**. A mismatch is a bug: stop, investigate, fix, re-run, and report both.
- **`data/logs/live.log`:** the full log of each run.

## The launchd jobs
| Command | What |
|---|---|
| `ops/jobs.sh status` | loaded or not, pid, last exit code |
| `ops/jobs.sh reload [job ...]` | re-render and reload. Waits for a running hourly or ingest job to finish, and for the old job to unload, before loading the new one |
| `ops/jobs.sh stop [job ...]` | unload (same waits) |
| `ops/jobs.sh diff` | installed plists vs the templates |
| `ops/jobs.sh logs [job ...]` | last 20 lines of each job's log |

Jobs: `live`, `ingest`, `rss`, `liquidations`, `dashboard`. With no names, all of them.

## Dashboard
http://localhost:8765 shows the net result, win rate, profit and losses, profit factor, drawdown, the open position, the equity curve, progress to the verdict, the hourly decisions and the health of each job. It refreshes every 30 s, only reads `data/live/` and `data/raw/`, and listens on localhost only. Figures are interim, never a verdict.

## Files
| Path | What |
|---|---|
| `data/live/predictions.jsonl` | one record per decision hour (append-only) |
| `data/live/features/<YYYY-MM>.parquet` | the feature row behind each decision |
| `data/live/btcusdc/*.parquet` | BTCUSDC 1m candles and funding that Tests A and B trade on (extended each run) |
| `data/live/archive/btcusdt/` | Test A and B records from before the BTCUSDC restart (2026-10-29 13:00–19:00 UTC) |
| `data/live/paper/ledger.jsonl`, `state.json` | paper trades (append-only) and the open position |
| `data/live/paper_b/ledger.jsonl`, `state.json` | Test B limit-order events (append-only) and the pending order |
| `data/live/binance/*.parquet` | REST top-up newer than the archive (rebuilt each run; the archive is never modified) |
| `data/live/news_dedup.parquet` | news clusters extended incrementally (the research file is never modified) |
| `data/models/phase6/<version>/` | frozen model + manifest (version = hash of all fitted parameters) |

## Troubleshooting
- **"archive is N days behind"**: run `uv run python -m mbpe.data.ingest`. The REST top-up covers at most 7 days.
- **No decision with reason "stale or missing inputs"**: a source was late at run time. The hour is recorded as no-trade, by design.
- **"late run"**: the run started more than 15 min after the hour, for example after sleep. The hour is no-trade, by design.
- **An open paper position doesn't close**: its settlement waits for complete 1m candles and published funding. It settles on a later run, never guessed.
- **`load` refuses a model**: `model.pkl` doesn't match its manifest hash. Re-freeze; don't edit artifacts.

## Stopping
Stop the scheduler (`ops/jobs.sh stop` / remove the cron lines / `docker compose -f ops/docker/compose.yml down`). Logs, ledgers and predictions stay in `data/live/`.
