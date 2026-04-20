"""Worker QThread that runs one task in either browser or desktop mode.

Replaces the v1 single-mode thread. Mode is selected by the caller and
stays put for the duration of the task. Stop is cooperative: we set a
flag and let the mode loop notice on its next checkpoint -- no more
QThread.terminate() (which can leave a half-running browser or a wedged
async loop behind).
"""

from __future__ import annotations

import threading

from PyQt5.QtCore import QThread, pyqtSignal

from usage import TaskUsage


class AgentThread(QThread):
    log_line = pyqtSignal(str)
    finished = pyqtSignal(str)               # "done" | "stopped" | "error: ..."
    usage_ready = pyqtSignal(object)         # emits the final TaskUsage on success
    # Approval request: (message_text, response_event_setter). The setter is a
    # plain callable the main thread invokes with True/False. Worker thread
    # blocks on the matching threading.Event until the answer arrives.
    approval_request = pyqtSignal(str, object)

    def __init__(
        self,
        task: str,
        api_key: str,
        *,
        mode: str = "browser",          # "browser" | "desktop"
        model: str = "claude-sonnet-4-5-20250929",
        show_browser: bool = True,
        max_steps: int = 60,
        approvals_enabled: bool = True,
        danger_words: tuple = (),       # passed through to desktop_mode
        browser_user_data_dir: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.task = task
        self.api_key = api_key
        self.mode = mode
        self.model = model
        self.show_browser = show_browser
        self.max_steps = max_steps
        self.approvals_enabled = approvals_enabled
        self.danger_words = danger_words
        self.browser_user_data_dir = browser_user_data_dir
        self._stop_requested = False
        self._usage = TaskUsage()

    # Cooperative stop. The mode loops poll this between actions/steps.
    def request_stop(self) -> None:
        self._stop_requested = True

    def _is_stopped(self) -> bool:
        return self._stop_requested

    def _ask_for_approval(self, message: str) -> bool:
        """Block the worker thread until the main thread answers the dialog.
        Called from inside desktop_mode's tool loop via the approval_callback."""
        evt = threading.Event()
        result = {"ok": False}
        def setter(answer: bool) -> None:
            result["ok"] = bool(answer)
            evt.set()
        # Emit -- queued connection delivers `setter` to the main thread, where
        # the slot pops a QMessageBox and calls setter(True/False).
        self.approval_request.emit(message, setter)
        # Hard cap so a forgotten dialog doesn't wedge us forever. 5 min is
        # generous enough for thoughtful approval; treats timeout as decline.
        evt.wait(timeout=300)
        return result["ok"]

    def run(self) -> None:
        log = self.log_line.emit
        approval_cb = self._ask_for_approval if self.approvals_enabled else None
        try:
            if self.mode == "desktop":
                from modes.desktop_mode import run_desktop_task, DEFAULT_DANGER_WORDS
                run_desktop_task(
                    task=self.task,
                    api_key=self.api_key,
                    model=self.model,
                    max_steps=self.max_steps,
                    log_fn=log,
                    is_stopped=self._is_stopped,
                    usage_tracker=self._usage,
                    approval_callback=approval_cb,
                    danger_words=self.danger_words or DEFAULT_DANGER_WORDS,
                )
            else:
                from modes.browser_mode import run_browser_task
                run_browser_task(
                    task=self.task,
                    api_key=self.api_key,
                    model=self.model,
                    show_browser=self.show_browser,
                    log_fn=log,
                    is_stopped=self._is_stopped,
                    usage_tracker=self._usage,
                    user_data_dir=self.browser_user_data_dir,
                )
        except Exception as e:
            # Agent errors (API 4xx/5xx, network hiccups, tool crashes) bubble
            # up as exceptions. A stop mid-flight may also raise, so prefer
            # "stopped" classification when the user asked for it.
            if self._stop_requested:
                self.finished.emit("stopped")
            else:
                self.finished.emit(f"error: {e}")
            return

        # Always surface usage, even on cooperative stop.
        self.usage_ready.emit(self._usage)
        if self._stop_requested:
            self.finished.emit("stopped")
        else:
            self.finished.emit("done")
