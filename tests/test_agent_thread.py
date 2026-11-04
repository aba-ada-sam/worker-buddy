"""Offline tests for AgentThread.run / _ask_for_approval using a PyQt5 stand-in."""
import sys
import threading
import time
import types
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_agent_thread():
    """Import agent_thread with a minimal fake QThread/pyqtSignal (real PyQt5
    is not needed: only emit() recording is exercised)."""
    class _Signal:
        def __init__(self, *a):
            pass

        def __set_name__(self, owner, name):
            self.name = name

        def __get__(self, obj, owner=None):
            if obj is None:
                return self
            rec = obj.__dict__.setdefault("_emitted", [])
            return types.SimpleNamespace(emit=lambda *args, _n=self.name: rec.append((_n, args)))

    class _QThread:
        def __init__(self, parent=None):
            pass

    qtcore = types.ModuleType("PyQt5.QtCore")
    qtcore.QThread, qtcore.pyqtSignal = _QThread, _Signal
    pyqt = types.ModuleType("PyQt5")
    pyqt.QtCore = qtcore
    with mock.patch.dict(sys.modules, {"PyQt5": pyqt, "PyQt5.QtCore": qtcore}):
        sys.modules.pop("agent_thread", None)
        import agent_thread
        return agent_thread


at = _load_agent_thread()


def _thread(mode="desktop"):
    t = at.AgentThread("task", "key", mode=mode)
    t._emitted = []
    return t


def _fake_desktop(result=None, exc=None, spend=True):
    mod = types.ModuleType("modes.desktop_mode")
    mod.DEFAULT_DANGER_WORDS = ("x",)

    def run_desktop_task(**kw):
        if spend:
            kw["usage_tracker"].add(input_tokens=10, output_tokens=5)
        if exc:
            raise exc
        return result
    mod.run_desktop_task = run_desktop_task
    return mock.patch.dict(sys.modules, {"modes.desktop_mode": mod})


def _names(t):
    return [n for n, _ in t._emitted]


def test_failed_task_still_reports_usage_before_error():
    t = _thread()
    with _fake_desktop(exc=RuntimeError("boom")):
        t.run()
    assert _names(t) == ["usage_ready", "finished"]
    assert t._emitted[-1][1] == ("error: boom",)


def test_failure_without_api_calls_emits_no_usage():
    t = _thread()
    with _fake_desktop(exc=RuntimeError("boom"), spend=False):
        t.run()
    assert _names(t) == ["finished"]


def test_max_steps_exhaustion_is_not_reported_as_done():
    t = _thread()
    with _fake_desktop(result="incomplete: max steps reached"):
        t.run()
    assert t._emitted[-1] == ("finished", ("error: incomplete: max steps reached",))


def test_normal_completion_is_done():
    t = _thread()
    with _fake_desktop(result="All good"):
        t.run()
    assert t._emitted[-1] == ("finished", ("done",))


def test_stop_unblocks_pending_approval_quickly_as_decline():
    t = _thread()
    threading.Timer(0.3, t.request_stop).start()
    start = time.time()
    assert t._ask_for_approval("really?") is False
    assert time.time() - start < 5


def test_approval_answer_is_returned():
    t = _thread()
    answered = []

    def answer():
        while not [e for e in t._emitted if e[0] == "approval_request"]:
            time.sleep(0.01)
        t._emitted[0][1][1](True)
        answered.append(True)
    threading.Thread(target=answer, daemon=True).start()
    assert t._ask_for_approval("ok?") is True
