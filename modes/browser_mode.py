"""Browser mode -- delegates to the `browser-use` package (0.12+ API).

browser-use 0.12 overhauled their top-level API:
- Browser / BrowserConfig were replaced with BrowserProfile + a session the
  Agent spawns on demand. We pass a BrowserProfile for the headless flag.
- LangChain integration is gone; the Agent expects a browser_use.llm.*
  BaseChatModel. We use ChatLiteLLM which supports Anthropic via the
  "anthropic/<model-id>" prefix.
- Step callbacks now use typed fields: register_new_step_callback (sync)
  for per-step events, register_done_callback (async) for completion.

The outer sync signature (run_browser_task) is unchanged so the caller
(agent_thread / MCP server) didn't have to care about the rewrite.
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


def _build_llm(model: str, api_key: str):
    """Return a browser-use BaseChatModel bound to the Anthropic API."""
    from browser_use.llm.litellm import ChatLiteLLM
    # LiteLLM prints a "Give Feedback / Get Help" banner on every request by
    # default. Silence it -- our log_fn already surfaces step-level progress.
    try:
        import litellm
        litellm.suppress_debug_info = True
    except Exception:
        pass
    # LiteLLM expects a provider-prefixed model id ("anthropic/...") so it
    # knows which backend to hit. The model id we get is already the raw
    # Anthropic id (e.g. claude-sonnet-4-5-20250929).
    return ChatLiteLLM(
        model=f"anthropic/{model}",
        api_key=api_key,
        max_tokens=4096,
    )


def _build_browser_profile(show_browser: bool, user_data_dir: str | None = None):
    """Optional browser config. Agent spawns a session from this on demand.

    user_data_dir, if given, points Chromium at a persistent profile folder
    so cookies / logins / extensions survive between tasks. Without it the
    agent gets a fresh incognito-ish profile every run -- useless for
    "check my Gmail" workflows.
    """
    try:
        from browser_use.browser.profile import BrowserProfile
    except Exception:
        return None
    kwargs: dict = {"headless": not show_browser}
    if user_data_dir:
        kwargs["user_data_dir"] = user_data_dir
    try:
        return BrowserProfile(**kwargs)
    except Exception as e:
        log.debug("BrowserProfile(%s) failed: %s", kwargs, e)
        # Try again without user_data_dir if that's what's choking
        if user_data_dir:
            try:
                return BrowserProfile(headless=not show_browser)
            except Exception:
                pass
        return None


def _step_callback(log_fn: Callable[[str], None]):
    """Per-step progress surface to the UI. browser-use 0.12 calls this with
    (BrowserStateSummary, AgentOutput, step_number)."""
    def _emit(state, output, step: int):
        try:
            # AgentOutput has `action` (list of actions) and `current_state`.
            msg = None
            if hasattr(output, "action") and output.action:
                first = output.action[0]
                # Each action is a pydantic model with one field set to a params dict.
                # The field name IS the action name (click_element, input_text, etc.).
                try:
                    dumped = first.model_dump(exclude_none=True, exclude_unset=True)
                    if dumped:
                        act_name = next(iter(dumped.keys()))
                        msg = f"step {step}: {act_name}"
                except Exception:
                    msg = f"step {step}: {str(first)[:120]}"
            if not msg:
                msg = f"step {step}"
            log_fn(msg)
        except Exception:
            log_fn(f"step {step}")
    return _emit


def run_browser_task(
    *,
    task: str,
    api_key: str,
    model: str = "claude-sonnet-4-5-20250929",
    show_browser: bool = True,
    log_fn: Callable[[str], None] = print,
    is_stopped: Callable[[], bool] = lambda: False,
    usage_tracker=None,                   # usage.TaskUsage | None
    user_data_dir: str | None = None,     # browser profile path for persistence
) -> str:
    """Run one browser task (sync wrapper around the browser-use Agent).

    Returns the agent's final result string. The caller's stop flag is
    polled via is_stopped(); when True, the Agent's should_stop callback
    returns True on its next check and the Agent unwinds cleanly.
    """

    async def _run() -> str:
        from browser_use import Agent

        if usage_tracker is not None and not getattr(usage_tracker, "model", ""):
            usage_tracker.model = model

        llm = _build_llm(model, api_key)
        profile = _build_browser_profile(show_browser, user_data_dir=user_data_dir)

        # Register a LiteLLM success callback so we capture token usage from
        # every browser-use API call. LiteLLM doesn't expose this through its
        # ChatLiteLLM wrapper, only through this global callback hook.
        cb = None
        if usage_tracker is not None:
            try:
                import litellm

                def _capture_usage(kwargs, response, start_time, end_time):
                    try:
                        u = getattr(response, "usage", None)
                        if u is None and isinstance(response, dict):
                            u = response.get("usage")
                        if u is None:
                            return
                        # u may be a pydantic model or a dict
                        get = (lambda k: getattr(u, k, None)) if not isinstance(u, dict) else u.get
                        usage_tracker.add(
                            input_tokens=get("prompt_tokens") or 0,
                            output_tokens=get("completion_tokens") or 0,
                            cache_creation_tokens=(
                                (get("cache_creation_input_tokens") or 0)
                                if not isinstance(u, dict)
                                else (u.get("cache_creation_input_tokens") or 0)
                            ),
                            cache_read_tokens=(
                                (get("cache_read_input_tokens") or 0)
                                if not isinstance(u, dict)
                                else (u.get("cache_read_input_tokens") or 0)
                            ),
                        )
                    except Exception:
                        log.debug("usage capture callback failed", exc_info=True)

                cb = _capture_usage
                # success_callback is a list; append rather than overwrite so
                # we don't stomp on whatever browser-use / litellm have wired.
                if not isinstance(litellm.success_callback, list):
                    litellm.success_callback = []
                litellm.success_callback.append(cb)
            except Exception:
                log.debug("could not register litellm success_callback", exc_info=True)

        log_fn(
            f"Browser agent starting (model={model}, headless={not show_browser}"
            f"{', persistent profile' if user_data_dir else ''})..."
        )

        # browser-use 0.12's cooperative-stop hook is an async callback that
        # returns True to stop. We translate the caller's sync is_stopped()
        # here so the worker-thread flag propagates cleanly.
        async def _should_stop() -> bool:
            return bool(is_stopped())

        agent_kwargs = {
            "task": task,
            "llm": llm,
            "register_new_step_callback": _step_callback(log_fn),
            "register_should_stop_callback": _should_stop,
        }
        if profile is not None:
            agent_kwargs["browser_profile"] = profile

        agent = Agent(**agent_kwargs)

        stop_event = asyncio.Event()
        heartbeat_task = asyncio.create_task(_heartbeat(stop_event, log_fn))

        try:
            history = await agent.run()
        finally:
            stop_event.set()
            try:
                await heartbeat_task
            except Exception:
                pass
            # Detach the usage callback so back-to-back tasks don't
            # double-count via stale callbacks left registered.
            if cb is not None:
                try:
                    import litellm
                    if cb in litellm.success_callback:
                        litellm.success_callback.remove(cb)
                except Exception:
                    pass

        # Try increasingly-coarse sources for a human-readable answer.
        # 1. final_result() -- set by browser-use when the agent marks itself done
        # 2. extracted_content() -- joined text of action results (almost always present)
        # 3. str(history) -- last-ditch, truncated, for debugging
        result = None
        if history is not None:
            try:
                result = history.final_result()
            except Exception:
                result = None
            if not result:
                try:
                    extracted = history.extracted_content()
                    if extracted:
                        # extracted_content is a list; keep the last meaningful entry.
                        meaningful = [s for s in extracted if s and s.strip()]
                        if meaningful:
                            result = meaningful[-1]
                except Exception:
                    pass
        if not result:
            result = str(history)[:400] if history else "Task completed"

        result_str = str(result)[:600]
        log_fn(f"Result: {result_str}")
        return result_str

    return asyncio.run(_run())
