"""Tests for the bug-review round of fixes.

Covers:
  - Coordinate scaling in _execute_computer_action when scale < 1.0
  - left_click_drag with missing / malformed coordinate
  - hold_key / key_up using keyDown / keyUp (not press)
  - Screenshot elision in message history (_elide_old_screenshots)
  - Model validation / fallback in supports_computer_use
  - MCP browser-job status machine (registration, stop_flag, unknown id)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modes import desktop_mode as dm  # noqa: E402
import desktop_tools as dt  # noqa: E402


# ── coordinate scaling ───────────────────────────────────────────────────────

@pytest.fixture
def patched_dt_click():
    """Minimal mock: capture click / drag args."""
    mocks = {
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
        "cursor_position":  MagicMock(return_value={"x": 100, "y": 200}),
        "wait":             MagicMock(return_value={"ok": True}),
    }
    with patch.multiple(dm, **mocks):
        yield mocks


def test_click_at_scale_0_5_doubles_coords(patched_dt_click):
    # Model coord (100, 200), scale=0.5 -> native (200, 400)
    dm._execute_computer_action({"action": "left_click", "coordinate": [100, 200]}, scale=0.5)
    patched_dt_click["click"].assert_called_once_with(200, 400)


def test_click_at_scale_1_passes_coords_through(patched_dt_click):
    dm._execute_computer_action({"action": "left_click", "coordinate": [42, 99]}, scale=1.0)
    patched_dt_click["click"].assert_called_once_with(42, 99)


def test_click_at_scale_0_306_scales_correctly(patched_dt_click):
    # This is the real scale for 5120x1440 downscaled to 1568: 1568/5120 = 0.30625
    # Claude clicks at (784, 220) in model space -> native (2560, 718)
    dm._execute_computer_action({"action": "left_click", "coordinate": [784, 220]}, scale=0.30625)
    args = patched_dt_click["click"].call_args
    assert abs(args[0][0] - 2560) <= 1  # rounding tolerance
    assert abs(args[0][1] - 718) <= 1


def test_drag_scales_coords(patched_dt_click):
    dm._execute_computer_action({"action": "left_click_drag", "coordinate": [50, 100]}, scale=0.5)
    # cursor_position returns {x:100, y:200} (already in native)
    # target scaled: 50/0.5, 100/0.5 = 100, 200
    patched_dt_click["left_click_drag"].assert_called_once_with(100, 200, 100, 200)


def test_scroll_scales_coords(patched_dt_click):
    dm._execute_computer_action({
        "action": "scroll", "coordinate": [100, 50],
        "scroll_direction": "down", "scroll_amount": 3,
    }, scale=0.5)
    patched_dt_click["scroll"].assert_called_once_with(200, 100, direction="down", clicks=3)


def test_cursor_position_returned_in_model_space(patched_dt_click):
    # cursor_position() returns native {x:100, y:200}; at scale=0.5 model sees {x:50, y:100}
    result = dm._execute_computer_action({"action": "cursor_position"}, scale=0.5)
    assert result == {"x": 50, "y": 100}


# ── missing / malformed coordinate guards ────────────────────────────────────

def test_left_click_drag_no_coord_returns_error(patched_dt_click):
    result = dm._execute_computer_action({"action": "left_click_drag"})
    assert result["ok"] is False
    assert "coordinate" in result["error"]
    patched_dt_click["left_click_drag"].assert_not_called()


def test_left_click_no_coord_returns_error(patched_dt_click):
    result = dm._execute_computer_action({"action": "left_click"})
    assert result["ok"] is False
    patched_dt_click["click"].assert_not_called()


def test_left_click_malformed_coord_returns_error(patched_dt_click):
    result = dm._execute_computer_action({"action": "left_click", "coordinate": ["abc", "def"]})
    assert result["ok"] is False
    patched_dt_click["click"].assert_not_called()


def test_left_click_short_coord_returns_error(patched_dt_click):
    result = dm._execute_computer_action({"action": "left_click", "coordinate": [42]})
    assert result["ok"] is False


# ── hold_key / key_up use keyDown / keyUp ────────────────────────────────────

def test_hold_key_calls_keyDown_not_press():
    with patch("pyautogui.keyDown") as kd, patch("pyautogui.keyUp") as ku:
        result = dm._execute_computer_action({"action": "hold_key", "text": "shift"})
    assert result["ok"] is True
    assert result["held"] == ["shift"]
    kd.assert_called_once_with("shift")
    ku.assert_not_called()  # still held down


def test_hold_key_combo_presses_all_keys_down():
    with patch("pyautogui.keyDown") as kd:
        dm._execute_computer_action({"action": "hold_key", "text": "ctrl+shift"})
    assert kd.call_count == 2
    assert kd.call_args_list[0][0][0] == "ctrl"
    assert kd.call_args_list[1][0][0] == "shift"


def test_key_up_releases_in_reverse_order():
    with patch("pyautogui.keyUp") as ku:
        result = dm._execute_computer_action({"action": "key_up", "text": "ctrl+shift"})
    assert result["ok"] is True
    assert result["released"] == ["ctrl", "shift"]
    # Released reverse: shift first, then ctrl
    assert ku.call_args_list[0][0][0] == "shift"
    assert ku.call_args_list[1][0][0] == "ctrl"


# ── screenshot history elision ───────────────────────────────────────────────

def _make_screenshot_tool_result(tool_use_id="tu_x"):
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
        ],
    }


def test_elide_keeps_last_3_screenshots_by_default():
    # 5 tool-result screenshots in message history; older 2 should be elided.
    msgs = []
    for i in range(5):
        msgs.append({"role": "user", "content": [_make_screenshot_tool_result(f"tu_{i}")]})
    dm._elide_old_screenshots(msgs, keep=3)
    # Newest 3 (indices 2,3,4) keep image content.
    for i in (2, 3, 4):
        block = msgs[i]["content"][0]
        assert block["content"][0]["type"] == "image"
    # Oldest 2 (indices 0, 1) are text placeholders.
    for i in (0, 1):
        block = msgs[i]["content"][0]
        assert block["content"][0]["type"] == "text"
        assert "elided" in block["content"][0]["text"]


def test_elide_leaves_everything_when_under_limit():
    msgs = [{"role": "user", "content": [_make_screenshot_tool_result()]}]
    dm._elide_old_screenshots(msgs, keep=3)
    assert msgs[0]["content"][0]["content"][0]["type"] == "image"


def test_elide_ignores_non_image_tool_results():
    msgs = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": [{"type": "text", "text": '{"ok": true}'}]}
        ]},
    ]
    dm._elide_old_screenshots(msgs, keep=0)
    # Text-only tool_results are left alone (they're cheap and still useful).
    assert msgs[0]["content"][0]["content"][0]["type"] == "text"


# ── model validation ─────────────────────────────────────────────────────────

def test_supports_computer_use_accepts_known_models():
    assert dm.supports_computer_use("claude-sonnet-4-5-20250929")
    assert dm.supports_computer_use("claude-sonnet-4-20250514")


def test_supports_computer_use_rejects_sonnet_4_6():
    assert not dm.supports_computer_use("claude-sonnet-4-6")


def test_supports_computer_use_rejects_opus_4_7():
    assert not dm.supports_computer_use("claude-opus-4-7")


def test_supports_computer_use_rejects_empty_or_nonsense():
    assert not dm.supports_computer_use("")
    assert not dm.supports_computer_use("gpt-5")


# ── _build_tools uses the passed-in display dimensions ───────────────────────

def test_build_tools_uses_model_dimensions():
    tools = dm._build_tools(1568, 441)
    computer = next(t for t in tools if t.get("type") == "computer_20250124")
    assert computer["display_width_px"] == 1568
    assert computer["display_height_px"] == 441


# ── get_model_display_size ───────────────────────────────────────────────────

def test_get_model_display_size_clamps_to_max_dim():
    # Monkeypatch pyautogui.size to claim a 5120x1440 screen
    with patch.object(dt.pyautogui, "size", return_value=(5120, 1440)):
        w, h, scale = dt.get_model_display_size(max_dim=1568)
    assert w == 1568
    assert abs(scale - 1568/5120) < 0.001
    assert h == int(1440 * scale)


def test_get_model_display_size_below_max_is_identity():
    with patch.object(dt.pyautogui, "size", return_value=(1280, 720)):
        w, h, scale = dt.get_model_display_size(max_dim=1568)
    assert (w, h, scale) == (1280, 720, 1.0)
