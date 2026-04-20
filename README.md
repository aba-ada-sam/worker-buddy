# Worker Buddy

A floating, always-on-top chat window that hands a plain-English task to Claude
and lets it drive a real browser **or** the whole Windows desktop on your behalf.
Lives in the system tray.

## What's new in v2

- **Two modes:** `Browser` (web tasks via `browser-use`) and **`Desktop`**
  (Anthropic Computer Use loop — Claude can see the screen and click/type in
  any program).
- **MCP server** — desktop primitives exposed as MCP tools, so Claude
  Desktop / Claude Code / any MCP client can drive your machine through Worker
  Buddy.
- Cooperative stop (no `QThread.terminate()` orphaning the browser or HTTP
  connection).
- **First-run safety modal** the first time you switch to desktop mode.
- Window/taskbar icon wired; `ANTHROPIC_API_KEY` env var works as a fallback.

## Setup

```cmd
pip install -r requirements.txt
playwright install chromium
```

Provide an Anthropic key (one of):

1. `ANTHROPIC_API_KEY` env var (handy for one-off runs / CI), **or**
2. Credentials JSON at `C:\JSON Credentials\QB_WC_credentials.json` shaped as
   `{"anthropic_key": "sk-ant-..."}`. The path is editable in **Settings →
   Credentials file**.

## Run the chat UI

```cmd
python main.py
```

Or use the silent launcher (no console flash):

```cmd
run.bat
```

### Optional: Start Menu shortcut

```cmd
powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1
```

Add `-Autostart` to also drop a shortcut into the Startup folder so Worker
Buddy launches at login. Both shortcuts are per-user and need no admin rights.
To remove, delete the `.lnk` files in `%APPDATA%\Microsoft\Windows\Start
Menu\Programs\` (and `\Startup\` if you used `-Autostart`).

## Modes

Click the **chip in the header** (says `BROWSER` or `DESKTOP`) to toggle, or
right-click the tray icon → **Mode**. The first switch to desktop pops a
one-time safety modal.

| Mode | Best for | What Claude sees |
|---|---|---|
| **Browser** | Multi-step web tasks: log into sites, fill forms, scrape pages | Page DOM via `browser-use` |
| **Desktop** | Anything off the web: Word, Excel, Explorer, native dialogs, image editors | Screenshots + mouse/keyboard control |

⚠️ Desktop mode literally moves your real mouse and types on your real keyboard.
Slamming the cursor into a screen corner triggers PyAutoGUI's failsafe and
aborts the agent immediately — that's your panic button.

### Model compatibility

Anthropic's Computer Use is supported on a specific subset of models. The
settings dropdown labels each one:

| Model | Browser | Desktop |
|---|---|---|
| `claude-sonnet-4-5-20250929` | ✓ | ✓ |
| `claude-sonnet-4-20250514` | ✓ | ✓ |
| `claude-3-7-sonnet-20250219` | ✓ | ✓ |
| `claude-opus-4-7`, `claude-opus-4-6`, `claude-haiku-4-5-20251001` | ✓ | ✗ |

If desktop mode is selected with an incompatible model, Worker Buddy
auto-falls-back to Sonnet 4.5 with a one-line note in the chat.

## Run the MCP server

```cmd
python mcp_server.py     # stdio transport
```

Or wire it into a client. Example for **Claude Desktop**
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

### Tools exposed

**Desktop primitives** (synchronous, return immediately):

| Tool | Purpose |
|---|---|
| `screenshot` | PNG of primary monitor (downscaled to ≤1568px on the longest edge) |
| `screen_size` | Width/height in pixels |
| `click`, `double_click`, `right_click` | Mouse clicks at (x, y) |
| `move_mouse`, `drag` | Mouse movement and drag |
| `type_text` | Type literal text at the current focus |
| `press_key` | Single key or combo: `Return`, `Tab`, `Escape`, `ctrl+s`, `win+r`, … |
| `scroll` | Scroll up/down/left/right at a point |
| `cursor_position`, `wait` | Bookkeeping |
| `list_windows`, `focus_window` | Enumerate / activate top-level windows (pywinauto) |

**Browser tasks** (asynchronous job pattern — browser tasks take seconds to
minutes and would otherwise block the MCP server):

| Tool | Purpose |
|---|---|
| `run_browser_task(task, model, show_browser)` | Start a browser-use agent in the background. Returns `{"ok": true, "job_id": "..."}` immediately. |
| `browser_task_status(job_id)` | Poll: `{"status": "running"\|"done"\|"error"\|"stopped", "elapsed_s": float, "log_tail": [...]}`. |
| `browser_task_result(job_id)` | Once status ≠ running: `{"status", "result", "error", "log", "elapsed_s"}`. |
| `browser_task_stop(job_id)` | Cooperative cancel. Idempotent on finished jobs. |

Typical flow from an MCP client:

```
job = run_browser_task(task="Find the iPhone 17 release date and return the URL")
# poll every second or so
while True:
    s = browser_task_status(job["job_id"])
    if s["status"] != "running": break
    sleep(1)
result = browser_task_result(job["job_id"])
```

### Tips for browser tasks

- Phrase the task as something the agent should **read** or **extract**, not
  just **navigate to**. "Open example.com" gets you "Navigated to
  https://example.com"; "Open example.com and return the H1 heading text"
  gets you what you actually want.
- For multi-step web work, give the agent permission to roam: "Search Google
  for X, open the top result, then read it and summarize."
- The browser runs visible by default. Pass `show_browser=False` for headless
  on CI / unattended jobs.

## Layout

```
worker-buddy/
├── main.py                  PyQt5 chat window + tray + first-run modal
├── agent_thread.py          Worker QThread — dispatches by mode
├── settings_dialog.py       Settings UI
├── desktop_tools.py         screenshot / click / type / key / scroll / windows
├── modes/
│   ├── browser_mode.py      browser-use 0.12 agent (LiteLLM Anthropic backend)
│   └── desktop_mode.py      Anthropic Computer Use loop (computer_20250124)
├── mcp_server.py            FastMCP stdio server
├── tests/                   pytest suite (82 tests)
├── requirements.txt
├── run.bat                  Launch chat UI silently (pythonw)
├── run_mcp.bat              Launch MCP server (stdio)
└── install_shortcut.ps1     Per-user Start Menu / autostart shortcut
```

## Settings reference

Surfaced in the **Settings** dialog (right-click tray icon → Settings...):

| Setting | Default | Notes |
|---|---|---|
| Always on Top | on | Chat window stays above other apps |
| Opacity | 100% | 55–100% |
| Show agent browser window | on | Off = headless Chromium for browser mode |
| Credentials file | `C:\JSON Credentials\QB_WC_credentials.json` | Anthropic key location (env var wins if set) |
| Model | `claude-sonnet-4-5-20250929` | See compatibility table above |
| Desktop mode: max steps | 60 | Runaway guard — Stop button still works |
| GitHub repo for updates | (blank) | `owner/repo` enables the tray "Check for Update" action |

## Notes

- Window dragging: title bar or empty body area
- Resize: bottom-right grip
- Minimize / close button hides to tray; agent keeps running, settings persist
- Logs land in `logs/mcp_server.log` (MCP)
- A one-time `WorkerBuddy3 → WorkerBuddy` QSettings migration runs on first
  launch of v2 to carry over any legacy prefs.
