"""Worker Buddy as an MCP server.

Exposes the desktop primitives (screenshot, click, type, key, scroll, find
window) and the browser_use higher-level task runner as MCP tools, so any
MCP client -- Claude Desktop, Claude Code, Cursor, etc. -- can drive the
machine through Worker Buddy without going through the chat UI.

Run with stdio transport (default for MCP clients):
    python mcp_server.py

The MCP client config (e.g. Claude Desktop's claude_desktop_config.json)
points at this script via its venv python:

    {
      "mcpServers": {
        "worker-buddy": {
          "command": "C:\\\\WorkerBuddy\\\\venv\\\\Scripts\\\\python.exe",
          "args": ["C:\\\\dev\\\\worker-buddy\\\\mcp_server.py"]
        }
      }
    }
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

import desktop_tools as dt

# Logs go to a file so they don't pollute the MCP stdio channel (anything on
# stdout would corrupt the JSON-RPC stream the client is reading).
_LOG_PATH = Path(__file__).resolve().parent / "logs" / "mcp_server.log"
_LOG_PATH.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    filename=str(_LOG_PATH),
)
log = logging.getLogger("worker_buddy.mcp")

mcp = FastMCP("worker-buddy")


# ── Desktop primitives ────────────────────────────────────────────────────────

@mcp.tool()
def screenshot() -> Image:
    """Capture the primary monitor and return a PNG image.

    The image is downscaled to <=1568px on the longest edge so it fits cleanly
    in a vision request. Use this whenever you need to see what's on screen
    before deciding what to click or type.
    """
    res = dt.take_screenshot()
    raw = base64.b64decode(res["image_b64"])
    return Image(data=raw, format="png")


@mcp.tool()
def screen_size() -> dict:
    """Return the primary monitor's resolution as {"width": int, "height": int}."""
    info = dt.get_screen_info()
    return {"width": info.width, "height": info.height}


@mcp.tool()
def click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    """Click at (x, y) in screen pixels.

    Args:
        x, y:    target coordinates (0,0 is top-left)
        button:  "left" | "right" | "middle"
        clicks:  1 for single, 2 for double, 3 for triple
    """
    return dt.click(x, y, button=button, clicks=clicks)


@mcp.tool()
def double_click(x: int, y: int) -> dict:
    """Double-click at (x, y)."""
    return dt.double_click(x, y)


@mcp.tool()
def right_click(x: int, y: int) -> dict:
    """Right-click at (x, y) (e.g. to open a context menu)."""
    return dt.right_click(x, y)


@mcp.tool()
def move_mouse(x: int, y: int) -> dict:
    """Move the mouse cursor to (x, y) without clicking."""
    return dt.mouse_move(x, y)


@mcp.tool()
def drag(x1: int, y1: int, x2: int, y2: int) -> dict:
    """Left-button drag from (x1,y1) to (x2,y2)."""
    return dt.left_click_drag(x1, y1, x2, y2)


@mcp.tool()
def type_text(text: str) -> dict:
    """Type literal text at the current focus. For non-printable keys, use press_key."""
    return dt.type_text(text)


@mcp.tool()
def press_key(key: str) -> dict:
    """Press a key or key combo. Examples: "Return", "Tab", "Escape", "ctrl+s", "win+r"."""
    return dt.press_key(key)


@mcp.tool()
def scroll(x: int, y: int, direction: str = "down", clicks: int = 3) -> dict:
    """Scroll at (x, y). direction: "up" | "down" | "left" | "right"."""
    return dt.scroll(x, y, direction=direction, clicks=clicks)


@mcp.tool()
def cursor_position() -> dict:
    """Return current mouse cursor position as {"x": int, "y": int}."""
    return dt.cursor_position()


@mcp.tool()
def wait(seconds: float) -> dict:
    """Sleep for `seconds` seconds. Use sparingly; prefer screenshots to confirm UI state."""
    return dt.wait(seconds)


@mcp.tool()
def list_windows() -> dict:
    """List visible top-level Windows window titles. Useful for orienting before clicking."""
    return dt.list_windows()


@mcp.tool()
def focus_window(title_substring: str) -> dict:
    """Bring a top-level window to the foreground by case-insensitive title substring."""
    return dt.find_window(title_substring, focus=True)


# ── Higher-level: hand off a whole web task to browser-use ────────────────────
#
# Browser tasks can take minutes. If we ran them synchronously inside the MCP
# tool call, the client would sit blocked (and couldn't call any other tool
# on this server) until the browser finished. Instead we spawn the task in a
# background thread, hand back a job_id immediately, and expose status/result
# tools the client can poll.

_jobs_lock = threading.Lock()
# job_id -> {"status": "running"|"done"|"error"|"stopped",
#            "result": str, "error": str, "log": list[str],
#            "stop_flag": bool, "thread": Thread, "started": float}
_jobs: dict[str, dict] = {}
_MAX_JOBS_RETAINED = 50
_MAX_LOG_LINES = 120


def _record(job_id: str, **updates) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.update(updates)


def _job_log(job_id: str, line: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        lines = job.setdefault("log", [])
        lines.append(line)
        # Cap the log so a runaway agent can't balloon memory.
        if len(lines) > _MAX_LOG_LINES:
            del lines[: len(lines) - _MAX_LOG_LINES]
    log.info("[browser %s] %s", job_id[:8], line)


def _evict_old_jobs() -> None:
    """Keep the jobs dict bounded. Drop finished entries older than the newest N."""
    with _jobs_lock:
        if len(_jobs) <= _MAX_JOBS_RETAINED:
            return
        # Sort by start time, keep the most-recent N.
        ordered = sorted(_jobs.items(), key=lambda kv: kv[1].get("started", 0.0), reverse=True)
        keep = dict(ordered[:_MAX_JOBS_RETAINED])
        _jobs.clear()
        _jobs.update(keep)


@mcp.tool()
def run_browser_task(task: str, model: str = "claude-sonnet-4-5-20250929", show_browser: bool = True) -> dict:
    """Start a browser-use agent in the background and return a job_id.

    Browser tasks can take minutes (login flows, multi-page navigation), so
    this returns immediately with a job_id. Poll `browser_task_status(job_id)`
    until status is "done" / "error" / "stopped", then call
    `browser_task_result(job_id)` to retrieve the final answer and log.

    Args:
        task:         plain-English description of the web task
        model:        Claude model to use (default sonnet-4-5)
        show_browser: False to run Chromium headless

    Returns:
        {"ok": True, "job_id": "..."} on success
        {"ok": False, "error": "..."} if we can't start (no API key, etc.)

    The Anthropic key is read from ANTHROPIC_API_KEY or
    C:\\JSON Credentials\\QB_WC_credentials.json.
    """
    api_key = _load_anthropic_key()
    if not api_key:
        return {"ok": False, "error": "No Anthropic key (set ANTHROPIC_API_KEY or QB_WC_credentials.json)"}

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "result": None,
            "error": None,
            "log": [],
            "stop_flag": False,
            "started": time.time(),
            "task": task,
        }

    def _runner():
        try:
            from modes.browser_mode import run_browser_task as _run
            result = _run(
                task=task, api_key=api_key, model=model, show_browser=show_browser,
                log_fn=lambda line: _job_log(job_id, line),
                is_stopped=lambda: _jobs.get(job_id, {}).get("stop_flag", False),
            )
            final_status = "stopped" if _jobs.get(job_id, {}).get("stop_flag") else "done"
            _record(job_id, status=final_status, result=result)
        except Exception as e:
            log.exception("browser job %s failed", job_id)
            _record(job_id, status="error", error=str(e))

    t = threading.Thread(target=_runner, name=f"browser-job-{job_id[:8]}", daemon=True)
    _record(job_id, thread=t)
    t.start()
    _evict_old_jobs()
    return {"ok": True, "job_id": job_id}


@mcp.tool()
def browser_task_status(job_id: str) -> dict:
    """Check a background browser job's status.

    Returns {"ok": True, "status": "running"|"done"|"error"|"stopped",
             "elapsed_s": float, "log_tail": list[str]}.
    Unknown job_ids come back with {"ok": False, "error": "..."}.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": f"unknown job_id: {job_id!r}"}
        return {
            "ok": True,
            "status": job["status"],
            "elapsed_s": round(time.time() - job.get("started", time.time()), 2),
            "log_tail": list(job.get("log", []))[-12:],
        }


@mcp.tool()
def browser_task_result(job_id: str) -> dict:
    """Fetch the final result of a completed browser job.

    Only meaningful when status != "running". The full log is returned so
    the caller can inspect what happened; for long-running tasks this can be
    sizeable but is capped at the last ~120 lines.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": f"unknown job_id: {job_id!r}"}
        return {
            "ok": job["status"] != "error",
            "status": job["status"],
            "result": job.get("result"),
            "error": job.get("error"),
            "log": list(job.get("log", [])),
            "elapsed_s": round(time.time() - job.get("started", time.time()), 2),
        }


@mcp.tool()
def browser_task_stop(job_id: str) -> dict:
    """Cooperatively stop a running browser job.

    Sets a flag the agent polls every ~500ms; the job will wrap up and
    transition to status="stopped". Safe to call on an already-finished job.
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": f"unknown job_id: {job_id!r}"}
        job["stop_flag"] = True
    return {"ok": True, "job_id": job_id, "message": "stop requested"}


def _load_anthropic_key() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    creds = Path(r"C:\JSON Credentials\QB_WC_credentials.json")
    if creds.exists():
        try:
            data = json.loads(creds.read_text(encoding="utf-8"))
            return data.get("anthropic_key", "")
        except Exception as e:
            log.warning("could not read creds: %s", e)
    return ""


if __name__ == "__main__":
    log.info("Worker Buddy MCP server starting (stdio transport)")
    mcp.run()
