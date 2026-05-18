"""Capture generic tool-call records for MySQL chat persistence."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _coerce_json_value(value: Any) -> Any:
    """Return a value suitable for a MySQL JSON column."""
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)


def json_dumps_for_mysql(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(_coerce_json_value(value), ensure_ascii=False, default=str)


def _parse_json_object(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def infer_tool_status(function_result: Any) -> str:
    """Map a Hermes tool result to chat_tool_call.status."""
    parsed = _parse_json_object(function_result)
    text = function_result if isinstance(function_result, str) else ""
    lowered = text.lower()

    if parsed:
        status = str(parsed.get("status") or "").lower()
        if status in {"timeout", "timed_out"}:
            return "timeout"
        if status in {"failed", "failure", "error"}:
            return "failed"
        if parsed.get("timeout") is True:
            return "timeout"
        if parsed.get("success") is False or parsed.get("error"):
            return "failed"

    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if lowered.startswith("error:"):
        return "failed"
    return "success"


def capture_tool_call_record(
    tool_call_id: Optional[str],
    function_name: str,
    function_args: Any,
    function_result: Any,
    *,
    latency_ms: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Build one generic tool-call record."""
    tool_name = str(function_name or "").strip()
    if not tool_name:
        return None

    record: Dict[str, Any] = {
        "tool_call_id": tool_call_id or None,
        "tool_name": tool_name[:128],
        "tool_args": _coerce_json_value(function_args),
        "tool_result": _coerce_json_value(function_result),
        "status": infer_tool_status(function_result),
    }
    if latency_ms is not None:
        record["latency_ms"] = max(0, int(latency_ms))
    return record


def tool_call_rows_for_turn_api(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map MySQL ``chat_tool_call`` rows to API items on a turn."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {
            "id": row.get("id"),
            "tool_name": row.get("tool_name", ""),
            "tool_args": row.get("tool_args"),
            "tool_result": row.get("tool_result"),
            "status": row.get("status", "success"),
        }
        if row.get("latency_ms") is not None:
            item["latency_ms"] = row["latency_ms"]
        if row.get("created_at"):
            item["created_at"] = row["created_at"]
        out.append(item)
    return out
