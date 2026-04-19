"""Tests for desktop_tools.

Pure-logic tests only -- no real mouse/keyboard/screenshot side effects.
The pyautogui calls are monkeypatched. Screenshot generation actually runs
because mss reading the screen is harmless and self-contained.
"""

import base64
import sys
from pathlib import Path

import pytest

# Make the project root importable so `import desktop_tools` works whether
# pytest is invoked from the project root or from within tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import desktop_tools as dt  # noqa: E402


class _MouseRecorder:
    """Drop-in for pyautogui's mouse functions; records calls instead of moving the cursor."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.position_value = (0, 0)
        self.size_value = (1920, 1080)

    def moveTo(self, x, y, duration=0):
        self.calls.append(("moveTo", x, y, duration))

    def click(self, x, y, clicks=1, button="left"):
        self.calls.append(("click", x, y, clicks, button))

    def dragTo(self, x, y, duration=0, button="left"):
        self.calls.append(("dragTo", x, y, duration, button))

    def typewrite(self, text, interval=0):
        self.calls.append(("typewrite", text, interval))

    def press(self, key):
        self.calls.append(("press", key))

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))

    def scroll(self, n):
        self.calls.append(("scroll", n))

    def hscroll(self, n):
        self.calls.append(("hscroll", n))

    def position(self):
        return self.position_value

    def size(self):
        return self.size_value


@pytest.fixture
def fake_mouse(monkeypatch):
    rec = _MouseRecorder()
    for name in ("moveTo", "click", "dragTo", "typewrite", "press", "hotkey",
                 "scroll", "hscroll", "position", "size"):
        monkeypatch.setattr(dt.pyautogui, name, getattr(rec, name))
    return rec


# ── _translate_key_token ─────────────────────────────────────────────────────

@pytest.mark.parametrize("token,expected", [
    ("Return", "enter"),
    ("Enter",  "enter"),
    ("return", "enter"),
    ("Tab",    "tab"),
    ("Escape", "esc"),
    ("Esc",    "esc"),
    ("BackSpace", "backspace"),
    ("Page_Up",   "pageup"),
    ("Page Down", "pagedown"),
    ("ctrl",   "ctrl"),
    ("super",  "win"),
    ("cmd",    "win"),
    ("a",      "a"),
    ("Z",      "z"),
    ("F5",     "f5"),
])
def test_translate_key_token(token, expected):
    assert dt._translate_key_token(token) == expected


# ── press_key ────────────────────────────────────────────────────────────────

def test_press_key_single(fake_mouse):
    res = dt.press_key("Return")
    assert res["ok"] is True
    assert res["mapped"] == ["enter"]
    assert ("press", "enter") in fake_mouse.calls


def test_press_key_combo(fake_mouse):
    res = dt.press_key("ctrl+s")
    assert res["ok"] is True
    assert res["mapped"] == ["ctrl", "s"]
    assert ("hotkey", ("ctrl", "s")) in fake_mouse.calls


def test_press_key_three_part_combo(fake_mouse):
    res = dt.press_key("ctrl+shift+t")
    assert res["mapped"] == ["ctrl", "shift", "t"]
    assert ("hotkey", ("ctrl", "shift", "t")) in fake_mouse.calls


def test_press_key_empty(fake_mouse):
    res = dt.press_key("")
    assert res["ok"] is False
    assert "empty" in res["error"]


def test_press_key_handles_spaces(fake_mouse):
    res = dt.press_key("ctrl + s")
    assert res["mapped"] == ["ctrl", "s"]


# ── click family ─────────────────────────────────────────────────────────────

def test_click_default_left(fake_mouse):
    res = dt.click(100, 200)
    assert res == {"ok": True, "x": 100, "y": 200, "button": "left", "clicks": 1}
    assert fake_mouse.calls[-1] == ("click", 100, 200, 1, "left")


def test_click_clamps_to_screen(fake_mouse):
    fake_mouse.size_value = (1920, 1080)
    res = dt.click(99999, -50)
    assert res["x"] == 1919
    assert res["y"] == 0


def test_click_unknown_button(fake_mouse):
    res = dt.click(10, 10, button="middle_dragon")
    assert res["ok"] is False


def test_double_click(fake_mouse):
    dt.double_click(50, 60)
    assert fake_mouse.calls[-1] == ("click", 50, 60, 2, "left")


def test_right_click(fake_mouse):
    dt.right_click(70, 80)
    assert fake_mouse.calls[-1] == ("click", 70, 80, 1, "right")


def test_drag(fake_mouse):
    res = dt.left_click_drag(10, 20, 30, 40)
    assert res["ok"] is True
    assert ("moveTo", 10, 20, 0) in fake_mouse.calls
    assert ("dragTo", 30, 40, 0.4, "left") in fake_mouse.calls


# ── scroll ───────────────────────────────────────────────────────────────────

def test_scroll_down_is_negative(fake_mouse):
    dt.scroll(100, 100, direction="down", clicks=5)
    assert ("scroll", -5) in fake_mouse.calls


def test_scroll_up_is_positive(fake_mouse):
    dt.scroll(100, 100, direction="up", clicks=2)
    assert ("scroll", 2) in fake_mouse.calls


def test_scroll_right_uses_hscroll(fake_mouse):
    dt.scroll(100, 100, direction="right", clicks=3)
    assert ("hscroll", 3) in fake_mouse.calls


def test_scroll_unknown_direction(fake_mouse):
    res = dt.scroll(0, 0, direction="diagonal")
    assert res["ok"] is False


# ── type_text ────────────────────────────────────────────────────────────────

def test_type_text(fake_mouse):
    res = dt.type_text("hello world")
    assert res == {"ok": True, "chars": 11}
    assert fake_mouse.calls[-1][0] == "typewrite"
    assert fake_mouse.calls[-1][1] == "hello world"


# ── wait ─────────────────────────────────────────────────────────────────────

def test_wait_clamps_negative():
    res = dt.wait(-1.0)
    assert res == {"ok": True, "waited": 0.0}


# ── screenshot (real call -- mss reads the actual screen, harmless) ──────────

def test_take_screenshot_returns_valid_png():
    res = dt.take_screenshot(max_dim=400)
    assert res["media_type"] == "image/png"
    assert res["width"] <= 400 and res["height"] <= 400
    raw = base64.b64decode(res["image_b64"])
    # PNG magic number
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    # Scale should be < 1 because we asked for tiny dim
    assert 0 < res["scale"] <= 1.0


def test_take_screenshot_default_caps_at_1568():
    res = dt.take_screenshot()
    assert max(res["width"], res["height"]) <= 1568
