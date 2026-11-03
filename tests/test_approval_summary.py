"""The approval dialog must show the part of the text that triggered it."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _import_dm():
    # Allow running where the GUI/automation deps are absent (offline review).
    for name in ("anthropic", "mss", "pyautogui"):
        try:
            __import__(name)
        except ImportError:
            m = types.ModuleType(name)
            if name == "anthropic":
                m.APIError = Exception
                m.Anthropic = object
            sys.modules[name] = m
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pil = types.ModuleType("PIL")
        pil.Image = types.ModuleType("PIL.Image")
        sys.modules["PIL"], sys.modules["PIL.Image"] = pil, pil.Image
    from modes import desktop_mode
    return desktop_mode


dm = _import_dm()


def test_danger_word_past_80_chars_is_visible_in_summary():
    text = "a" * 120 + " rm -rf ~ " + "b" * 50
    inp = {"action": "type", "text": text}
    needle = dm._looks_dangerous("type", inp, dm.DEFAULT_DANGER_WORDS)
    assert needle == "rm -rf"
    summary = dm._action_summary(inp, needle)
    assert "rm -rf" in summary
    assert "chars total" in summary


def test_short_text_summary_unchanged():
    s = dm._action_summary({"action": "type", "text": "hi"}, "hi")
    assert s == "type 'hi'"


def test_hold_key_is_gated_like_key():
    assert dm._looks_dangerous("hold_key", {"text": "delete"}, ("delete",)) == "delete"


def test_summary_without_match_still_bounded():
    s = dm._action_summary({"action": "type", "text": "x" * 500})
    assert len(s) < 160
