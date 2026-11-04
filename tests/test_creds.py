"""Offline tests for creds.load_anthropic_key / safe_int."""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import creds  # noqa: E402

NO_ENV = {"ANTHROPIC_API_KEY": ""}


def _file(d, raw: bytes):
    p = Path(d) / "c.json"
    p.write_bytes(raw)
    return p


def test_bom_credentials_file_is_read():
    # PowerShell 5 `Set-Content -Encoding UTF8` writes a BOM; json.load(open()) failed on it.
    with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", NO_ENV):
        p = _file(d, b'\xef\xbb\xbf{"anthropic_key": " sk-test "}')
        assert creds.load_anthropic_key(p) == "sk-test"


def test_env_var_wins_over_file():
    with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": " envkey "}):
        assert creds.load_anthropic_key(_file(d, b'{"anthropic_key": "filekey"}')) == "envkey"


def test_non_object_json_raises_readable_error():
    with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", NO_ENV):
        try:
            creds.load_anthropic_key(_file(d, b'["x"]'))
        except ValueError as e:
            assert "JSON object" in str(e)
        else:
            raise AssertionError("expected ValueError")


def test_non_string_key_becomes_empty():
    with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", NO_ENV):
        assert creds.load_anthropic_key(_file(d, b'{"anthropic_key": 5}')) == ""


def test_safe_int_handles_junk_and_clamps():
    assert creds.safe_int("abc", 60) == 60
    assert creds.safe_int("", 60) == 60
    assert creds.safe_int(None, 60) == 60
    assert creds.safe_int("12", 60) == 12
    assert creds.safe_int(10**9, 60, 1, 500) == 500
    assert creds.safe_int(-4, 60, 1, 500) == 1
