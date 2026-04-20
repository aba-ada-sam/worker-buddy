"""Desktop mode -- Anthropic Computer Use agent loop.

Claude is configured with the `computer_20250124` tool. We feed it the user's
task plus screenshots; it returns tool_use blocks (screenshot / click / type
/ key / scroll / etc.); we execute them via desktop_tools and return
tool_result blocks back. Repeat until Claude stops emitting tool_use.

Designed to be driven from a worker QThread (see agent_thread.py). The
caller owns the stop flag; we check it between each step.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import anthropic

from desktop_tools import (
    click,
    cursor_position,
    double_click,
    get_model_display_size,
    get_screen_info,
    left_click_drag,
    list_windows,
    mouse_move,
    press_key,
    right_click,
    scroll,
    take_screenshot,
    type_text,
    wait,
)

log = logging.getLogger("worker_buddy.desktop")

# How many model->tool round trips we'll allow in one task before bailing out.
# The user can stop at any time via the chat UI; this is a runaway guard.
DEFAULT_MAX_STEPS = 60

# How many screenshot tool_results to keep in the rolling message history.
# Older screenshot content blocks are replaced with a short text placeholder
# so the API payload stays bounded. Recommended by Anthropic's docs.
_KEEP_SCREENSHOTS = 3

# Computer-use beta header. Bump this if/when Anthropic ships a newer spec.
_COMPUTER_USE_BETA = "computer-use-2025-01-24"

# Anthropic's Computer Use tool. Type version is what determines which actions
# Claude knows about; sonnet-4.x supports computer_20250124 with the full set
# (mouse_move, scroll, hold_key, triple_click, etc.).
_TOOL_TYPE = "computer_20250124"
_TOOL_NAME = "computer"

# Models that support the computer_20250124 tool. Verified empirically:
# sonnet-4-6 / opus-4-6 reject it; 4.5-era models accept it. Keep in sync
# with Anthropic's published Computer Use compatibility matrix.
COMPUTER_USE_MODELS = frozenset({
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
})

# Fallback when a user has an incompatible model saved.
_COMPUTER_USE_DEFAULT = "claude-sonnet-4-5-20250929"

# Substring matches that trigger an approval prompt before execution. Lowercased
# match against typed text and key combos. Conservative defaults; the chat UI
# can extend these via QSettings.
DEFAULT_DANGER_WORDS = (
    "delete", "format", "send",  "transfer", "purchase", "pay ",
    "drop table", "rm -rf", "rm /", "shutdown", "/restart",
    "uninstall", "wipe", "factory reset", "wire $", "submit order",
)


class DesktopAgentError(Exception):
    """Wraps an API/tool error with a caller-visible reason."""


def supports_computer_use(model: str) -> bool:
    return model in COMPUTER_USE_MODELS


def _action_summary(tu_input: dict) -> str:
    """Compact human-readable summary of a `computer` tool_use input -- used
    as the message body in the approval dialog and the danger-word scan."""
    action = tu_input.get("action", "?")
    bits = [action]
    if tu_input.get("coordinate"):
        bits.append(f"@ {tuple(tu_input['coordinate'])}")
    text = tu_input.get("text")
    if text is not None:
        bits.append(repr(str(text)[:80]))
    return " ".join(bits)


def _looks_dangerous(action: str, tu_input: dict, danger_words: tuple) -> str | None:
    """Return the matched danger phrase if this action looks destructive,
    else None. Only `type` and `key` actions can carry payloads we'd worry
    about; clicks/scrolls/screenshots are inherently safe to perform.
    """
    if action not in ("type", "key"):
        return None
    text = (tu_input.get("text") or "").lower()
    if not text:
        return None
    for needle in danger_words:
        if needle.lower() in text:
            return needle
    return None

# Optional hand-off tool for window inspection -- gives Claude a way to ask
# "what windows exist" before clicking blindly. Defined as a regular custom
# tool alongside the built-in computer tool.
_LIST_WINDOWS_TOOL = {
    "name": "list_windows",
    "description": (
        "List visible top-level Windows window titles. Use this to orient "
        "yourself before clicking, especially after the user mentions a "
        "specific app by name."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_FOCUS_WINDOW_TOOL = {
    "name": "focus_window",
    "description": (
        "Bring a top-level window to the foreground by a substring match on "
        "its title. Returns the matched titles."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title_substring": {
                "type": "string",
                "description": "Case-insensitive substring of the window title.",
            }
        },
        "required": ["title_substring"],
    },
}


def _build_tools(model_w: int, model_h: int) -> list[dict]:
    """Advertise the tool with the MODEL's coordinate space (== screenshot dims),
    not the native screen. Claude's click coordinates will land in the same
    space we show him; we scale them back up when calling pyautogui."""
    return [
        {
            "type": _TOOL_TYPE,
            "name": _TOOL_NAME,
            "display_width_px": int(model_w),
            "display_height_px": int(model_h),
            "display_number": 1,
        },
        _LIST_WINDOWS_TOOL,
        _FOCUS_WINDOW_TOOL,
    ]


_SYSTEM_PROMPT = """You are Worker Buddy, a careful desktop automation agent running on the user's Windows machine.

You have full mouse and keyboard control via the `computer` tool, plus `list_windows` and `focus_window` for orientation. The screen resolution is given in the tool config.

Operating principles:
- Take a screenshot first if you don't already have a recent view of the screen.
- Prefer keyboard shortcuts over mouse clicks when they're reliable (e.g. Ctrl+S to save).
- After typing or clicking, take a fresh screenshot before assuming the action worked.
- If a UI element you expected isn't visible, scroll or check window focus before guessing.
- When the task is complete, send a short final message (no tool_use) summarizing what you did.
- If you hit something genuinely ambiguous or destructive (overwriting a file, sending a message), pause and explain rather than guessing.

Do not narrate every keystroke. Brief progress notes between actions are fine."""


def _execute_computer_action(action_input: dict, *, scale: float = 1.0) -> dict:
    """Translate a `computer` tool_use input dict into a desktop_tools call.

    Claude's click coordinates are in the MODEL display space (the downscaled
    screenshot we advertised in the tool config). We scale them up by
    1/scale so they land in native screen pixels before pyautogui uses them.
    Cursor-position results travel the other way (divide by 1/scale to go
    back to model space).
    """
    import pyautogui as _pg
    action = action_input.get("action", "")
    coord  = action_input.get("coordinate")  # [x, y] in model space
    text   = action_input.get("text")

    def _native(c):
        """Model-space [x,y] -> native screen [x,y]. Returns None on bad input."""
        if not c or len(c) < 2:
            return None
        try:
            return int(round(c[0] / scale)), int(round(c[1] / scale))
        except (TypeError, ValueError):
            return None

    if action == "screenshot":
        return take_screenshot()

    # Coordinate-based actions. All unified under one _native() guard so a
    # missing/malformed coord returns a clean error instead of IndexError.
    coord_actions = {
        "mouse_move", "left_click", "right_click", "middle_click",
        "double_click", "triple_click", "left_click_drag",
    }
    if action in coord_actions:
        pt = _native(coord)
        if pt is None:
            return {"ok": False, "error": f"{action}: missing or invalid coordinate"}
        x, y = pt
        if action == "mouse_move":     return mouse_move(x, y)
        if action == "left_click":     return click(x, y)
        if action == "right_click":    return right_click(x, y)
        if action == "middle_click":   return click(x, y, button="middle")
        if action == "double_click":   return double_click(x, y)
        if action == "triple_click":   return click(x, y, clicks=3)
        if action == "left_click_drag":
            cur = cursor_position()
            return left_click_drag(cur["x"], cur["y"], x, y)

    if action == "type" and text is not None:
        return type_text(text)
    if action == "key" and text is not None:
        return press_key(text)
    if action == "hold_key" and text is not None:
        # Press the key(s) down and leave them held until `key_up` fires.
        # pyautogui has keyDown / keyUp for exactly this.
        from desktop_tools import _translate_key_token
        parts = [_translate_key_token(p) for p in text.replace(" ", "").split("+") if p]
        try:
            for k in parts:
                _pg.keyDown(k)
            return {"ok": True, "held": parts}
        except Exception as e:
            # Best-effort cleanup so we don't leave a stuck key.
            for k in parts:
                try: _pg.keyUp(k)
                except Exception: pass
            return {"ok": False, "error": f"hold_key failed: {e}"}
    if action == "key_up" and text is not None:
        from desktop_tools import _translate_key_token
        parts = [_translate_key_token(p) for p in text.replace(" ", "").split("+") if p]
        errs = []
        for k in reversed(parts):
            try: _pg.keyUp(k)
            except Exception as e: errs.append(f"{k}:{e}")
        return {"ok": not errs, "released": parts, **({"error": "; ".join(errs)} if errs else {})}
    if action == "cursor_position":
        cp = cursor_position()
        # Return in model space so Claude sees numbers consistent with his coord system.
        return {"x": int(round(cp["x"] * scale)), "y": int(round(cp["y"] * scale))}
    if action == "wait":
        return wait(float(action_input.get("duration", 1.0)))
    if action == "scroll":
        if coord:
            pt = _native(coord)
            if pt is None:
                return {"ok": False, "error": "scroll: invalid coordinate"}
            cx, cy = pt
        else:
            cur = cursor_position(); cx, cy = cur["x"], cur["y"]
        direction = action_input.get("scroll_direction", "down")
        amount = int(action_input.get("scroll_amount", 3))
        return scroll(cx, cy, direction=direction, clicks=amount)
    return {"ok": False, "error": f"unsupported computer action: {action!r}"}


def _execute_custom_tool(name: str, tool_input: dict) -> dict:
    if name == "list_windows":
        return list_windows()
    if name == "focus_window":
        from desktop_tools import find_window
        return find_window(tool_input.get("title_substring", ""), focus=True)
    return {"ok": False, "error": f"unknown tool: {name!r}"}


def _result_block_for_screenshot(tool_use_id: str, result: dict) -> dict:
    """Computer-use screenshot results must be returned as image content blocks."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": result["media_type"],
                    "data": result["image_b64"],
                },
            }
        ],
    }


def _result_block_for_text(tool_use_id: str, payload: dict, *, is_error: bool = False) -> dict:
    import json as _json
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": [{"type": "text", "text": _json.dumps(payload, default=str)[:1500]}],
        **({"is_error": True} if is_error else {}),
    }


def _elide_old_screenshots(messages: list[dict], keep: int = _KEEP_SCREENSHOTS) -> None:
    """Replace all but the most recent `keep` screenshot tool_result blocks
    with a short text placeholder. Mutates `messages` in place.

    Screenshots are the heaviest thing in our conversation history. Without
    trimming, every round trip re-sends every prior screenshot -- 10 steps
    on an ultrawide is ~3 MB per request. Anthropic's computer-use docs
    recommend this exact pattern: keep last N, elide the rest.
    """
    # Walk message-blocks newest-first; count screenshots; rewrite older ones.
    seen = 0
    for msg in reversed(messages):
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            inner = block.get("content")
            if not isinstance(inner, list):
                continue
            has_image = any(
                isinstance(c, dict) and c.get("type") == "image" for c in inner
            )
            if not has_image:
                continue
            seen += 1
            if seen > keep:
                block["content"] = [
                    {"type": "text", "text": "[earlier screenshot elided to save tokens]"}
                ]


def run_desktop_task(
    *,
    task: str,
    api_key: str,
    model: str = _COMPUTER_USE_DEFAULT,
    max_steps: int = DEFAULT_MAX_STEPS,
    log_fn: Callable[[str], None] = print,
    is_stopped: Callable[[], bool] = lambda: False,
    usage_tracker=None,                       # usage.TaskUsage | None
    approval_callback: Callable[[str], bool] | None = None,
    danger_words: tuple = DEFAULT_DANGER_WORDS,
) -> str:
    """Run one desktop task to completion (or until stopped / max_steps).

    Returns the agent's final text reply (or a short error string). All
    intermediate progress goes through `log_fn(line)`.
    """
    # Validate the model against the known computer-use-capable list. If the
    # user has an incompatible saved model (e.g. sonnet-4-6), swap it out with
    # a clear note rather than blowing up with a 400 from Anthropic.
    if not supports_computer_use(model):
        log_fn(f"Model {model!r} doesn't support Computer Use -- falling back to {_COMPUTER_USE_DEFAULT}.")
        model = _COMPUTER_USE_DEFAULT

    if usage_tracker is not None and not getattr(usage_tracker, "model", ""):
        usage_tracker.model = model

    client = anthropic.Anthropic(api_key=api_key)

    # Figure out the coordinate space we'll advertise to Claude (same dims as
    # the downscaled screenshots we send). Clicks come back in this space;
    # we scale them up by 1/scale before calling pyautogui.
    info = get_screen_info()
    model_w, model_h, scale = get_model_display_size()
    tools = _build_tools(model_w, model_h)
    if scale < 1.0:
        log_fn(f"Desktop agent on {info.width}x{info.height}; "
               f"model sees {model_w}x{model_h} (scale={scale:.3f}), model={model}")
    else:
        log_fn(f"Desktop agent on {info.width}x{info.height}, model={model}")

    messages: list[dict] = [{"role": "user", "content": task}]
    final_text = ""

    for step in range(max_steps):
        if is_stopped():
            log_fn("Stopped by user.")
            return "stopped"

        # Before each request, elide older screenshots to keep payload bounded.
        _elide_old_screenshots(messages, keep=_KEEP_SCREENSHOTS)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                tools=tools,
                messages=messages,
                system=_SYSTEM_PROMPT,
                # Computer-use beta. The current SDK auto-sets this when a
                # computer_* tool is present, but we pin it explicitly so we
                # don't silently drift when the SDK ships a newer default.
                extra_headers={"anthropic-beta": _COMPUTER_USE_BETA},
            )
        except anthropic.APIError as e:
            log_fn(f"API error: {e}")
            raise DesktopAgentError(str(e)) from e

        # Roll up token usage so the chat can show a per-task cost summary.
        if usage_tracker is not None and getattr(response, "usage", None):
            u = response.usage
            usage_tracker.add(
                input_tokens=getattr(u, "input_tokens", 0) or 0,
                output_tokens=getattr(u, "output_tokens", 0) or 0,
                cache_creation_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            )

        # Append assistant turn to history before processing tool uses.
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]

        # Stream any narration to the log.
        for tb in text_blocks:
            t = (tb.text or "").strip()
            if t:
                log_fn(t)
                final_text = t  # last text wins as the "summary"

        # No more tool calls -> we're done.
        if not tool_uses:
            log_fn("Agent finished.")
            return final_text or "Done."

        # Execute every tool call in this assistant turn and feed the results
        # back as a single user message containing tool_result blocks.
        results: list[dict] = []
        for tu in tool_uses:
            if is_stopped():
                log_fn("Stopped by user (mid-step).")
                return "stopped"

            name = tu.name
            tu_id = tu.id
            tu_input = tu.input or {}

            try:
                if name == _TOOL_NAME:
                    action = tu_input.get("action", "")
                    log_fn(f"-> {action}{(' '+str(tu_input.get('coordinate'))) if tu_input.get('coordinate') else ''}{(' ' + repr(tu_input.get('text'))[:60]) if tu_input.get('text') else ''}")
                    # Approval gate: if this action looks destructive AND a
                    # callback is wired, ask the user before doing it. The
                    # callback runs synchronously and returns True/False.
                    if approval_callback is not None:
                        triggered = _looks_dangerous(action, tu_input, danger_words)
                        if triggered:
                            summary = _action_summary(tu_input)
                            log_fn(f"   ?  approval needed (matched {triggered!r}): {summary}")
                            if not approval_callback(
                                f"Worker Buddy wants to run a potentially destructive action:\n\n"
                                f"  {summary}\n\n"
                                f"Triggered by danger word: {triggered!r}\n\n"
                                f"Allow?"
                            ):
                                log_fn("   ✗ user declined; reporting back to agent")
                                results.append(_result_block_for_text(
                                    tu_id,
                                    {"ok": False, "error": "user declined this action; pick a safer alternative"},
                                    is_error=True,
                                ))
                                continue
                            log_fn("   ✓ user approved")
                    result = _execute_computer_action(tu_input, scale=scale)
                    if action == "screenshot" and "image_b64" in result:
                        results.append(_result_block_for_screenshot(tu_id, result))
                    else:
                        results.append(_result_block_for_text(tu_id, result, is_error=not result.get("ok", True)))
                else:
                    log_fn(f"-> {name}({tu_input})")
                    result = _execute_custom_tool(name, tu_input)
                    results.append(_result_block_for_text(tu_id, result, is_error=not result.get("ok", True)))
            except Exception as e:
                log.exception("tool execution failed")
                results.append(_result_block_for_text(tu_id, {"error": str(e)}, is_error=True))
                log_fn(f"   ! tool error: {e}")

            # Tiny pause so we don't visually jackhammer the screen.
            time.sleep(0.05)

        messages.append({"role": "user", "content": results})

    log_fn(f"Hit max_steps={max_steps} without completing.")
    return final_text or "incomplete: max steps reached"
