# Test B (limit orders) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second paper forward test that trades Test A's hourly decisions with limit-order execution and conservative fills, plus a one-time historical replay.

**Architecture:** One shared module computes a limit-order trade from 1m candles and funding (`limit_order_trade`), plus a one-position book (`run_book`). The live executor (`paper_trading_b.step`) runs inside the hourly run after Test A's paper step and writes only to `data/live/paper_b/`. The replay script uses the same functions on the Phase 6 walk-forward candidates and writes `reports/test_b/`, rendered into the docs by a generated block.

**Tech Stack:** Python 3.12, polars, numpy, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-29-test-b-limit-orders-design.md` (approved 2026-10-29). Rules are referred to as R1–R10 from its "Rules" section.

## Global Constraints
- No comments or docstrings in any Python file, tests included.
- Test A's code path, files and behaviour are unchanged. Test B writes only to `data/live/paper_b/` and `reports/test_b/`.
- A Test B failure is its own `paper_b` step failure in the hourly run and never stops or alters Test A.
- Costs come from `config/labels.yaml` via `mbpe.labels.triple_barrier.load_config`: maker_fee 0.0002, taker_fee 0.0005, slippage_bps 2.0, tp_atr 2.0, sl_atr 1.0, latency_minutes 1, horizon_minutes 240.
- Fill window: 15 candles (t+1 … t+15 min). Trade-through is strict (`<` / `>`); a touch is not a fill and not a take-profit. A stop triggers on a touch (`<=` / `>=`), as in `_scan`.
- Results in docs are generated blocks only; never hand-typed numbers.
- Commit only the task's files; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Code and results are separate commits.
- `uv run ruff check . && uv run ruff format --check . && uv run pytest -q && uv run python -m mbpe.reporting.generated_tables --check` passes at the end of every task.

## Review Focus
- A gap in the 1m candles inside the fill window or the trade window → `limit_order_trade` returns `None` (live: wait; replay: count as `missing_candles`), never a fill or exit guessed across the gap. Test: `test_gap_returns_none`.
- A fill on the 15th candle (t+15 min) is a fill; nothing on the 16th candle counts. Test: `test_fill_window_edges`.
- The fill candle reaching the take-profit doesn't exit; the fill candle reaching the stop does, with the stop winning. Test: `test_fill_candle_stop_and_tp`.
- Funding missing for part of a trade's window → `None` (wait), as in Test A's `settle`. Test: `test_missing_funding_returns_none`.
- The hourly run re-running for an already-logged hour (the `logged_times` path) must not place a second Test B order. Test: `test_step_is_idempotent_for_same_decision`.

---

### Task 1: Pre-register Test B in SCOPE

**Files:**
- Modify: `docs/SCOPE.md` (new subsection after "Phase 6 Workstream E: forward-test start record")

- [ ] **Step 1:** Add `### Test B: limit-order execution (pre-registered 2026-10-29, before any Test B code or result)`. It contains, copied from the spec: Intent (two sentences), R1–R10 verbatim, the Verdict section including the always-long benchmark definition, the start rule ("first hour after it is wired into the hourly run; earlier hours are never filled in"), and the historical-replay paragraph ("run once, after the live start; reported whatever it shows; rules not changed after it").
- [ ] **Step 2:** Verify: `uv run python -m mbpe.reporting.generated_tables --check` → `all generated tables match their sources`.
- [ ] **Step 3:** Commit `docs/SCOPE.md` and the spec and plan files: `Pre-register Test B (limit-order execution) before any Test B code`.

### Task 2: Shared limit-order trade function and book

**Files:**
- Create: `mbpe/labels/limit_orders.py`
- Test: `tests/test_limit_orders.py`

**Interfaces:**
- Consumes: `LabelConfig`, `load_config`, `MissingFundingError`, `check_funding_coverage` from `mbpe.labels.triple_barrier`.
- Produces:
  - `FILL_MINUTES = 15`
  - `limit_order_trade(decision_ms: int, side: int, atr: float, candles: pl.DataFrame, funding: pl.DataFrame, cfg: LabelConfig) -> dict | None`. `candles` has `open_time` (Datetime ms UTC), `open`, `high`, `low`, `close`; `funding` has `calc_time`, `last_funding_rate`, `funding_interval_hours` (same frames as `label_trades`). Returns `None` if not yet determinable (missing candles or funding). Otherwise one of:
    - `{"status": "unfilled", "decision_ms", "side", "limit_price", "tp", "sl", "free_ms"}` with `free_ms = decision_ms + (1 + 15) * 60_000`;
    - `{"status": "closed", "decision_ms", "side", "limit_price", "tp", "sl", "fill_ms", "exit_ms", "exit_price", "exit_reason" ("tp"|"sl"|"expiry"), "gross_R", "fee_R", "slippage_R", "funding_R", "net_R", "free_ms"}` with `free_ms = exit_ms`.
  - `run_book(decisions: list[tuple[int, int, float]], candles, funding, cfg) -> list[dict]`: decisions are `(decision_ms, side, atr)` in time order. Applies R9: a decision with `decision_ms < free_ms` of the previous result gives `{"status": "skip", "decision_ms", "reason": "busy"}`. A `None` result gives `{"status": "missing_candles", "decision_ms"}` and doesn't block.

**Algorithm (pinned by the spec):** L = open of the candle at `decision_ms + 60_000`. tp = L + side·2·atr, sl = L − side·1·atr. Fill index f = first of candles 0..14 with `low < L` (long) / `high > L` (short). Trade window = candles f … 239 (end fixed at entry + 240 min). Stop from f, triggered by a touch: exit at sl, or at the candle open if the open is already beyond sl (gap; never at fill candle f). TP from f+1, strictly through. Ties: stop wins. Expiry at the close of candle 239. `exit_ms` = candle open + 60_000 (gap: candle open), as in `label_trades`. Unit = |L − sl|. Fees: maker on entry (L); exit maker if tp else taker; slippage 0 on entry and tp, else `slippage_bps/1e4 · exit_price`. Funding from `fill_ms` (candle f open) to `exit_ms`, as `label_trades` computes `rate`. net_R = gross − fee − slippage + funding.

- [ ] **Step 1: Write failing tests** on a synthetic 1m candle frame (flat 100.0 with OHLC overrides per minute, zero funding every 8 h, atr = 1.0, decision at 2026-01-01 00:00):
  - `test_unfilled_when_only_touched`: long, every low == 100.0 → `status == "unfilled"`, `free_ms == decision + 16 min`.
  - `test_fill_window_edges`: low 99.9 at candle 14 → filled at candle 14 (`fill_ms == decision + 15 min`); the same dip at candle 15 only → unfilled.
  - `test_take_profit_needs_trade_through`: fill at candle 0, high == 102.0 at candle 5 → not tp; high 102.1 at candle 6 → `exit_reason == "tp"`, `exit_price == 102.0`, `gross_R == pytest.approx(2.0)`.
  - `test_fill_candle_stop_and_tp`: fill candle 3 with low 98.9 → `exit_reason == "sl"`, `exit_ms == candle 3 open + 60 s`; fill candle 3 with high 102.5 and no later move → not tp (expiry).
  - `test_costs`: tp trade → `fee_R == pytest.approx((0.0002*100 + 0.0002*102) / 1)`, `slippage_R == 0`; sl trade → `fee_R == pytest.approx(0.0002*100 + 0.0005*99)`, `slippage_R == pytest.approx(2e-4*99)`.
  - `test_short_mirror`: a short with high 100.1 at candle 0 and low 97.9 later → tp, `gross_R == pytest.approx(2.0)`.
  - `test_gap_returns_none`: drop candle 100 → `None`.
  - `test_missing_funding_returns_none`: funding frame ending before the exit → `None`.
  - `test_run_book_busy_and_free`: three decisions 1 h apart; the first holds a position for 3 h → second `skip`, third trades; an unfilled order frees the book at `free_ms`.
- [ ] **Step 2:** `uv run pytest tests/test_limit_orders.py -q` → fails (module missing).
- [ ] **Step 3:** Implement `mbpe/labels/limit_orders.py` per the interfaces and algorithm.
- [ ] **Step 4:** `uv run pytest tests/test_limit_orders.py -q` → all pass. Also add `test_matches_label_trades_when_filled_at_open_with_taker_costs`: with a fill on candle 0 and no strict-vs-touch difference in the data, `gross_R` and `funding_R` equal `label_trades` for the same decision.
- [ ] **Step 5:** Commit: `Test B: shared limit-order trade function and book`.

### Task 3: Live executor, hourly wiring, start record

**Files:**
- Create: `mbpe/live/paper_trading_b.py`
- Modify: `mbpe/live/hourly_run.py` (one line after the `step("paper", ...)` call at line 147)
- Modify: `docs/SCOPE.md` (amendment + start record), `docs/RUNBOOK.md` (Files table row for `data/live/paper_b/`)
- Test: `tests/test_paper_trading_b.py`

**Interfaces:**
- Consumes: `limit_order_trade`, `FILL_MINUTES` (Task 2); `decision_atr` from `mbpe.labels.triple_barrier` (ATR exactly as Test A's `_open` computes it).
- Produces: `PAPER_B = Path("data/live/paper_b")`; `step(now: datetime, candles: pl.DataFrame, funding: pl.DataFrame, decision: dict | None, model_version: str, root: Path = PAPER_B) -> list[dict]`. State file `state.json`: `{"pending": {decision_ms, side, atr} | None, "free_ms": int, "seen": [decision_ms, ...last 48]}`. Ledger `ledger.jsonl` events: `order`, `unfilled`, `close`, `skip` (each with `logged_at`, `model_version`), written like Test A's `step` (append only when there are events; atomic state write).

- [ ] **Step 1: Write failing tests:**
  - `test_live_equals_book`: synthetic candles over 12 h and decisions at 6 of the hours. Calling `step` once per hour, with `candles` truncated to `now` (minute 3 of each hour), and then once more at the end yields the same `unfilled`/`close`/`skip` sequence and R values as `run_book` on the same decisions.
  - `test_step_is_idempotent_for_same_decision`: calling `step` twice with the same decision adds one `order` event.
  - `test_no_decision_only_settles`: `decision=None` with a pending order whose candles are complete → one `close` or `unfilled`, no `order`.
- [ ] **Step 2:** `uv run pytest tests/test_paper_trading_b.py -q` → fails.
- [ ] **Step 3:** Implement `step`: settle the pending order with `limit_order_trade` (None → keep pending); then, if `decision["decision"] in ("long", "short")` and its `decision_ms` is not in `seen`: skip if pending or `decision_ms < free_ms`; else compute ATR as Test A does, record `order`, set pending, and try settling at once.
- [ ] **Step 4:** In `run_hourly`, after the Test A paper step: `step("paper_b", lambda: paper_b.step(now, candles, funding, decision, fz.version, root / "paper_b"))` inside the same `if data is not None and fz is not None:` block. Add `from mbpe.live import paper_trading_b as paper_b` to the imports.
- [ ] **Step 5:** `uv run pytest -q` → all pass (Test A's live tests unchanged). Verify Test A untouched: `git diff mbpe/live/paper_trading.py` is empty.
- [ ] **Step 6:** In `docs/SCOPE.md`, add a dated amendment: "2026-10-29: Test B added to the hourly run as a separate `paper_b` step writing to `data/live/paper_b/`; Test A's code, files and behaviour are unchanged." Add a Files row in RUNBOOK. Commit code and docs: `Test B: live executor in the hourly run (Test A unchanged)`.
- [ ] **Step 7 (owner runs nothing; launchd picks up the code at the next hour):** after the next minute-3 run, check `data/logs/live.log` and the newest `predictions.jsonl` record. Its `steps` must show `"paper": "ok"` and `"paper_b": "ok"`, and `data/live/paper_b/state.json` must exist. Record in SCOPE under Test B: `**Start record:** first Test B decision time = <that hour> UTC, code commit <hash>`. Commit: `Test B start record`.

### Task 4: Historical replay (run once)

**Files:**
- Create: `mbpe/research/test_b_replay.py`
- Modify: `mbpe/reporting/generated_tables.py` (new `@block("test_b_replay")`)
- Modify: `docs/PROGRESS.md`, `docs/FINDINGS.md` (block markers under a "Test B" heading in Phase 6)
- Output: `reports/test_b/replay.json`
- Test: `tests/test_test_b_replay.py`

**Interfaces:**
- Consumes: `run_book` (Task 2); `load_dataset`, `prepare_arrays` from `mbpe.research.experiment`; `make_folds`, `split` from `mbpe.research.walk_forward`; `cfg_p6`, `META_FIRST_FOLD` from `mbpe.research.phase6_models`; `block_bootstrap_ci` from `mbpe.research.backtest` (block = `bootstrap_block`, n = `bootstrap_samples` from the eval config); candidates `data/features/phase6_candidates.parquet` (`kind == "lgbm"`, `fold >= 4`, `meta_p >= val_thr`; ATR = `atr_1h_pct` × that hour's close, the same as Test A's `decision_atr`; use `decision_atr` on the clean candles to stay exact); `reports/phase6/eval/overall_metrics.csv` for Test A's walk-forward row (lgbm, `primary_metaval`, taker, folds 4–17 period).
- Produces: `replay(out: Path = Path("reports/test_b")) -> dict`, writing `replay.json` with keys `decisions, attempted, filled, fill_rate, test_b{n, mean_R, ci_lo, ci_hi}, taker_same_orders{n, mean_R, ci_lo, ci_hi}, test_a_walk_forward{n, mean_R, ci_lo, ci_hi}, always_long{decisions, filled, mean_R, ci_lo, ci_hi}, run_at, commit`.

- [ ] **Step 1: Write a failing test** `test_replay_on_synthetic_inputs`: monkeypatch the loaders to a 3-day synthetic candle frame, 5 candidate rows and 2 folds. Assert `attempted == decisions - busy skips`, `fill_rate == filled / attempted`, and that `taker_same_orders.n == filled` uses the candidates' `net_R_taker` on exactly the filled orders' rows.
- [ ] **Step 2:** `uv run pytest tests/test_test_b_replay.py -q` → fails.
- [ ] **Step 3:** Implement `replay`.
  - Test B: `run_book` over the filtered candidates in time order.
  - always-long: `run_book` over every hour in the folds 4–17 test windows where `prepare_arrays(...)`'s `eligible` is true, all with side +1.
  - `__main__` runs `replay()` and prints the JSON.
- [ ] **Step 4:** Add `@block("test_b_replay")`, a two-column table like `phase6_c_headline`, with rows:
  - decisions / attempted / filled (fill rate);
  - Test B mean net R [95% CI];
  - taker on the same orders;
  - Test A walk-forward;
  - always-long (limit orders).
  
  Use `fmt`. Insert the empty markers `<!-- generated:test_b_replay -->` / `<!-- /generated:test_b_replay -->` under a new "### Test B: limit orders (historical replay, development evidence)" heading in PROGRESS and FINDINGS.
- [ ] **Step 5:** `uv run pytest -q` passes. Commit the code: `Test B: historical replay code and table`.
- [ ] **Step 6 (run once):** `uv run python -m mbpe.research.test_b_replay`, then `uv run python -m mbpe.reporting.generated_tables` (writes the blocks), then `--check` → matches. Add a FINDINGS sentence under the table without numbers: development evidence only, spent data, rules unchanged after it. Commit results: `reports/test_b/replay.json`, PROGRESS, FINDINGS: `Test B: historical replay results (run once)`.
