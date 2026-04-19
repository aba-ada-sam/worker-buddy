# Worker Buddy

A floating, always-on-top chat window that hands a plain-English task to Claude
and lets it drive a real browser **or** the whole Windows desktop on your behalf.
Runs from the system tray.

## What's new in v2

- **Two modes:** `Browser` (the original `browser-use` agent) and **`Desktop`**
  (Anthropic Computer Use loop — Claude can see the screen and click/type in
  any program a human would use).
- **MCP server** — the same desktop primitives are exposed as MCP tools, so
  Claude Desktop / Claude Code / any MCP client can call them as tools.
- Cooperative stop (no more `QThread.terminate()` orphaning the browser).
- Default model bumped to `claude-sonnet-4-5-20250929`.

## Setup

```cmd
pip install -r requirements.txt
playwright install chromium
```

Credentials: put your Anthropic key in
`C:\JSON Credentials\QB_WC_credentials.json` as
`{"anthropic_key": "sk-ant-..."}`. The MCP server also accepts the
`ANTHROPIC_API_KEY` env var.

## Run the chat UI

```cmd
python main.py
```

Or use the launcher (no console window):

```cmd
run.bat
```

## Modes

Click the **chip in the header** (says `BROWSER` or `DESKTOP`) to toggle, or
right-click the tray icon → **Mode**.

| Mode | Best for | What Claude sees |
|---|---|---|
| **Browser** | Multi-step web tasks: log into sites, fill forms, scrape pages | The browser DOM (via `browser-use`) |
| **Desktop** | Anything off the web: Word, Excel, Explorer, native dialogs, image editors | Screenshots of your screen + mouse/keyboard control |

⚠️ Desktop mode literally moves your real mouse and types on your real keyboard.
Slamming the cursor into a screen corner triggers PyAutoGUI's failsafe and
aborts the agent immediately — that's your panic button.

## Run the MCP server

```cmd
python mcp_server.py
```

Or wire it into a client. Example for Claude Desktop
(`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "worker-buddy": {
      "command": "C:\\WorkerBuddy\\venv\\Scripts\\python.exe",
      "args": ["C:\\dev\\worker-buddy\\mcp_server.py"]
    }
  }
}
```

Tools exposed:

| Tool | Purpose |
|---|---|
| `screenshot` | PNG of primary monitor (downscaled to <=1568px) |
| `screen_size` | Width/height in pixels |
| `click`, `double_click`, `right_click` | Mouse clicks at (x, y) |
| `move_mouse`, `drag` | Mouse movement and drag |
| `type_text` | Type literal text at current focus |
| `press_key` | Single key or combo (`Return`, `ctrl+s`, `win+r`, …) |
| `scroll` | Scroll in any direction at a point |
| `cursor_position`, `wait` | Bookkeeping |
| `list_windows`, `focus_window` | Enumerate / activate top-level windows (pywinauto) |
| `run_browser_task` | Hand off a whole web task to `browser-use` and get the final answer |

## Usage

- Type a plain-English task; press Enter to send (Shift+Enter for newline)
- Watch progress in the chat
- **Stop** button (replaces Send while running) cancels cooperatively
- Right-click the tray icon (indigo dot in the system tray) for Mode, Always-on-Top, Opacity, Settings, Update check, Exit

## Layout

```
worker-buddy/
├── main.py                  PyQt5 chat window + tray
├── agent_thread.py          Worker QThread — dispatches by mode
├── settings_dialog.py       Settings UI
├── desktop_tools.py         screenshot / click / type / key / scroll / windows
├── modes/
│   ├── browser_mode.py      browser-use loop (run_browser_task)
│   └── desktop_mode.py      Anthropic Computer Use loop (run_desktop_task)
├── mcp_server.py            MCP server exposing the same tools
├── requirements.txt
├── run.bat                  Launch chat UI silently
└── run_mcp.bat              Launch MCP server (stdio)
```

## Notes

- Window dragging: title bar or empty body area
- Resize: bottom-right grip
- Minimize / close button hides to tray; the agent keeps running
- Logs land in `logs/dropcat.log` (browser/desktop modes) and `logs/mcp_server.log` (MCP)
