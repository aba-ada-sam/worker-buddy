"""Desktop control primitives.

Thin, dependency-light wrappers around mss (screenshots), pyautogui (mouse
and keyboard), and pywinauto (Windows UI inspection / window focus). These
are the building blocks that both the Anthropic Computer Use agent loop
(modes/desktop_mode.py) and the MCP server (mcp_server.py) call into, so
fixing a bug here fixes it everywhere.

All functions return plain dicts so callers can serialize results into
tool-result blocks without further wrapping.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass

import mss
import pyautogui
from PIL import Image

# pyautogui's failsafe (slam mouse to corner -> abort) is on by default. Keep
# it on for safety -- it gives the user an emergency-stop gesture during a
# runaway agent. Lower the sleep between actions so we don't drag the loop.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


@dataclass(frozen=True)
class ScreenInfo:
    width: int
    height: int


def get_screen_info() -> ScreenInfo:
    w, h = pyautogui.size()
    return ScreenInfo(width=int(w), height=int(h))


def take_screenshot(*, max_dim: int = 1568) -> dict:
    """Capture the primary monitor and return a base64 PNG.

    Anthropic's Computer Use guidance recommends keeping screenshots under
    ~1568px on the longest edge so the model isn't paying for tokens it
    can't use. We downscale aggressively when the monitor is large.

    Returns:
        {
          "image_b64": "<base64 PNG>",
          "media_type": "image/png",
          "width":   actual width sent to model,
          "height":  actual height sent to model,
          "scale":   model_pixels / screen_pixels  (so callers can map clicks back),
        }
    """
    with mss.mss() as sct:
        # Monitor 0 is "all monitors stitched"; 1 is the primary.
        mon = sct.monitors[1]
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    src_w, src_h = img.size
    longest = max(src_w, src_h)
    if longest > max_dim:
        scale = max_dim / longest
        new_size = (int(src_w * scale), int(src_h * scale))
        img = img.resize(new_size, Image.LANCZOS)
    else:
        scale = 1.0

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return {
        "image_b64":  base64.b64encode(buf.getvalue()).decode("ascii"),
        "media_type": "image/png",
        "width":      img.size[0],
        "height":     img.size[1],
        "scale":      scale,
    }


def _clamp_to_screen(x: int, y: int) -> tuple[int, int]:
    info = get_screen_info()
    return max(0, min(info.width - 1, int(x))), max(0, min(info.height - 1, int(y)))


def mouse_move(x: int, y: int, *, duration: float = 0.15) -> dict:
    cx, cy = _clamp_to_screen(x, y)
    pyautogui.moveTo(cx, cy, duration=duration)
    return {"ok": True, "x": cx, "y": cy}


def click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> dict:
    cx, cy = _clamp_to_screen(x, y)
    if button not in ("left", "right", "middle"):
        return {"ok": False, "error": f"unknown button: {button!r}"}
    pyautogui.click(cx, cy, clicks=int(clicks), button=button)
    return {"ok": True, "x": cx, "y": cy, "button": button, "clicks": int(clicks)}


def double_click(x: int, y: int) -> dict:
    return click(x, y, clicks=2)


def right_click(x: int, y: int) -> dict:
    return click(x, y, button="right")


def left_click_drag(x1: int, y1: int, x2: int, y2: int, *, duration: float = 0.4) -> dict:
    sx, sy = _clamp_to_screen(x1, y1)
    ex, ey = _clamp_to_screen(x2, y2)
    pyautogui.moveTo(sx, sy)
    pyautogui.dragTo(ex, ey, duration=duration, button="left")
    return {"ok": True, "from": [sx, sy], "to": [ex, ey]}


def type_text(text: str, *, interval: float = 0.01) -> dict:
    """Type literal text. Use press_key() for non-printable keys."""
    pyautogui.typewrite(text, interval=interval)
    return {"ok": True, "chars": len(text)}


# Map Anthropic Computer Use key names to pyautogui key names. Anthropic's
# format follows xdotool/xkeysyms (Return, Tab, ctrl+s); pyautogui uses
# lowercase ('enter', 'tab', 'ctrlleft'). We translate the common ones and
# pass through the rest.
_KEY_MAP = {
    "Return": "enter", "return": "enter", "Enter": "enter",
    "Tab": "tab", "Escape": "esc", "Esc": "esc",
    "BackSpace": "backspace", "Backspace": "backspace", "Delete": "delete",
    "space": "space", "Space": "space",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Home": "home", "End": "end", "Page_Up": "pageup", "Page_Down": "pagedown",
    "Page Up": "pageup", "Page Down": "pagedown",
    "ctrl": "ctrl", "alt": "alt", "shift": "shift", "win": "win", "cmd": "win", "super": "win",
    "Print": "printscreen", "Insert": "insert",
}


def _translate_key_token(token: str) -> str:
    t = token.strip()
    if t in _KEY_MAP:
        return _KEY_MAP[t]
    if len(t) == 1:
        return t.lower()
    return t.lower()


def press_key(key: str) -> dict:
    """Press a key or key combo (e.g. 'Return', 'ctrl+s', 'win+r')."""
    parts = [p for p in key.replace(" ", "").split("+") if p]
    if not parts:
        return {"ok": False, "error": "empty key"}
    mapped = [_translate_key_token(p) for p in parts]
    if len(mapped) == 1:
        pyautogui.press(mapped[0])
    else:
        pyautogui.hotkey(*mapped)
    return {"ok": True, "key": key, "mapped": mapped}


def scroll(x: int, y: int, *, direction: str = "down", clicks: int = 3) -> dict:
    """Scroll at a given point. direction: up | down | left | right."""
    cx, cy = _clamp_to_screen(x, y)
    pyautogui.moveTo(cx, cy)
    n = int(clicks)
    if direction in ("down", "south"):
        pyautogui.scroll(-n)
    elif direction in ("up", "north"):
        pyautogui.scroll(n)
    elif direction in ("left", "west"):
        pyautogui.hscroll(-n) if hasattr(pyautogui, "hscroll") else pyautogui.scroll(0)
    elif direction in ("right", "east"):
        pyautogui.hscroll(n) if hasattr(pyautogui, "hscroll") else pyautogui.scroll(0)
    else:
        return {"ok": False, "error": f"unknown direction: {direction!r}"}
    return {"ok": True, "x": cx, "y": cy, "direction": direction, "clicks": n}


def cursor_position() -> dict:
    x, y = pyautogui.position()
    return {"x": int(x), "y": int(y)}


def wait(seconds: float) -> dict:
    s = max(0.0, float(seconds))
    time.sleep(s)
    return {"ok": True, "waited": s}


def find_window(title_substring: str, *, focus: bool = True) -> dict:
    """Find a top-level window whose title contains the substring (case-insensitive)
    and optionally bring it to the foreground. Windows-only (pywinauto)."""
    try:
        from pywinauto import Desktop
    except Exception as e:
        return {"ok": False, "error": f"pywinauto unavailable: {e}"}

    needle = title_substring.lower()
    matches: list[dict] = []
    try:
        for w in Desktop(backend="uia").windows():
            try:
                title = w.window_text() or ""
            except Exception:
                continue
            if needle in title.lower():
                matches.append({"title": title, "handle": int(w.handle) if hasattr(w, "handle") else None})
                if focus and len(matches) == 1:
                    try:
                        w.set_focus()
                    except Exception:
                        # set_focus can fail on minimized windows; try restore first.
                        try:
                            w.restore(); w.set_focus()
                        except Exception:
                            pass
    except Exception as e:
        return {"ok": False, "error": f"window enumeration failed: {e}"}
    return {"ok": True, "matches": matches[:25], "focused": matches[0]["title"] if (focus and matches) else None}


def list_windows(*, max_results: int = 60) -> dict:
    """List visible top-level window titles. Useful for the agent to orient itself."""
    try:
        from pywinauto import Desktop
    except Exception as e:
        return {"ok": False, "error": f"pywinauto unavailable: {e}"}
    out: list[str] = []
    try:
        for w in Desktop(backend="uia").windows():
            try:
                t = w.window_text()
            except Exception:
                continue
            if t and t.strip():
                out.append(t)
                if len(out) >= max_results:
                    break
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "windows": out}
