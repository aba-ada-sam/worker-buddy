"""Model/MCP-supplied waits and click counts are bounded."""
import sys
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _n in ("mss", "pyautogui"):
    try:
        __import__(_n)
    except ImportError:
        sys.modules[_n] = types.ModuleType(_n)
try:
    from PIL import Image  # noqa: F401
except ImportError:
    _pil = types.ModuleType("PIL")
    _pil.Image = types.ModuleType("PIL.Image")
    sys.modules["PIL"], sys.modules["PIL.Image"] = _pil, _pil.Image

import desktop_tools as dt  # noqa: E402


def test_wait_is_capped():
    with mock.patch.object(dt.time, "sleep") as sl:
        res = dt.wait(10_000)
    sl.assert_called_once_with(dt.MAX_WAIT_SECONDS)
    assert res["waited"] == dt.MAX_WAIT_SECONDS


def test_wait_normal_value_untouched():
    with mock.patch.object(dt.time, "sleep") as sl:
        assert dt.wait(2.5)["waited"] == 2.5
    sl.assert_called_once_with(2.5)


def test_click_count_is_capped():
    pg = mock.MagicMock()
    with mock.patch.object(dt, "pyautogui", pg), \
         mock.patch.object(dt, "get_screen_info", return_value=dt.ScreenInfo(1920, 1080)):
        res = dt.click(10, 10, clicks=100000)
    assert res["clicks"] == dt.MAX_CLICKS
    assert pg.click.call_args.kwargs["clicks"] == dt.MAX_CLICKS


def test_click_count_floor_is_one():
    pg = mock.MagicMock()
    with mock.patch.object(dt, "pyautogui", pg), \
         mock.patch.object(dt, "get_screen_info", return_value=dt.ScreenInfo(1920, 1080)):
        assert dt.click(1, 1, clicks=0)["clicks"] == 1


def test_scroll_amount_is_capped():
    pg = mock.MagicMock()
    with mock.patch.object(dt, "pyautogui", pg), \
         mock.patch.object(dt, "get_screen_info", return_value=dt.ScreenInfo(1920, 1080)):
        res = dt.scroll(5, 5, direction="down", clicks=10**9)
    assert res["clicks"] == dt.MAX_SCROLL_CLICKS
    pg.scroll.assert_called_once_with(-dt.MAX_SCROLL_CLICKS)
