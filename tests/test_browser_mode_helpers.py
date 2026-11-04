"""Offline tests for browser_mode helpers that need no browser-use install."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modes import browser_mode as bm  # noqa: E402


class _Action:
    def __init__(self, dumped):
        self._d = dumped

    def model_dump(self, **kw):
        return self._d


def test_step_callback_reports_action_name():
    lines = []
    cb = bm._step_callback(lines.append)
    cb(None, NS(action=[_Action({"click_element": {"index": 3}})]), 2)
    assert lines == ["step 2: click_element"]


def test_step_callback_survives_malformed_output():
    lines = []
    cb = bm._step_callback(lines.append)
    cb(None, None, 1)
    cb(None, NS(action=[]), 2)

    class Boom:
        def model_dump(self, **kw):
            raise RuntimeError("x")
    cb(None, NS(action=[Boom()]), 3)
    assert lines[0] == "step 1" and lines[1] == "step 2"
    assert lines[2].startswith("step 3")


def test_build_browser_profile_returns_none_without_browser_use():
    # browser_use is not installed in the offline test env; must degrade, not raise.
    try:
        import browser_use  # noqa: F401
        return
    except ImportError:
        pass
    assert bm._build_browser_profile(True, user_data_dir="x") is None


def test_heartbeat_stops_promptly_when_event_set():
    async def go():
        stop = asyncio.Event()
        lines = []
        task = asyncio.create_task(bm._heartbeat(stop, lines.append))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        return lines
    assert asyncio.run(go()) == []
