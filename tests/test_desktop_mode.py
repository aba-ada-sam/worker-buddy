"""Tests for the desktop_mode action dispatcher.

Anthropic API calls are not exercised here -- we test that tool_use blocks
get translated into the right desktop_tools calls, and that screenshot
results get formatted as image content blocks for the model.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch  # patch used by test_focus_window_routes_to_find_window

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modes import desktop_mode as dm  # noqa: E402


# ── _execute_computer_action ─────────────────────────────────────────────────

@pytest.fixture
def patched_dt(monkeypatch):
    """Replace every desktop_tools function the dispatcher might call.

    Returns a dict keyed by function name; each value is the MagicMock so
    tests can assert call args.
    """
    mocks: dict[str, MagicMock] = {
        "take_screenshot":  MagicMock(return_value={
            "image_b64": "iVBORw0KGgo=", "media_type": "image/png",
            "width": 100, "height": 100, "scale": 1.0,
        }),
        "click":            MagicMock(return_value={"ok": True}),
        "right_click":      MagicMock(return_value={"ok": True}),
        "double_click":     MagicMock(return_value={"ok": True}),
        "mouse_move":       MagicMock(return_value={"ok": True}),
        "left_click_drag":  MagicMock(return_value={"ok": True}),
        "type_text":        MagicMock(return_value={"ok": True}),
        "press_key":        MagicMock(return_value={"ok": True}),
        "scroll":           MagicMock(return_value={"ok": True}),
        "cursor_position":  MagicMock(return_value={"x": 7, "y": 8}),
        "wait":             MagicMock(return_value={"ok": True, "waited": 0.5}),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(dm, name, m)
    return mocks


def test_screenshot_action(patched_dt):
    out = dm._execute_computer_action({"action": "screenshot"})
    assert "image_b64" in out
    patched_dt["take_screenshot"].assert_called_once()


def test_left_click_uses_coordinate(patched_dt):
    dm._execute_computer_action({"action": "left_click", "coordinate": [42, 99]})
    patched_dt["click"].assert_called_once_with(42, 99)


def test_right_click(patched_dt):
    dm._execute_computer_action({"action": "right_click", "coordinate": [10, 20]})
    patched_dt["right_click"].assert_called_once_with(10, 20)


def test_middle_click_uses_button_kwarg(patched_dt):
    dm._execute_computer_action({"action": "middle_click", "coordinate": [5, 6]})
    patched_dt["click"].assert_called_once_with(5, 6, button="middle")


def test_double_click(patched_dt):
    dm._execute_computer_action({"action": "double_click", "coordinate": [1, 2]})
    patched_dt["double_click"].assert_called_once_with(1, 2)


def test_triple_click_passes_clicks_kwarg(patched_dt):
    dm._execute_computer_action({"action": "triple_click", "coordinate": [3, 4]})
    patched_dt["click"].assert_called_once_with(3, 4, clicks=3)


def test_drag_starts_from_cursor(patched_dt):
    dm._execute_computer_action({"action": "left_click_drag", "coordinate": [50, 60]})
    # The dispatcher reads cursor_position() and then calls left_click_drag from cursor -> coord
    patched_dt["cursor_position"].assert_called_once()
    patched_dt["left_click_drag"].assert_called_once_with(7, 8, 50, 60)


def test_type_action(patched_dt):
    dm._execute_computer_action({"action": "type", "text": "hello"})
    patched_dt["type_text"].assert_called_once_with("hello")


def test_key_action(patched_dt):
    dm._execute_computer_action({"action": "key", "text": "ctrl+s"})
    patched_dt["press_key"].assert_called_once_with("ctrl+s")


def test_hold_key_falls_back_to_press(patched_dt):
    dm._execute_computer_action({"action": "hold_key", "text": "shift"})
    patched_dt["press_key"].assert_called_once_with("shift")


def test_wait_action(patched_dt):
    dm._execute_computer_action({"action": "wait", "duration": 2.5})
    patched_dt["wait"].assert_called_once_with(2.5)


def test_scroll_action_with_coordinate(patched_dt):
    dm._execute_computer_action({
        "action": "scroll", "coordinate": [100, 200],
        "scroll_direction": "down", "scroll_amount": 5,
    })
    patched_dt["scroll"].assert_called_once_with(100, 200, direction="down", clicks=5)


def test_scroll_action_without_coordinate_uses_cursor(patched_dt):
    dm._execute_computer_action({
        "action": "scroll", "scroll_direction": "up", "scroll_amount": 1,
    })
    patched_dt["cursor_position"].assert_called_once()
    patched_dt["scroll"].assert_called_once_with(7, 8, direction="up", clicks=1)


def test_unknown_action(patched_dt):
    out = dm._execute_computer_action({"action": "summon_demon"})
    assert out["ok"] is False
    assert "unsupported" in out["error"]


# ── tool_result formatting ──────────────────────────────────────────────────

def test_screenshot_result_block_carries_image():
    res = {"image_b64": "Zm9v", "media_type": "image/png"}
    block = dm._result_block_for_screenshot("tu_123", res)
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_123"
    assert block["content"][0]["type"] == "image"
    assert block["content"][0]["source"]["data"] == "Zm9v"


def test_text_result_block_serializes_payload():
    block = dm._result_block_for_text("tu_xyz", {"ok": True, "x": 1, "y": 2})
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_xyz"
    text = block["content"][0]["text"]
    assert "ok" in text and "1" in text and "2" in text


def test_text_result_block_marks_errors():
    block = dm._result_block_for_text("tu_err", {"ok": False, "error": "boom"}, is_error=True)
    assert block.get("is_error") is True


# ── custom tools ─────────────────────────────────────────────────────────────

def test_custom_tool_unknown():
    out = dm._execute_custom_tool("not_a_tool", {})
    assert out["ok"] is False


def test_focus_window_routes_to_find_window():
    with patch("desktop_tools.find_window") as fw:
        fw.return_value = {"ok": True, "matches": [{"title": "Notepad"}]}
        out = dm._execute_custom_tool("focus_window", {"title_substring": "note"})
        fw.assert_called_once_with("note", focus=True)
        assert out["ok"] is True


# ── tools list ───────────────────────────────────────────────────────────────

def test_build_tools_includes_computer_and_custom():
    tools = dm._build_tools()
    types = [t.get("type") or t.get("name") for t in tools]
    assert "computer_20250124" in types
    assert "list_windows" in types
    assert "focus_window" in types
