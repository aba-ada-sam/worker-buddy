@echo off
:: Worker Buddy MCP server — stdio transport.
:: For interactive testing only; MCP clients spawn this themselves with their
:: own stdin/stdout pipes (see README for claude_desktop_config.json snippet).

set "VENV=C:\WorkerBuddy\venv"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo Could not find %PY%
    pause
    exit /b 1
)

"%PY%" "%~dp0mcp_server.py"
