"""Capture and serialize DBOps SQL executions for API responses and MySQL storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_DBOPS_META_MARKER = "\n__DBOPS_META__\n"


def _parse_tool_json(function_result: Any) -> Optional[Dict[str, Any]]:
    if function_result is None:
        return None
    if isinstance(function_result, dict):
        return function_result
    if not isinstance(function_result, str):
        return None
    text = function_result.strip()
    if not text:
        return None

    if _DBOPS_META_MARKER in function_result:
        display_part, _, meta_part = function_result.partition(_DBOPS_META_MARKER)
        try:
            meta = json.loads(meta_part.strip())
        except json.JSONDecodeError:
            meta = {}
        if isinstance(meta, dict):
            if display_part.strip() and not meta.get("user_display"):
                meta["user_display"] = display_part.strip()
            return meta
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_args(function_args: Any) -> Dict[str, Any]:
    if isinstance(function_args, dict):
        return function_args
    if isinstance(function_args, str) and function_args.strip():
        try:
            parsed = json.loads(function_args)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def capture_dbops_sql_execution(
    tool_call_id: Optional[str],
    function_name: str,
    function_args: Any,
    function_result: Any,
) -> Optional[Dict[str, Any]]:
    """Build one SQL execution record when ``dbops_query`` completes."""
    if function_name != "dbops_query":
        return None

    args = _coerce_args(function_args)
    parsed = _parse_tool_json(function_result)

    sql_content = str(args.get("sql_content") or "").strip()
    database = str(args.get("db_name") or "").strip()
    instance = str(args.get("instance_name") or "").strip()
    status = "success"
    error_message: Optional[str] = None
    query_time_ms: Optional[float] = None
    row_count: Optional[int] = None

    if parsed:
        source = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
        query = parsed.get("query") if isinstance(parsed.get("query"), dict) else {}
        database = database or str(source.get("db_name") or "").strip()
        instance = instance or str(source.get("instance_name") or "").strip()
        sql_content = sql_content or str(query.get("full_sql") or "").strip()
        if parsed.get("success") is False or parsed.get("error"):
            status = "error"
            err = parsed.get("error")
            if isinstance(err, str):
                error_message = err[:512]
            elif err is not None:
                error_message = str(err)[:512]
        if query.get("query_time") is not None:
            try:
                query_time_ms = float(query["query_time"]) * 1000.0
            except (TypeError, ValueError):
                query_time_ms = None
        if parsed.get("row_count") is not None:
            try:
                row_count = int(parsed["row_count"])
            except (TypeError, ValueError):
                row_count = None

    user_display: Optional[str] = None
    result_table: Optional[str] = None
    if parsed:
        ud = parsed.get("user_display")
        if isinstance(ud, str) and ud.strip():
            user_display = ud.strip()
        rt = parsed.get("result_table")
        if isinstance(rt, str) and rt.strip():
            result_table = rt.strip()

    if not sql_content:
        return None
    if not database and not instance:
        # Cannot form a useful audit record without target DB context.
        return None

    record: Dict[str, Any] = {
        "sql_content": sql_content,
        "database": database,
        "instance": instance,
        "status": status,
        "executed_at": _utc_now_iso(),
    }
    if tool_call_id:
        record["tool_call_id"] = tool_call_id
    if error_message:
        record["error_message"] = error_message
    if query_time_ms is not None:
        record["query_time_ms"] = query_time_ms
    if row_count is not None:
        record["row_count"] = row_count
    if user_display:
        record["user_display"] = user_display
    if result_table:
        record["result_table"] = result_table
    return record


def sql_records_for_api(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Shape stored/collected records for HTTP ``sql`` array in responses."""
    out: List[Dict[str, Any]] = []
    for rec in records:
        item: Dict[str, Any] = {
            "sql_content": rec.get("sql_content", ""),
            "database": rec.get("database", ""),
            "instance": rec.get("instance", ""),
            "status": rec.get("status", "success"),
        }
        if rec.get("tool_call_id"):
            item["tool_call_id"] = rec["tool_call_id"]
        if rec.get("executed_at"):
            item["executed_at"] = rec["executed_at"]
        if rec.get("error_message"):
            item["error_message"] = rec["error_message"]
        if rec.get("query_time_ms") is not None:
            item["query_time_ms"] = rec["query_time_ms"]
        if rec.get("row_count") is not None:
            item["row_count"] = rec["row_count"]
        if rec.get("user_display"):
            item["user_display"] = rec["user_display"]
        if rec.get("result_table"):
            item["result_table"] = rec["result_table"]
        out.append(item)
    return out


def sql_results_display_for_api(records: List[Dict[str, Any]]) -> str:
    """Merge multiple SQL execution ``user_display`` blocks for API consumers."""
    from tools.dbops_result_formatter import format_multiple_dbops_displays

    sections = [
        str(rec.get("user_display") or "").strip()
        for rec in records
        if rec.get("user_display")
    ]
    return format_multiple_dbops_displays(sections)


def sql_records_for_turn_api(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map MySQL ``chat_sql_execution`` rows to API ``sql`` items on a turn."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {
            "sql_content": row.get("sql_content", ""),
            "database": row.get("db_name", ""),
            "instance": row.get("instance_name", ""),
            "status": row.get("status", "success"),
        }
        if row.get("tool_call_id"):
            item["tool_call_id"] = row["tool_call_id"]
        if row.get("created_at"):
            item["executed_at"] = row["created_at"]
        if row.get("error_message"):
            item["error_message"] = row["error_message"]
        if row.get("query_time_ms") is not None:
            item["query_time_ms"] = row["query_time_ms"]
        if row.get("row_count") is not None:
            item["row_count"] = row["row_count"]
        out.append(item)
    return out
