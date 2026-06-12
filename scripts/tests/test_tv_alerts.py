"""Tests for scripts/lib/tv_alerts.py (watchlist -> TradingView alerts sync)."""

import importlib.util
import json
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "lib" / "tv_alerts.py"
_spec = importlib.util.spec_from_file_location("tv_alerts", _MODULE_PATH)
ta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ta)


def long_candidate(**overrides):
    c = {
        "ticker": "NVDA",
        "side": "long",
        "pivot": 155.2,
        "worst_entry": 157.5,
        "stop": 151.3,
        "target": 163.7,
        "shares": 380,
    }
    c.update(overrides)
    return c


def short_candidate(**overrides):
    c = {
        "ticker": "NFLX",
        "side": "short",
        "pivot": 245.0,
        "stop": 260.0,
        "target": 215.0,
        "shares": 100,
    }
    c.update(overrides)
    return c


def wl(candidates):
    return {"date": "2026-06-11", "candidates": candidates}


# --------------------------------------------------------------------------- #
# watchlist_to_alert_plan
# --------------------------------------------------------------------------- #
class TestAlertPlan:
    def test_long_candidate_three_alerts(self):
        plan = ta.watchlist_to_alert_plan(wl([long_candidate()]))
        assert len(plan["signals"]) == 1
        sig = plan["signals"][0]
        assert sig["ticker"] == "NVDA"
        assert sig["direction"] == "LONG"
        levels = {a["level"]: a for a in sig["alerts"]}
        assert levels["Trigger"]["price"] == 155.2
        assert levels["Trigger"]["price_condition"] == "Crossing Up"
        assert levels["Stop"]["price"] == 151.3
        assert levels["Stop"]["price_condition"] == "Crossing Down"
        assert levels["T1"]["price"] == 163.7
        assert levels["T1"]["price_condition"] == "Crossing Up"

    def test_short_candidate_mirrored_conditions(self):
        plan = ta.watchlist_to_alert_plan(wl([short_candidate()]))
        sig = plan["signals"][0]
        assert sig["direction"] == "SHORT"
        levels = {a["level"]: a for a in sig["alerts"]}
        assert levels["Trigger"]["price_condition"] == "Crossing Down"
        assert levels["Stop"]["price_condition"] == "Crossing Up"
        assert levels["T1"]["price_condition"] == "Crossing Down"

    def test_messages_have_ticker_prefix_and_wl_tag(self):
        plan = ta.watchlist_to_alert_plan(wl([long_candidate()]))
        for alert in plan["signals"][0]["alerts"]:
            assert alert["message"].startswith("NVDA: ")
            assert ta.WL_TAG in alert["message"]

    def test_candidate_without_stop_is_skipped(self):
        plan = ta.watchlist_to_alert_plan(wl([long_candidate(stop=None)]))
        assert plan["signals"] == []
        assert plan["skipped"][0]["ticker"] == "NVDA"

    def test_candidate_without_target_gets_two_alerts(self):
        plan = ta.watchlist_to_alert_plan(wl([long_candidate(target=None)]))
        levels = [a["level"] for a in plan["signals"][0]["alerts"]]
        assert levels == ["Trigger", "Stop"]

    def test_empty_watchlist(self):
        plan = ta.watchlist_to_alert_plan(wl([]))
        assert plan["signals"] == []


# --------------------------------------------------------------------------- #
# tv_available
# --------------------------------------------------------------------------- #
class TestTvAvailable:
    def test_true_when_cdp_responds(self, monkeypatch):
        class _Resp:
            def read(self):
                return b'{"Browser": "Chrome"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(ta.urllib.request, "urlopen", lambda *a, **k: _Resp())
        assert ta.tv_available() is True

    def test_false_when_connection_refused(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(ta.urllib.request, "urlopen", boom)
        assert ta.tv_available() is False


# --------------------------------------------------------------------------- #
# sync / purge orchestration (node subprocess mocked)
# --------------------------------------------------------------------------- #
class TestSync:
    def _capture_node(self, monkeypatch, responses=None):
        calls = []

        def fake_run_node(script, args, *, project_root, timeout):
            calls.append((script, list(args)))
            if responses:
                return responses.pop(0)
            return {"summary": {"created": 2, "deleted": 1, "kept": 3, "errors": 0}}

        monkeypatch.setattr(ta, "_run_node", fake_run_node)
        return calls

    def test_sync_runs_diff_delete_then_create(self, monkeypatch, tmp_path):
        calls = self._capture_node(monkeypatch)
        state_path = tmp_path / "alerts_state.json"
        res = ta.sync_watchlist_alerts(wl([long_candidate()]), state_path, project_root=tmp_path)
        scripts = [c[0] for c in calls]
        assert scripts == ["delete_alerts.mjs", "create_alerts.mjs"]
        delete_args = calls[0][1]
        assert "--keep-from-plan" in delete_args
        assert ta.WL_TAG in delete_args  # --message-contains [WL]
        # state remembers the alerted tickers
        state = json.loads(state_path.read_text())
        assert state["tickers"] == ["NVDA"]
        assert res["errors"] == 0

    def test_sync_purges_dropped_tickers_first(self, monkeypatch, tmp_path):
        calls = self._capture_node(monkeypatch)
        state_path = tmp_path / "alerts_state.json"
        state_path.write_text(json.dumps({"tickers": ["OLD1", "NVDA", "OLD2"]}))
        ta.sync_watchlist_alerts(wl([long_candidate()]), state_path, project_root=tmp_path)
        assert calls[0][0] == "delete_alerts.mjs"
        purge_args = calls[0][1]
        assert "--tickers" in purge_args
        idx = purge_args.index("--tickers")
        assert purge_args[idx + 1] == "OLD1,OLD2"
        assert "--keep-from-plan" not in purge_args
        assert ta.WL_TAG in purge_args

    def test_sync_empty_watchlist_only_purges(self, monkeypatch, tmp_path):
        calls = self._capture_node(monkeypatch)
        state_path = tmp_path / "alerts_state.json"
        state_path.write_text(json.dumps({"tickers": ["OLD1"]}))
        ta.sync_watchlist_alerts(wl([]), state_path, project_root=tmp_path)
        scripts = [c[0] for c in calls]
        assert scripts == ["delete_alerts.mjs"]  # purge only, nothing to create
        state = json.loads(state_path.read_text())
        assert state["tickers"] == []

    def test_sync_nothing_to_do(self, monkeypatch, tmp_path):
        calls = self._capture_node(monkeypatch)
        res = ta.sync_watchlist_alerts(wl([]), tmp_path / "s.json", project_root=tmp_path)
        assert calls == []
        assert res["created"] == 0 and res["deleted"] == 0

    def test_sync_collects_node_errors(self, monkeypatch, tmp_path):
        responses = [
            {"error": "TradingView Desktop недоступен"},
            {"summary": {"created": 0, "skipped": 0, "errors": 0}},
        ]
        self._capture_node(monkeypatch, responses)
        res = ta.sync_watchlist_alerts(
            wl([long_candidate()]), tmp_path / "s.json", project_root=tmp_path
        )
        assert res["errors"] >= 1
        assert res["error_details"]

    def test_purge_updates_state(self, monkeypatch, tmp_path):
        calls = self._capture_node(monkeypatch)
        state_path = tmp_path / "alerts_state.json"
        state_path.write_text(json.dumps({"tickers": ["NVDA", "AAPL"]}))
        res = ta.purge_watchlist_alerts(["NVDA"], state_path, project_root=tmp_path)
        assert calls[0][0] == "delete_alerts.mjs"
        args = calls[0][1]
        assert "--tickers" in args and "NVDA" in args[args.index("--tickers") + 1]
        assert ta.WL_TAG in args
        state = json.loads(state_path.read_text())
        assert state["tickers"] == ["AAPL"]
        assert res["deleted"] == 1

    def test_purge_empty_is_noop(self, monkeypatch, tmp_path):
        calls = self._capture_node(monkeypatch)
        res = ta.purge_watchlist_alerts([], tmp_path / "s.json", project_root=tmp_path)
        assert calls == []
        assert res["deleted"] == 0

    def test_sync_keeps_dropped_tickers_in_state_on_error(self, monkeypatch, tmp_path):
        """A failed purge must be retried next sync — dropping the ticker from
        state would orphan its [WL] alerts in TradingView forever."""
        responses = [
            {"error": "TradingView wedged mid-run"},  # purge of OLD1 fails
            {"summary": {"deleted": 0, "kept": 3, "errors": 0}},
            {"summary": {"created": 0, "skipped": 3, "errors": 0}},
        ]
        self._capture_node(monkeypatch, responses)
        state_path = tmp_path / "alerts_state.json"
        state_path.write_text(json.dumps({"tickers": ["OLD1", "NVDA"]}))
        res = ta.sync_watchlist_alerts(wl([long_candidate()]), state_path, project_root=tmp_path)
        assert res["errors"] >= 1
        state = json.loads(state_path.read_text())
        assert "OLD1" in state["tickers"]  # not forgotten
        assert "NVDA" in state["tickers"]

    def test_purge_keeps_state_on_error(self, monkeypatch, tmp_path):
        responses = [{"error": "CDP timeout"}]
        self._capture_node(monkeypatch, responses)
        state_path = tmp_path / "alerts_state.json"
        state_path.write_text(json.dumps({"tickers": ["NVDA", "AAPL"]}))
        res = ta.purge_watchlist_alerts(["NVDA"], state_path, project_root=tmp_path)
        assert res["errors"] == 1
        state = json.loads(state_path.read_text())
        assert state["tickers"] == ["NVDA", "AAPL"]  # untouched, retried next time

    def test_not_found_in_ui_accumulated(self, monkeypatch, tmp_path):
        responses = [
            {"summary": {"deleted": 1, "kept": 0, "not_found_in_ui": 2, "errors": 0}},
            {"summary": {"created": 3, "skipped": 0, "errors": 0}},
        ]
        self._capture_node(monkeypatch, responses)
        res = ta.sync_watchlist_alerts(
            wl([long_candidate()]), tmp_path / "s.json", project_root=tmp_path
        )
        assert res["not_found_in_ui"] == 2
