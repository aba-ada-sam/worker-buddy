"""Offline tests for run_desktop_task: approval gate and screenshot elision."""
import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _n in ("anthropic", "mss", "pyautogui"):
    try:
        __import__(_n)
    except ImportError:
        _m = types.ModuleType(_n)
        if _n == "anthropic":
            _m.APIError = Exception
            _m.Anthropic = object
        sys.modules[_n] = _m
try:
    from PIL import Image  # noqa: F401
except ImportError:
    _pil = types.ModuleType("PIL")
    _pil.Image = types.ModuleType("PIL.Image")
    sys.modules["PIL"], sys.modules["PIL.Image"] = _pil, _pil.Image

from modes import desktop_mode as dm  # noqa: E402


def _tool_use(i, **inp):
    return NS(type="tool_use", id=f"tu{i}", name="computer", input=inp)


def _resp(*blocks):
    return NS(content=list(blocks), usage=None)


def test_declined_dangerous_type_is_not_executed():
    typed_calls = []
    responses = [
        _resp(_tool_use(1, action="type", text="please delete everything")),
        NS(content=[NS(type="text", text="ok done")], usage=None),
    ]
    with mock.patch.object(dm, "type_text", side_effect=lambda t: typed_calls.append(t) or {"ok": True}):
        client = mock.MagicMock()
        client.messages.create.side_effect = responses
        with mock.patch.object(dm.anthropic, "Anthropic", return_value=client, create=True), \
             mock.patch.object(dm, "get_screen_info", return_value=NS(width=1000, height=800)), \
             mock.patch.object(dm, "get_model_display_size", return_value=(1000, 800, 1.0)), \
             mock.patch.object(dm.time, "sleep"):
            asked = []
            out = dm.run_desktop_task(
                task="t", api_key="k", danger_words=("delete",), log_fn=lambda s: None,
                approval_callback=lambda msg: asked.append(msg) or False)
    assert typed_calls == []
    assert len(asked) == 1 and "delete" in asked[0]
    assert out == "ok done"


def test_approved_dangerous_type_is_executed():
    typed_calls = []
    responses = [
        _resp(_tool_use(1, action="type", text="delete me")),
        NS(content=[NS(type="text", text="fin")], usage=None),
    ]
    with mock.patch.object(dm, "type_text", side_effect=lambda t: typed_calls.append(t) or {"ok": True}):
        client = mock.MagicMock()
        client.messages.create.side_effect = responses
        with mock.patch.object(dm.anthropic, "Anthropic", return_value=client, create=True), \
             mock.patch.object(dm, "get_screen_info", return_value=NS(width=1000, height=800)), \
             mock.patch.object(dm, "get_model_display_size", return_value=(1000, 800, 1.0)), \
             mock.patch.object(dm.time, "sleep"):
            dm.run_desktop_task(task="t", api_key="k", danger_words=("delete",),
                                log_fn=lambda s: None, approval_callback=lambda m: True)
    assert typed_calls == ["delete me"]


def _shot_msg(i):
    return {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": f"t{i}",
        "content": [{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]}]}


def test_elide_keeps_only_newest_screenshots():
    msgs = [_shot_msg(i) for i in range(6)]
    dm._elide_old_screenshots(msgs, keep=2)
    kinds = [m["content"][0]["content"][0]["type"] for m in msgs]
    assert kinds == ["text"] * 4 + ["image"] * 2


def test_elide_ignores_plain_string_and_non_image_blocks():
    msgs = [{"role": "user", "content": "hello"},
            {"role": "user", "content": [{"type": "tool_result", "content": [{"type": "text", "text": "x"}]}]},
            {"role": "assistant", "content": [object()]}]
    dm._elide_old_screenshots(msgs, keep=0)   # must not raise
    assert msgs[0]["content"] == "hello"
