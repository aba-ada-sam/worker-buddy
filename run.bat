@echo off
:: Worker Buddy — silent launcher for the chat UI.
:: Uses the project venv directly so PATH order doesn't matter.

set "VENV=C:\WorkerBuddy\venv"
set "PYW=%VENV%\Scripts\pythonw.exe"

if not exist "%PYW%" (
    echo Could not find %PYW%
    echo Run:  python -m venv "%VENV%" ^&^& "%VENV%\Scripts\pip" install -r "%~dp0requirements.txt"
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0main.py"
