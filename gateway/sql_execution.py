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
    generation_reason = str(args.get("generation_reason") or "").strip() or None
    status = "success"
    error_message: Optional[str] = None
    query_time_ms: Optional[float] = None
    row_count: Optional[int] = None
    total_row_count: Optional[int] = None
    delivery_mode: Optional[str] = None
    download_url: Optional[str] = None
    export_uid: Optional[str] = None
    dbops_export_task_id: Optional[str] = None

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
        if parsed.get("total_row_count") is not None:
            try:
                total_row_count = int(parsed["total_row_count"])
            except (TypeError, ValueError):
                total_row_count = None
        dm = parsed.get("delivery_mode")
        if isinstance(dm, str) and dm.strip():
            delivery_mode = dm.strip()
        elif parsed.get("executed") is False:
            delivery_mode = "sql_only"
        du = parsed.get("download_url")
        if isinstance(du, str) and du.strip():
            download_url = du.strip()
        gr = parsed.get("generation_reason")
        if isinstance(gr, str) and gr.strip():
            generation_reason = gr.strip()
        eu = parsed.get("export_uid")
        if isinstance(eu, str) and eu.strip():
            export_uid = eu.strip()
        et = parsed.get("dbops_export_task_id")
        if isinstance(et, str) and et.strip():
            dbops_export_task_id = et.strip()

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
    if total_row_count is not None:
        record["total_row_count"] = total_row_count
    if delivery_mode:
        record["delivery_mode"] = delivery_mode
    if download_url:
        record["download_url"] = download_url
    if generation_reason:
        record["generation_reason"] = generation_reason
    if export_uid:
        record["export_uid"] = export_uid
    if dbops_export_task_id:
        record["dbops_export_task_id"] = dbops_export_task_id
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
        if rec.get("total_row_count") is not None:
            item["total_row_count"] = rec["total_row_count"]
        if rec.get("delivery_mode"):
            item["delivery_mode"] = rec["delivery_mode"]
        if rec.get("download_url"):
            item["download_url"] = rec["download_url"]
        if rec.get("generation_reason"):
            item["generation_reason"] = rec["generation_reason"]
        if rec.get("export_uid"):
            item["export_uid"] = rec["export_uid"]
        if rec.get("dbops_export_task_id"):
            item["dbops_export_task_id"] = rec["dbops_export_task_id"]
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


def mysql_sql_row_to_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map a ``chat_sql_execution`` row to the in-memory record shape."""
    rec: Dict[str, Any] = {
        "sql_content": row.get("sql_content", ""),
        "database": row.get("db_name", ""),
        "instance": row.get("instance_name", ""),
        "status": row.get("status", "success"),
    }
    if row.get("tool_call_id"):
        rec["tool_call_id"] = row["tool_call_id"]
    if row.get("created_at"):
        rec["executed_at"] = row["created_at"]
    if row.get("error_message"):
        rec["error_message"] = row["error_message"]
    if row.get("query_time_ms") is not None:
        rec["query_time_ms"] = row["query_time_ms"]
    if row.get("row_count") is not None:
        rec["row_count"] = row["row_count"]
    if row.get("total_row_count") is not None:
        rec["total_row_count"] = row["total_row_count"]
    if row.get("delivery_mode"):
        rec["delivery_mode"] = row["delivery_mode"]
    if row.get("download_url"):
        rec["download_url"] = row["download_url"]
    if row.get("generation_reason"):
        rec["generation_reason"] = row["generation_reason"]
    if row.get("export_uid"):
        rec["export_uid"] = row["export_uid"]
    if row.get("dbops_export_task_id"):
        rec["dbops_export_task_id"] = row["dbops_export_task_id"]
    if row.get("user_display"):
        rec["user_display"] = row["user_display"]
    if row.get("result_table"):
        rec["result_table"] = row["result_table"]
    return rec


def _merge_captured_sql_fields(
    record: Dict[str, Any],
    captured: Dict[str, Any],
) -> None:
    """Fill display fields from a live ``dbops_query`` capture when DB columns are empty."""
    for key in (
        "user_display",
        "result_table",
        "delivery_mode",
        "download_url",
        "generation_reason",
        "export_uid",
        "dbops_export_task_id",
        "total_row_count",
        "row_count",
        "query_time_ms",
        "error_message",
        "status",
    ):
        if record.get(key) in (None, "") and captured.get(key) not in (None, ""):
            record[key] = captured[key]


def _captures_from_tool_call_rows(
    tool_call_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    captures: List[Dict[str, Any]] = []
    for tc in tool_call_rows:
        if str(tc.get("tool_name") or "") != "dbops_query":
            continue
        captured = capture_dbops_sql_execution(
            None,
            "dbops_query",
            tc.get("tool_args"),
            tc.get("tool_result"),
        )
        if captured:
            captures.append(captured)
    return captures


def _enrich_records_from_captures(
    records: List[Dict[str, Any]],
    captures: List[Dict[str, Any]],
) -> None:
    if not records or not captures:
        return

    cap_by_tool_call_id = {
        str(c["tool_call_id"]): c
        for c in captures
        if c.get("tool_call_id")
    }
    used_capture_ids: set[int] = set()

    for rec in records:
        tc_id = str(rec.get("tool_call_id") or "").strip()
        if tc_id and tc_id in cap_by_tool_call_id:
            _merge_captured_sql_fields(rec, cap_by_tool_call_id[tc_id])
            for idx, cap in enumerate(captures):
                if str(cap.get("tool_call_id") or "") == tc_id:
                    used_capture_ids.add(idx)

    cap_by_sql = {
        str(c.get("sql_content") or "").strip(): c
        for c in captures
        if str(c.get("sql_content") or "").strip()
    }
    for rec in records:
        if rec.get("user_display"):
            continue
        sql_key = str(rec.get("sql_content") or "").strip()
        cap = cap_by_sql.get(sql_key)
        if cap:
            _merge_captured_sql_fields(rec, cap)

    remaining_caps = [c for i, c in enumerate(captures) if i not in used_capture_ids]
    remaining_recs = [r for r in records if not r.get("user_display")]
    for rec, cap in zip(remaining_recs, remaining_caps):
        _merge_captured_sql_fields(rec, cap)


def sql_records_for_turn_api(
    sql_rows: List[Dict[str, Any]],
    *,
    tool_call_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Map MySQL SQL rows to the same ``sql`` array shape as ``/v1/responses``."""
    records = [mysql_sql_row_to_record(row) for row in sql_rows]
    if tool_call_rows:
        _enrich_records_from_captures(records, _captures_from_tool_call_rows(tool_call_rows))
    return sql_records_for_api(records)


def turn_sql_payload_for_api(
    sql_rows: List[Dict[str, Any]],
    *,
    tool_call_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build turn-level ``sql`` + ``sql_display`` matching responses API attachments."""
    sql_items = sql_records_for_turn_api(sql_rows, tool_call_rows=tool_call_rows)
    payload: Dict[str, Any] = {"sql": sql_items}
    combined = sql_results_display_for_api(sql_items)
    if combined:
        payload["sql_display"] = combined
    return payload
