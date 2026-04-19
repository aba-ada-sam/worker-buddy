"""Browser mode -- delegates to the `browser-use` package.

This is the original Worker Buddy v1 logic, lifted out of agent_thread.py
and parameterised so it can be driven by the new mode-dispatching thread.
The agent runs to completion in one async call; we surface intermediate
steps via a callback when browser-use exposes one, and fall back to a
heartbeat otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

log = logging.getLogger("worker_buddy.browser")


async def _heartbeat(stop_event: asyncio.Event, log_fn: Callable[[str], None]):
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
            log_fn(phrases[idx % len(phrases)])
            idx += 1


def _try_register_step_callback(agent, log_fn: Callable[[str], None]) -> bool:
    """browser-use's step callback API name has shifted across versions; try
    the known variants and report whether one stuck."""

    def _emit(step, *args):
        try:
            action = getattr(step, "action", None) or getattr(step, "result", None)
            msg = str(action)[:140] if action else str(step)[:140]
        except Exception:
            msg = str(step)[:140]
        log_fn(msg)

    for attr in ("register_new_step_callback", "on_step_done", "on_step"):
        if not hasattr(agent, attr):
            continue
        try:
            obj = getattr(agent, attr)
            if callable(obj) and attr.startswith("register"):
                obj(_emit)
            else:
                setattr(agent, attr, _emit)
            return True
        except Exception:
            continue
    return False


def run_browser_task(
    *,
    task: str,
    api_key: str,
    model: str = "claude-sonnet-4-5-20250929",
    show_browser: bool = True,
    log_fn: Callable[[str], None] = print,
    is_stopped: Callable[[], bool] = lambda: False,
) -> str:
    """Run one browser task (sync wrapper around the browser-use Agent).

    Returns the agent's final result string. The caller's stop flag is
    polled via is_stopped(); when True, we shut the browser and exit.
    """

    async def _run() -> str:
        from langchain_anthropic import ChatAnthropic
        from browser_use import Agent

        llm = ChatAnthropic(model=model, anthropic_api_key=api_key)

        browser = None
        try:
            from browser_use import Browser, BrowserConfig
            browser = Browser(config=BrowserConfig(headless=not show_browser))
        except Exception:
            # Older / newer browser-use versions handle Browser construction
            # internally; the Agent will spawn one on demand if we omit it.
            pass

        log_fn("Browser agent starting...")

        agent_kwargs = {"task": task, "llm": llm}
        if browser:
            agent_kwargs["browser"] = browser
        agent = Agent(**agent_kwargs)

        stop_event = asyncio.Event()
        heartbeat_task = None
        if not _try_register_step_callback(agent, log_fn):
            heartbeat_task = asyncio.create_task(_heartbeat(stop_event, log_fn))

        async def _watch_stop():
            while not stop_event.is_set():
                if is_stopped():
                    log_fn("Stop requested -- closing browser.")
                    if browser:
                        try:
                            await browser.close()
                        except Exception:
                            pass
                    return
                await asyncio.sleep(0.5)

        watcher = asyncio.create_task(_watch_stop())

        try:
            result = await agent.run()
        finally:
            stop_event.set()
            if heartbeat_task:
                try:
                    await heartbeat_task
                except Exception:
                    pass
            # Await the cancelled watcher so its CancelledError is consumed
            # here rather than surfacing as a "Task was destroyed but it is
            # pending" warning on the event loop's way out.
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

        result_str = str(result)[:400] if result else "Task completed"
        log_fn(f"Result: {result_str}")
        return result_str

    return asyncio.run(_run())
