# Test B: the same system with limit orders (design)

## Intent
Test A, the forward test running since 2026-10-29 13:00 UTC, answers "did the proposed system work?" It loses about four times its raw signal to trading costs (+3.1 bps gross vs -10.8 bps net per trade, historically). Test B answers one narrower question: **would the same decisions make money if orders paid the lower maker fee, with realistic fills?** It is a second, separately pre-registered paper forward test that runs alongside Test A.

The audience includes investors. The test has to be credible: rules fixed before it starts, nothing that flatters the result, and Test A untouched.

## Goals
- Paper-trade Test A's exact hourly decisions with limit-order execution, in real time. **Test B starts at the first hour after it is wired into the hourly run. Hours before that are never filled in afterwards**, because they'd be computed after their outcomes exist. *(My call, Claude: the investor audience needs it to be truly real-time.)*
- Model fills conservatively: a limit order fills only when the price trades **through** it.
- Report a historical replay of the same rules once, as development evidence, whatever it shows.
- A verdict with the same structure as Test A's.

## Non-goals
- No change to the model, threshold, features, barriers or horizon. **The only change is the order type** (owner's decision).
- No higher threshold or "trade less" variant (owner's decision: one change at a time).
- No real orders and no exchange keys. Paper only.
- Dashboard display of Test B is not part of this work (it belongs to the investor-dashboard project).

## Anti-goals (a failure even if it works)
- Any change to Test A's behaviour, records or files.
- A fill model that assumes every limit order fills (the old `maker_entry_tp` cost scenario does, so it is optimistic and must not be used for Test B).
- Changing Test B's rules after seeing the historical replay or any live result.

## Rules (to be copied into `docs/SCOPE.md` as the pre-registration)
Notation: t = decision time (the hour), L = Test A's entry price = open of the 1m candle starting at t + 1 min, ATR = the same decision ATR as Test A, side = +1 long / -1 short.

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

## Verdict (pre-registered)
- **When:** only after both ≥ 6 months from Test B's first decision **and** ≥ 100 closed (filled) trades.
- **Pass:** mean net R > 0 with the 95% block-bootstrap CI lower bound > 0 (same method as Test A), **and** above the always-long benchmark.
- **Always-long benchmark** *(my call, Claude)*. It is computed from the archive with the same function as Test B:
  - **Hours:** every hour from Test B's start where Test A logged a decision record (so missed, late and stale hours are excluded, as they are for Test B) and the hour is outside the CPI/FOMC/NFP blackout (the model's own eligibility, as in Phase 6's always-long).
  - **Each hour:** a long, with rules 2–9 applied: the same limit-order execution, its own one-position book, and a pending order blocking new ones. Funding is charged as for Test B.
  - It uses Test B's execution, not taker, so that cheaper costs alone can't beat it.
- Also reported, without being part of the pass rule: fill rate, and Test B against Test A on the hours both traded.
- No change to the rules before the verdict. A bug fix is logged as a dated amendment with its effect, as for Test A.

## Historical replay (development evidence, run once)
- **Input** *(my call, Claude)*: every LightGBM walk-forward candidate in folds 4–17 with meta P ≥ that fold's validation threshold (`data/features/phase6_candidates.parquet`), in time order, through Test B's own book (rules 2–9), using the clean 1m futures candles and funding. This mirrors what Test B does live: act on every signal, with its own position rule.
- **Report:**
  - decisions, attempted orders, fill rate;
  - Test B's mean net R with 95% CI;
  - the taker result (Test A's execution) on exactly the orders Test B attempted, which is the like-for-like cost comparison;
  - Test A's actual walk-forward result over folds 4–17, for reference;
  - the always-long benchmark over the same folds, defined as in the verdict.
- Run only after the rules are committed. Reported in `docs/PROGRESS.md` and `docs/FINDINGS.md` as generated tables, whatever it shows. Rules are not changed after it.
- 2021–2026 data is spent, so this is development evidence only. It can't establish an edge.

## Constraints
- Test A's code path, files (`data/live/paper/`, `predictions.jsonl`) and behaviour are unchanged. Test B writes only to `data/live/paper_b/`.
- A failure in Test B's step is logged as its own step failure and never stops or alters Test A.
- Wiring Test B into the hourly run is a live-system change, so it gets a dated SCOPE amendment ("adds Test B; Test A unchanged").
- No comments or docstrings in Python files. Results tables are generated, never hand-typed.
- One shared function computes Test B's trade for both the replay and the live executor, so the two can't drift. A test checks that the live executor, run hour by hour, reproduces the replay function exactly (as for Test A).
- Settlement waits for complete 1m candles and published funding, never guessed (same as Test A).

## Left to the builder
- Module layout and names (suggested: the fill and exit logic beside `mbpe/labels/triple_barrier.py`, the executor beside `mbpe/live/paper_trading.py`).
- Ledger and state formats (follow Test A's `ledger.jsonl` / `state.json`, plus `unfilled` events).
- How the replay is scripted, provided its tables are generated.

## Order of work
*(My call, Claude: the live start comes before the replay, so the 6-month clock starts as early as possible, and the replay can't influence anything live.)*
1. Commit the rules above into `docs/SCOPE.md` as Test B's pre-registration, including the start rule and the benchmark definition.
2. Build the shared trade function and the live executor, with tests:
   - fills, unfilled orders, touch vs trade-through;
   - fill-candle stop, tie rule, costs, funding, window end;
   - live executor = replay function, hour by hour.
3. Wire Test B into the hourly run (amendment), check its first live hour, and record Test B's start record (first decision time, commit) in SCOPE. Commit.
4. Run the historical replay once, generate its tables, and commit code then results separately.
