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

# Anthropic's Computer Use tool. Type version is what determines which actions
# Claude knows about; sonnet-4.x supports computer_20250124 with the full set
# (mouse_move, scroll, hold_key, triple_click, etc.).
_TOOL_TYPE = "computer_20250124"
_TOOL_NAME = "computer"

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


def _build_tools() -> list[dict]:
    info = get_screen_info()
    return [
        {
            "type": _TOOL_TYPE,
            "name": _TOOL_NAME,
            "display_width_px": info.width,
            "display_height_px": info.height,
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


def _execute_computer_action(action_input: dict) -> dict:
    """Translate a `computer` tool_use input dict into a desktop_tools call."""
    action = action_input.get("action", "")
    coord  = action_input.get("coordinate")  # [x, y] for most actions
    text   = action_input.get("text")

    if action == "screenshot":
        return take_screenshot()
    if action == "mouse_move" and coord:
        return mouse_move(coord[0], coord[1])
    if action == "left_click" and coord:
        return click(coord[0], coord[1])
    if action == "right_click" and coord:
        return right_click(coord[0], coord[1])
    if action == "middle_click" and coord:
        return click(coord[0], coord[1], button="middle")
    if action == "double_click" and coord:
        return double_click(coord[0], coord[1])
    if action == "triple_click" and coord:
        return click(coord[0], coord[1], clicks=3)
    if action == "left_click_drag":
        # Anthropic format: start at current cursor, drag to coordinate
        cur = cursor_position()
        return left_click_drag(cur["x"], cur["y"], coord[0], coord[1])
    if action == "type" and text is not None:
        return type_text(text)
    if action == "key" and text is not None:
        return press_key(text)
    if action == "hold_key" and text is not None:
        # Best effort: just press it; pyautogui doesn't have a great hold primitive.
        return press_key(text)
    if action == "cursor_position":
        return cursor_position()
    if action == "wait":
        # Anthropic uses duration in seconds.
        return wait(float(action_input.get("duration", 1.0)))
    if action == "scroll":
        if not coord:
            cur = cursor_position(); cx, cy = cur["x"], cur["y"]
        else:
            cx, cy = coord[0], coord[1]
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


def run_desktop_task(
    *,
    task: str,
    api_key: str,
    model: str = "claude-sonnet-4-7",
    max_steps: int = DEFAULT_MAX_STEPS,
    log_fn: Callable[[str], None] = print,
    is_stopped: Callable[[], bool] = lambda: False,
) -> str:
    """Run one desktop task to completion (or until stopped / max_steps).

    Returns the agent's final text reply (or a short error string). All
    intermediate progress goes through `log_fn(line)`.
    """
    client = anthropic.Anthropic(api_key=api_key)
    tools = _build_tools()
    info = get_screen_info()
    log_fn(f"Desktop agent starting on {info.width}x{info.height}, model={model}")

    messages: list[dict] = [{"role": "user", "content": task}]
    final_text = ""

    for step in range(max_steps):
        if is_stopped():
            log_fn("Stopped by user.")
            return "stopped"

        try:
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                tools=tools,
                messages=messages,
                system=_SYSTEM_PROMPT,
                # Computer use requires this beta header in some SDK versions; the
                # current SDK auto-sets it when a computer_* tool is in the list,
                # but we pass it explicitly to be safe across versions.
                extra_headers={"anthropic-beta": "computer-use-2025-01-24"},
            )
        except anthropic.APIError as e:
            log_fn(f"API error: {e}")
            return f"error: {e}"

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
                    result = _execute_computer_action(tu_input)
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
