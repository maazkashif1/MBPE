import json
from datetime import datetime, timedelta

import pytest

from dashboard import summary

START = summary.FORWARD_START
H = timedelta(hours=1)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def close(hours: int, net: float) -> dict:
    t = START + timedelta(hours=hours)
    return {"event": "close", "exit_time": t.isoformat(), "net_R": net, "net_R_maker_entry_tp": net + 0.05}


def test_trade_stats():
    s = summary.trade_stats([close(4, 1.86), close(8, -1.14), close(12, -1.14), close(16, 0.5)])
    assert (s["closed"], s["wins"], s["losses"]) == (4, 2, 2)
    assert s["win_rate"] == 0.5
    assert s["net_R"] == pytest.approx(0.08)
    assert s["won_R"] == pytest.approx(2.36) and s["lost_R"] == pytest.approx(-2.28)
    assert s["profit_factor"] == pytest.approx(2.36 / 2.28)
    assert s["max_drawdown_R"] == pytest.approx(2.28)
    assert s["net_R_maker"] == pytest.approx(0.28)
    assert [p["equity_R"] for p in s["equity"]] == pytest.approx([1.86, 0.72, -0.42, 0.08])
    empty = summary.trade_stats([])
    assert empty["win_rate"] is None and empty["profit_factor"] is None and empty["max_drawdown_R"] == 0


def test_build_on_a_synthetic_tree(tmp_path):
    live = tmp_path / "live"
    now = START + timedelta(hours=5, minutes=10)
    preds = [
        {"decision_ms": ms(START), "decision": "short", "model_version": "abc", "threshold": 0.3},
        {"decision_ms": ms(START + H), "decision": "none", "reason": "no side > 0"},
        {"decision_ms": ms(START + 2 * H), "decision": "none", "reason": "meta 0.2 < 0.3"},
        {"decision_ms": ms(START + 4 * H), "decision": "none", "reason": "late run", "model_version": None},
    ]
    write_jsonl(live / "predictions.jsonl", preds)
    write_jsonl(live / "paper" / "ledger.jsonl", [{"event": "open"}, close(4, -1.14)])
    pos = {
        "side": "long",
        "decision_time": (START + timedelta(hours=5)).isoformat(),
        "entry_time": (START + timedelta(hours=5)).isoformat(),
        "entry_price": 100.0,
        "tp": 102.0,
        "sl": 99.0,
        "atr": 1.0,
        "meta_p": 0.4,
    }
    (live / "paper" / "state.json").write_text(json.dumps({"open": pos}))
    write_jsonl(live / "alerts.jsonl", [{"at": (now - 30 * H).isoformat()}, {"at": now.isoformat()}])

    out = summary.build(tmp_path, now=now)
    assert out["model"] == {"version": "abc", "threshold": 0.3}
    assert out["trades"]["closed"] == 1 and out["trades"]["losses"] == 1
    d = out["decisions"]
    assert (d["hours_expected"], d["hours_logged"], d["hours_missed"]) == (6, 4, 2)
    assert d["outcomes"] == {"trade": 1, "no_side": 1, "meta_below": 1, "blackout": 0, "stale": 0, "late": 1}
    assert [h["status"] for h in d["recent"]] == ["missed", "none", "missed", "none", "none", "short"]
    assert out["open_position"]["horizon_end"] == (START + timedelta(hours=9)).isoformat()
    assert out["open_position"]["unrealized_gross_R"] is None
    assert len(out["alerts_24h"]) == 1
    assert out["next_run"] == (START + timedelta(hours=6, minutes=3)).isoformat()


def test_unrealized_r_follows_the_side():
    pos = {k: None for k in ("decision_time", "atr", "meta_p", "tp")}
    pos |= {"entry_time": START.isoformat(), "entry_price": 100.0, "sl": 98.0}
    long, short = pos | {"side": "long"}, pos | {"side": "short", "sl": 102.0}
    assert summary.open_position({"open": long}, 101.0)["unrealized_gross_R"] == 0.5
    assert summary.open_position({"open": short}, 101.0)["unrealized_gross_R"] == -0.5
    assert summary.open_position({"open": None}, 101.0) is None
