"""Tests for the polish-batch additions (logging, cost, browser persistence,
approval gates).

Pure-logic only -- no Qt, no Anthropic, no real browser. The integration
points (cross-thread approval dialog, LiteLLM callback firing on a real
API call) are exercised by the live smoke tests in the polish-batch
commit, not here.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import desktop_tools as dt  # noqa: E402
import usage  # noqa: E402
from modes import desktop_mode as dm  # noqa: E402


# ── usage.py ────────────────────────────────────────────────────────────────

def test_task_usage_starts_zero():
    u = usage.TaskUsage()
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.api_calls == 0
    assert u.cost_usd() == 0.0


def test_task_usage_add_accumulates():
    u = usage.TaskUsage(model="claude-sonnet-4-5-20250929")
    u.add(input_tokens=1000, output_tokens=500)
    u.add(input_tokens=2000, output_tokens=300, cache_read_tokens=400)
    assert u.input_tokens == 3000
    assert u.output_tokens == 800
    assert u.cache_read_tokens == 400
    assert u.api_calls == 2


def test_cost_calculation_sonnet_4_5():
    # Sonnet 4.5: $3/MTok input, $15/MTok output
    u = usage.TaskUsage(model="claude-sonnet-4-5-20250929")
    u.add(input_tokens=1_000_000, output_tokens=1_000_000)
    # Expected: $3 + $15 = $18
    assert abs(u.cost_usd() - 18.0) < 0.001


def test_cost_calculation_opus_4_7():
    # Opus 4.7: $15/MTok input, $75/MTok output
    u = usage.TaskUsage(model="claude-opus-4-7")
    u.add(input_tokens=100_000, output_tokens=10_000)
    expected = (100_000 / 1_000_000) * 15 + (10_000 / 1_000_000) * 75
    assert abs(u.cost_usd() - expected) < 0.001


def test_cost_calculation_includes_cache_pricing():
    # cache_creation more expensive than input; cache_read much cheaper.
    u = usage.TaskUsage(model="claude-sonnet-4-5-20250929")
    u.add(input_tokens=0, output_tokens=0,
          cache_creation_tokens=1_000_000, cache_read_tokens=1_000_000)
    # cache_w $3.75 + cache_r $0.30 = $4.05
    assert abs(u.cost_usd() - 4.05) < 0.001


def test_cost_unknown_model_uses_fallback_pricing():
    u = usage.TaskUsage(model="not-a-real-model")
    u.add(input_tokens=1_000_000, output_tokens=1_000_000)
    # Falls back to Sonnet pricing ($3 + $15 = $18)
    assert abs(u.cost_usd() - 18.0) < 0.001


def test_summary_line_pluralization():
    u = usage.TaskUsage(model="claude-sonnet-4-5-20250929")
    u.add(input_tokens=100, output_tokens=50)
    line = u.summary_line()
    assert "1 call" in line and "1 calls" not in line
    u.add(input_tokens=100, output_tokens=50)
    line2 = u.summary_line()
    assert "2 calls" in line2


def test_summary_line_uses_cents_format_for_small_runs():
    u = usage.TaskUsage(model="claude-sonnet-4-5-20250929")
    u.add(input_tokens=1000, output_tokens=500)
    line = u.summary_line()
    # Cost will be ~$0.0105 -- should show $0.01XX format
    assert line.startswith("Cost: $0.")


# ── desktop_mode danger detection ───────────────────────────────────────────

def test_looks_dangerous_matches_substring():
    triggered = dm._looks_dangerous(
        "type", {"text": "Are you sure you want to DELETE everything?"},
        ("delete", "format"),
    )
    assert triggered == "delete"


def test_looks_dangerous_case_insensitive():
    triggered = dm._looks_dangerous(
        "type", {"text": "FORMAT C:"}, ("delete", "format"),
    )
    assert triggered == "format"


def test_looks_dangerous_no_match():
    triggered = dm._looks_dangerous(
        "type", {"text": "click the blue button"}, ("delete", "format"),
    )
    assert triggered is None


def test_looks_dangerous_only_for_type_and_key_actions():
    # A click action with text="delete" shouldn't trigger -- clicks have no text.
    triggered = dm._looks_dangerous(
        "left_click", {"coordinate": [10, 20], "text": "delete"}, ("delete",),
    )
    assert triggered is None
    # type IS gated:
    assert dm._looks_dangerous("type", {"text": "delete"}, ("delete",)) == "delete"
    # key combo is also gated (e.g. user could send a kill shortcut)
    assert dm._looks_dangerous("key", {"text": "ctrl+shift+delete"}, ("delete",)) == "delete"


def test_default_danger_words_includes_common_destructive_terms():
    expected = {"delete", "format", "send", "transfer", "shutdown"}
    actual = set(dm.DEFAULT_DANGER_WORDS)
    # All expected substrings should appear (allowing for things like "rm -rf"
    # being a single entry that happens to contain "rm").
    for term in expected:
        assert any(term in w.lower() for w in actual), f"missing: {term}"


def test_action_summary_renders_compactly():
    s = dm._action_summary({"action": "left_click", "coordinate": [100, 200]})
    assert "left_click" in s and "(100, 200)" in s


def test_action_summary_truncates_long_text():
    long_text = "x" * 200
    s = dm._action_summary({"action": "type", "text": long_text})
    assert len(s) < 130  # action + repr-quoted truncated text


# ── approval-callback wiring in _execute_computer_action loop ───────────────
# (We can't unit-test the full agent loop without an Anthropic client; we
# verify the dangerous-action detector + a mock approval callback below.)

@pytest.fixture
def patched_dt_for_approval(monkeypatch):
    """Mock desktop_tools functions used by _execute_computer_action."""
    mocks = {
        "type_text":  MagicMock(return_value={"ok": True}),
        "press_key":  MagicMock(return_value={"ok": True}),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(dm, name, m)
    return mocks


def test_execute_action_does_not_call_approval_directly(patched_dt_for_approval):
    """Sanity: _execute_computer_action itself doesn't know about approvals.
    Approval is a layer above, in run_desktop_task's per-tool loop. So a
    direct call should still go through to the underlying primitive."""
    result = dm._execute_computer_action({"action": "type", "text": "delete everything"})
    assert result["ok"] is True
    patched_dt_for_approval["type_text"].assert_called_once_with("delete everything")
