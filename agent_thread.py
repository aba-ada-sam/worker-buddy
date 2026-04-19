import asyncio
from PyQt5.QtCore import QThread, pyqtSignal


class AgentThread(QThread):
    log_line = pyqtSignal(str)
    finished = pyqtSignal(str)

    def __init__(self, task: str, api_key: str, model: str = "claude-sonnet-4-6",
                 show_browser: bool = True, parent=None):
        super().__init__(parent)
        self.task = task
        self.api_key = api_key
        self.model = model
        self.show_browser = show_browser
        self._stop_requested = False
        self._agent = None

    def request_stop(self):
        self._stop_requested = True
        self.finished.emit("stopped")
        self.terminate()

    def run(self):
        async def _heartbeat(stop_event: asyncio.Event):
            phrases = [
                "Waiting for result...",
                "Agent still working...",
                "Processing task...",
                "Almost there...",
            ]
            idx = 0
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(asyncio.shield(stop_event.wait()), timeout=3)
                except asyncio.TimeoutError:
                    pass
                if not stop_event.is_set():
                    self.log_line.emit(phrases[idx % len(phrases)])
                    idx += 1

        async def _run():
            from langchain_anthropic import ChatAnthropic
            from browser_use import Agent

            llm = ChatAnthropic(model=self.model, anthropic_api_key=self.api_key)

            browser = None
            try:
                from browser_use import Browser, BrowserConfig
                browser = Browser(config=BrowserConfig(headless=not self.show_browser))
            except Exception:
                pass

            self.log_line.emit("Agent starting...")

            agent_kwargs = {"task": self.task, "llm": llm}
            if browser:
                agent_kwargs["browser"] = browser

            self._agent = Agent(**agent_kwargs)

            # Try to register a step callback
            stop_heartbeat = asyncio.Event()
            heartbeat_task = None
            step_cb_registered = False

            def _emit_step(step, *args):
                try:
                    action = getattr(step, "action", None) or getattr(step, "result", None)
                    msg = str(action)[:140] if action else str(step)[:140]
                except Exception:
                    msg = str(step)[:140]
                self.log_line.emit(msg)

            for attr in ("register_new_step_callback", "on_step_done", "on_step"):
                if hasattr(self._agent, attr):
                    try:
                        obj = getattr(self._agent, attr)
                        if callable(obj) and attr.startswith("register"):
                            obj(_emit_step)
                        else:
                            setattr(self._agent, attr, _emit_step)
                        step_cb_registered = True
                        break
                    except Exception:
                        pass

            if not step_cb_registered:
                heartbeat_task = asyncio.create_task(_heartbeat(stop_heartbeat))

            try:
                result = await self._agent.run()
            finally:
                stop_heartbeat.set()
                if heartbeat_task:
                    try:
                        await heartbeat_task
                    except Exception:
                        pass
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass

            result_str = str(result)[:200] if result else "Task completed"
            self.log_line.emit(f"✓ Result: {result_str}")

        try:
            asyncio.run(_run())
            if not self._stop_requested:
                self.finished.emit("done")
        except Exception as e:
            if not self._stop_requested:
                self.finished.emit(f"error: {e}")
