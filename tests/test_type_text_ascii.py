"""type_text must not report success after pyautogui silently drops characters."""
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


def test_non_ascii_is_refused_and_nothing_typed():
    typed = []
    orig = dt.pyautogui.typewrite
    dt.pyautogui.typewrite = lambda t, interval=0: typed.append(t)
    try:
        res = dt.type_text("José – café")
    finally:
        dt.pyautogui.typewrite = orig
    assert res["ok"] is False and res["chars"] == 0
    assert typed == []


def test_ascii_with_newline_and_tab_is_typed():
    typed = []
    with mock.patch.object(dt.pyautogui, "typewrite", lambda t, interval=0: typed.append(t), create=True):
        res = dt.type_text("a b\n\tc~")
    assert res == {"ok": True, "chars": 7}
    assert typed == ["a b\n\tc~"]


def test_untypable_chars_lists_offenders():
    assert dt.untypable_chars("abc") == []
    assert dt.untypable_chars("aéé☃") == ["é", "☃"]
