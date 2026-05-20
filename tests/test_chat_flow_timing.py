import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor

from gateway.chat_flow_timing import ChatFlowTimer, chat_flow_scope, flow_step


def test_chat_flow_timer_logs_steps(caplog, monkeypatch):
    monkeypatch.setenv("HERMES_CHAT_FLOW_TIMING", "1")
    with caplog.at_level(logging.INFO, logger="hermes.chat.flow"):
        with chat_flow_scope("test.flow", request_id="req-1") as timer:
            timer.step("alpha")
            timer.step("beta", extra_field=42)
    assert any("chat-flow-timing" in r.message for r in caplog.records)
    assert any("step=alpha" in r.message for r in caplog.records)
    assert any("step=done" in r.message for r in caplog.records)


def test_flow_step_noop_without_timer(monkeypatch):
    monkeypatch.setenv("HERMES_CHAT_FLOW_TIMING", "1")
    flow_step("orphan_step")  # should not raise


def test_flow_step_in_executor_thread_via_copy_context(caplog, monkeypatch):
    """Mirrors _run_agent: contextvars must survive run_in_executor workers."""
    monkeypatch.setenv("HERMES_CHAT_FLOW_TIMING", "1")

    def _worker():
        flow_step("agent_create_start")
        flow_step("tool_complete", tool="dbops", latency_ms=42)

    with caplog.at_level(logging.INFO, logger="hermes.chat.flow"):
        with chat_flow_scope("test.flow", request_id="req-exec"):
            ctx = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(ctx.run, _worker).result()

    messages = [r.message for r in caplog.records]
    assert any("step=agent_create_start" in m for m in messages)
    assert any("step=tool_complete" in m and "tool=dbops" in m for m in messages)
