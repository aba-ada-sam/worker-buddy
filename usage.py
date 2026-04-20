"""Token usage + cost accounting for Worker Buddy.

A small, dependency-free tracker. Both desktop_mode (which calls Anthropic
directly) and browser_mode (which goes through LiteLLM) feed token counts
in here; the chat UI surfaces a per-task summary and a tray menu shows
running totals (today / this month / lifetime).

Pricing reflects Anthropic's published per-MTok rates. Update PRICES when
new models ship. Cache-write / cache-read tokens are tracked separately
when the SDK reports them, since Anthropic prices them differently from
plain input.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path

# USD per million tokens. Source: Anthropic public pricing.
# Tuple: (input, output, cache_write, cache_read).
PRICES: dict[str, tuple[float, float, float, float]] = {
    # Claude 4.5 Sonnet
    "claude-sonnet-4-5-20250929": (3.0, 15.0, 3.75, 0.30),
    # Claude 4 Sonnet (May 2025)
    "claude-sonnet-4-20250514":   (3.0, 15.0, 3.75, 0.30),
    # Claude 3.7 Sonnet
    "claude-3-7-sonnet-20250219": (3.0, 15.0, 3.75, 0.30),
    # Claude 3.5 Sonnet v2
    "claude-3-5-sonnet-20241022": (3.0, 15.0, 3.75, 0.30),
    # Claude 4.7 Opus
    "claude-opus-4-7":            (15.0, 75.0, 18.75, 1.50),
    # Claude 4.6 Opus
    "claude-opus-4-6":            (15.0, 75.0, 18.75, 1.50),
    # Claude 4.5 Haiku
    "claude-haiku-4-5-20251001":  (1.0, 5.0, 1.25, 0.10),
}
# Used when an unknown model id is reported -- conservative-ish (Sonnet).
_FALLBACK_PRICE = (3.0, 15.0, 3.75, 0.30)


@dataclass
class TaskUsage:
    """Token + cost rollup for a single task."""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    api_calls: int = 0

    def add(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> None:
        self.input_tokens         += int(input_tokens or 0)
        self.output_tokens        += int(output_tokens or 0)
        self.cache_creation_tokens += int(cache_creation_tokens or 0)
        self.cache_read_tokens    += int(cache_read_tokens or 0)
        self.api_calls            += 1

    def cost_usd(self) -> float:
        p_in, p_out, p_cw, p_cr = PRICES.get(self.model, _FALLBACK_PRICE)
        return (
            self.input_tokens          / 1_000_000 * p_in
            + self.output_tokens       / 1_000_000 * p_out
            + self.cache_creation_tokens / 1_000_000 * p_cw
            + self.cache_read_tokens   / 1_000_000 * p_cr
        )

    def summary_line(self) -> str:
        """One-line human summary safe to drop in the chat."""
        cost = self.cost_usd()
        bits = [f"{self.input_tokens:,} in", f"{self.output_tokens:,} out"]
        if self.cache_read_tokens:
            bits.append(f"{self.cache_read_tokens:,} cached")
        token_str = " / ".join(bits)
        # Pretty cents for sub-dollar runs, otherwise dollars to 3 places.
        cost_str = f"${cost:.4f}" if cost < 1 else f"${cost:.3f}"
        return f"Cost: {cost_str}  ({token_str}, {self.api_calls} call{'s' if self.api_calls != 1 else ''})"


# ── Persistent rollup -- "today / this month / lifetime" ─────────────────────

_lock = threading.Lock()


def _ledger_path() -> Path:
    return Path(__file__).resolve().parent / "logs" / "usage_ledger.json"


def _load_ledger() -> dict:
    p = _ledger_path()
    if not p.exists():
        return {"by_day": {}, "lifetime": {"input": 0, "output": 0, "cache_w": 0, "cache_r": 0, "cost_usd": 0.0, "tasks": 0}}
    try:
        with _lock:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"by_day": {}, "lifetime": {"input": 0, "output": 0, "cache_w": 0, "cache_r": 0, "cost_usd": 0.0, "tasks": 0}}


def _save_ledger(data: dict) -> None:
    p = _ledger_path()
    p.parent.mkdir(exist_ok=True)
    with _lock:
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record(task: TaskUsage) -> None:
    """Roll a finished task's usage into the persistent ledger."""
    if task.api_calls == 0:
        return
    today = date.today().isoformat()
    cost = task.cost_usd()
    data = _load_ledger()
    day = data["by_day"].setdefault(today, {"input": 0, "output": 0, "cache_w": 0, "cache_r": 0, "cost_usd": 0.0, "tasks": 0})
    for key, val in (
        ("input", task.input_tokens), ("output", task.output_tokens),
        ("cache_w", task.cache_creation_tokens), ("cache_r", task.cache_read_tokens),
    ):
        day[key] += val
        data["lifetime"][key] += val
    day["cost_usd"] += cost
    day["tasks"] += 1
    data["lifetime"]["cost_usd"] += cost
    data["lifetime"]["tasks"] += 1
    # Trim by_day to last 90 days so the file stays small.
    if len(data["by_day"]) > 90:
        keep = sorted(data["by_day"].keys())[-90:]
        data["by_day"] = {k: data["by_day"][k] for k in keep}
    _save_ledger(data)


def rollup_summary() -> str:
    """Multi-line summary suitable for a tray balloon / dialog."""
    data = _load_ledger()
    today = data["by_day"].get(date.today().isoformat(), {"cost_usd": 0.0, "tasks": 0, "input": 0, "output": 0})
    # Month-to-date sum
    ym = date.today().strftime("%Y-%m")
    month_cost = 0.0
    month_tasks = 0
    for day_str, rec in data["by_day"].items():
        if day_str.startswith(ym):
            month_cost += rec.get("cost_usd", 0.0)
            month_tasks += rec.get("tasks", 0)
    life = data["lifetime"]
    return (
        f"Today:    ${today['cost_usd']:.3f}  ({today['tasks']} task{'s' if today['tasks'] != 1 else ''})\n"
        f"Month:    ${month_cost:.2f}  ({month_tasks} tasks)\n"
        f"Lifetime: ${life['cost_usd']:.2f}  ({life['tasks']} tasks, "
        f"{life['input']:,} in / {life['output']:,} out)"
    )


def lifetime_cost() -> float:
    return float(_load_ledger()["lifetime"].get("cost_usd", 0.0))
