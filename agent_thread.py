"""Worker QThread that runs one task in either browser or desktop mode.

Replaces the v1 single-mode thread. Mode is selected by the caller and
stays put for the duration of the task. Stop is cooperative: we set a
flag and let the mode loop notice on its next checkpoint -- no more
QThread.terminate() (which can leave a half-running browser or a wedged
async loop behind).
"""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal


class AgentThread(QThread):
    log_line = pyqtSignal(str)
    finished = pyqtSignal(str)  # "done" | "stopped" | "error: ..."

    def __init__(
        self,
        task: str,
        api_key: str,
        *,
        mode: str = "browser",          # "browser" | "desktop"
        model: str = "claude-sonnet-4-7",
        show_browser: bool = True,
        max_steps: int = 60,
        parent=None,
    ):
        super().__init__(parent)
        self.task = task
        self.api_key = api_key
        self.mode = mode
        self.model = model
        self.show_browser = show_browser
        self.max_steps = max_steps
        self._stop_requested = False

    # Cooperative stop. The mode loops poll this between actions/steps.
    def request_stop(self) -> None:
        self._stop_requested = True

    def _is_stopped(self) -> bool:
        return self._stop_requested

    def run(self) -> None:
        log = self.log_line.emit
        try:
            if self.mode == "desktop":
                from modes.desktop_mode import run_desktop_task
                result = run_desktop_task(
                    task=self.task,
                    api_key=self.api_key,
                    model=self.model,
                    max_steps=self.max_steps,
                    log_fn=log,
                    is_stopped=self._is_stopped,
                )
            else:
                from modes.browser_mode import run_browser_task
                result = run_browser_task(
                    task=self.task,
                    api_key=self.api_key,
                    model=self.model,
                    show_browser=self.show_browser,
                    log_fn=log,
                    is_stopped=self._is_stopped,
                )
        except Exception as e:
            if self._stop_requested:
                self.finished.emit("stopped")
            else:
                self.finished.emit(f"error: {e}")
            return

        if self._stop_requested or result == "stopped":
            self.finished.emit("stopped")
        elif isinstance(result, str) and result.startswith("error:"):
            self.finished.emit(result)
        else:
            self.finished.emit("done")
