"""The API-key lookup in main.py (_start_agent) and mcp_server.py
(_load_anthropic_key) must resolve to the same key for every credential layout.

Fully offline: PyQt5, pyautogui, mss, PIL, mcp, desktop_tools and agent_thread
are replaced by stubs at import time, so no GUI starts, no input is sent and the
Anthropic API is never called.
"""
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Dummy:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return mock.MagicMock()


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _qt_module(name):
    m = types.ModuleType(name)
    cache = {}

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        return cache.setdefault(attr, type(attr, (_Dummy,), {}))
    m.__getattr__ = __getattr__
    return m


class _FastMCP:
    def __init__(self, *a, **k):
        pass

    def tool(self, *a, **k):
        return lambda f: f


class _Captured:
    keys = []


class _AgentThread:
    def __init__(self, task, api_key, **kw):
        _Captured.keys.append(api_key)
        self.log_line = self.finished = self.usage_ready = self.approval_request = mock.MagicMock()

    def start(self):
        pass


def _stubs():
    pkg = _module("PyQt5")
    pkg.__path__ = []
    mcp = _module("mcp"); mcp.__path__ = []
    mcp_server = _module("mcp.server"); mcp_server.__path__ = []
    return {
        "PyQt5": pkg,
        "PyQt5.QtWidgets": _qt_module("PyQt5.QtWidgets"),
        "PyQt5.QtCore": _qt_module("PyQt5.QtCore"),
        "PyQt5.QtGui": _qt_module("PyQt5.QtGui"),
        "pyautogui": mock.MagicMock(),
        "mss": mock.MagicMock(),
        "PIL": mock.MagicMock(),
        "PIL.Image": mock.MagicMock(),
        "mcp": mcp,
        "mcp.server": mcp_server,
        "mcp.server.fastmcp": _module("mcp.server.fastmcp", FastMCP=_FastMCP, Image=_Dummy),
        "desktop_tools": mock.MagicMock(),
        "agent_thread": _module("agent_thread", AgentThread=_AgentThread),
    }


class _Settings:
    def __init__(self, creds_path):
        self.creds_path = creds_path

    def value(self, key, default=None, type=None):
        if key == "creds_path":
            return self.creds_path
        return default


class _FakeWindow:
    def __init__(self, creds_path):
        self.settings = _Settings(creds_path)
        self.task_input = mock.MagicMock()
        self.task_input.toPlainText.return_value = "do a thing"
        self._pending_files = []
        self._flush_timer = mock.MagicMock()
        self.bubbles = []

    def _bubble(self, kind, text, **kw):
        self.bubbles.append((kind, text))

    def _clear_chips(self): pass
    def _set_running(self): pass
    def _on_agent_finished(self, *a): pass
    def _on_usage_ready(self, *a): pass
    def _on_approval_request(self, *a): pass
    def append_log(self, *a): pass


KEY = "sk-ant-test-0123"
CASES = {
    "plain": b'{"anthropic_key": "%s"}' % KEY.encode(),
    "bom": b'\xef\xbb\xbf{"anthropic_key": "%s"}' % KEY.encode(),
    "trailing_ws": b'{"anthropic_key": "%s"}\r\n\r\n  \n' % KEY.encode(),
    "key_value_padded": b'{"anthropic_key": "%s \\n"}' % KEY.encode(),
}


class KeyCallSiteTests(unittest.TestCase):
    def setUp(self):
        _Captured.keys = []
        patcher = mock.patch.dict(sys.modules, _stubs())
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("main", "mcp_server", "creds"):
            sys.modules.pop(name, None)
        self.addCleanup(lambda: [sys.modules.pop(n, None) for n in ("main", "mcp_server")])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)
        with mock.patch("logging.basicConfig"):
            self.main = importlib.import_module("main")
            self.mcp_server = importlib.import_module("mcp_server")
        self.creds = importlib.import_module("creds")

    def _file(self, raw):
        p = Path(self.tmp.name) / "c.json"
        p.write_bytes(raw)
        return str(p)

    def _from_main(self, path):
        win = _FakeWindow(path)
        _Captured.keys = []
        self.main.MainWindow._start_agent(win)
        return _Captured.keys[-1] if _Captured.keys else None, win.bubbles

    def _from_mcp(self, path):
        with mock.patch.object(self.creds, "DEFAULT_CREDS_PATH", path):
            return self.mcp_server._load_anthropic_key()

    def test_env_var_set_wins_over_file(self):
        path = self._file(CASES["plain"])
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-key-9"}):
            self.assertEqual(self._from_main(path)[0], "env-key-9")
            self.assertEqual(self._from_mcp(path), "env-key-9")

    def test_env_var_wins_even_when_file_missing(self):
        gone = str(Path(self.tmp.name) / "nope.json")
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-key-9"}):
            self.assertEqual(self._from_main(gone)[0], "env-key-9")
            self.assertEqual(self._from_mcp(gone), "env-key-9")

    def test_file_layouts_give_same_key_at_both_call_sites(self):
        for name, raw in CASES.items():
            with self.subTest(name):
                path = self._file(raw)
                key, bubbles = self._from_main(path)
                self.assertEqual(key, KEY, bubbles)
                self.assertEqual(self._from_mcp(path), KEY)

    def test_missing_file_no_key_and_no_agent_started(self):
        gone = str(Path(self.tmp.name) / "nope.json")
        key, bubbles = self._from_main(gone)
        self.assertIsNone(key)
        self.assertTrue(bubbles)
        self.assertEqual(self._from_mcp(gone), "")


class SafeIntTests(unittest.TestCase):
    def test_non_numeric_falls_back_to_default_without_clamping(self):
        import creds
        self.assertEqual(creds.safe_int("abc", 60), 60)
        self.assertEqual(creds.safe_int(None, 60), 60)
        self.assertEqual(creds.safe_int("", 60), 60)
        # No range given: the UI (QSpinBox 5..300) keeps owning the clamping.
        self.assertEqual(creds.safe_int("1000", 60), 1000)
        self.assertEqual(creds.safe_int(2, 60), 2)


if __name__ == "__main__":
    unittest.main()
