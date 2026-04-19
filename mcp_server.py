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

@mcp.tool()
def run_browser_task(task: str, model: str = "claude-sonnet-4-5-20250929", show_browser: bool = True) -> dict:
    """Run a self-contained web task end-to-end via the browser-use agent.

    Use this for multi-step browser work (log into a site, fill a form, scrape
    a page) where you don't want to drive each click from MCP. The browser
    runs in its own Chromium window; final result text is returned.

    The Anthropic key is read from settings:
      1. ANTHROPIC_API_KEY env var, OR
      2. C:\\JSON Credentials\\QB_WC_credentials.json -> "anthropic_key"
    """
    api_key = _load_anthropic_key()
    if not api_key:
        return {"ok": False, "error": "No Anthropic key (set ANTHROPIC_API_KEY or QB_WC_credentials.json)"}

    log_lines: list[str] = []
    def _log(line: str):
        log_lines.append(line)
        log.info("[browser] %s", line)

    try:
        from modes.browser_mode import run_browser_task as _run
        result = _run(
            task=task, api_key=api_key, model=model, show_browser=show_browser,
            log_fn=_log, is_stopped=lambda: False,
        )
        return {"ok": True, "result": result, "log": log_lines[-30:]}
    except Exception as e:
        log.exception("run_browser_task failed")
        return {"ok": False, "error": str(e), "log": log_lines[-30:]}


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
