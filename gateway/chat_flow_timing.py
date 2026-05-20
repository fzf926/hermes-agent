"""Structured timing logs for API chat / responses flows."""

from __future__ import annotations

import contextvars
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger("hermes.chat.flow")

_current_timer: contextvars.ContextVar[Optional["ChatFlowTimer"]] = contextvars.ContextVar(
    "chat_flow_timer",
    default=None,
)


def chat_flow_timing_enabled() -> bool:
    raw = os.getenv("HERMES_CHAT_FLOW_TIMING", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def get_chat_flow_timer() -> Optional["ChatFlowTimer"]:
    return _current_timer.get()


def bind_chat_flow_timer(timer: "ChatFlowTimer") -> contextvars.Token:
    return _current_timer.set(timer)


def reset_chat_flow_timer(token: contextvars.Token) -> None:
    _current_timer.reset(token)


def flow_step(step: str, **fields: Any) -> None:
    """Log a step on the current timer when timing is enabled."""
    timer = get_chat_flow_timer()
    if timer is not None:
        timer.step(step, **fields)


class ChatFlowTimer:
    """Accumulates per-step and total latency for one HTTP conversation request."""

    __slots__ = ("flow", "request_id", "_extra", "_t0", "_last")

    def __init__(self, flow: str, *, request_id: str = "", **extra: Any) -> None:
        self.flow = flow
        self.request_id = (request_id or "").strip()
        self._extra = {k: v for k, v in extra.items() if v is not None and v != ""}
        self._t0 = time.perf_counter()
        self._last = self._t0

    def step(self, step: str, **fields: Any) -> None:
        if not chat_flow_timing_enabled():
            return
        now = time.perf_counter()
        step_ms = (now - self._last) * 1000.0
        total_ms = (now - self._t0) * 1000.0
        self._last = now
        parts = [
            f"flow={self.flow}",
            f"step={step}",
            f"step_ms={step_ms:.1f}",
            f"total_ms={total_ms:.1f}",
        ]
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        merged = {**self._extra, **fields}
        for key, value in merged.items():
            if value is None or value == "":
                continue
            parts.append(f"{key}={value}")
        logger.info("[chat-flow-timing] %s", " ".join(parts))

    def finish(self, **fields: Any) -> None:
        self.step("done", **fields)


@contextmanager
def chat_flow_scope(flow: str, *, request_id: str = "", **extra: Any) -> Iterator[ChatFlowTimer]:
    timer = ChatFlowTimer(flow, request_id=request_id, **extra)
    token = bind_chat_flow_timer(timer)
    try:
        yield timer
    finally:
        timer.finish()
        reset_chat_flow_timer(token)
