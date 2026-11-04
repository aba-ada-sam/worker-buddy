"""Anthropic API key lookup shared by the chat UI and the MCP server."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CREDS_PATH = r"C:\JSON Credentials\QB_WC_credentials.json"


def load_anthropic_key(creds_path: str | os.PathLike | None = None) -> str:
    """Return the API key from ANTHROPIC_API_KEY or the credentials JSON.

    Raises ValueError/OSError with a readable message if the file is present
    but unusable. The file is read as utf-8-sig because PowerShell 5 writes a
    BOM with `-Encoding UTF8` (plain json.load then fails with "Unexpected
    UTF-8 BOM"), and a locale default such as cp1252 would corrupt non-ASCII.
    """
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env:
        return env
    path = Path(creds_path or DEFAULT_CREDS_PATH)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("credentials file must contain a JSON object")
    key = data.get("anthropic_key", "")
    return key.strip() if isinstance(key, str) else ""


def safe_int(value, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """int(value) for QSettings-style values that may be '', None or junk."""
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n
