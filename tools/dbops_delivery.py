"""Volume-based DBOps result delivery (inline / Excel / export)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.chat_flow_timing import flow_step
from tools.dbops_count import build_count_sql, build_paginated_sql
from tools.dbops_delivery_config import (
    DBOpsDeliveryConfig,
    build_hermes_download_url,
    load_dbops_delivery_config,
)
from tools.dbops_excel import write_query_excel
from tools.dbops_export_client import run_dbops_export
from tools.dbops_http import DBOPS_QUERY_URL, post_dbops_form
from tools.dbops_result_formatter import (
    align_rows_to_columns,
    format_dbops_user_display,
    format_markdown_table,
)
from tools.dbops_models import DBOpsResolvedQuery

DBOPS_META_MARKER = "\n__DBOPS_META__\n"
_DBOPS_AGENT_INSTRUCTION = (
    "向用户展示查询结果时：必须完整复制 user_display 中的「查询成功」摘要和同一张 Markdown 表格；"
    "禁止把多行结果拆成「记录 1 / 记录 2」逐字段列举。"
    "同轮多次 dbops_query 时，按每次调用的 user_display 分块展示，不要混在一张表里。"
)


@dataclass
class DeliveryOutcome:
    text: str
    meta: dict[str, Any]


def _parse_count_from_rows(columns: list[Any], rows: list[Any]) -> int | None:
    aligned = align_rows_to_columns(columns, rows)
    if not aligned:
        return 0
    first = aligned[0]
    if isinstance(first, dict):
        for key in ("cnt", "COUNT(*)", "count"):
            if key in first:
                try:
                    return int(first[key])
                except (TypeError, ValueError):
                    pass
        if len(first) == 1:
            val = next(iter(first.values()))
            try:
                return int(val)
            except (TypeError, ValueError):
                return None
        return None
    if isinstance(first, list):
        for idx, col in enumerate(columns):
            if str(col).lower() in {"cnt", "count(*)", "count"}:
                try:
                    return int(first[idx])
                except (TypeError, ValueError, IndexError):
                    pass
        if len(first) == 1:
            try:
                return int(first[0])
            except (TypeError, ValueError):
                return None
    return None


def _run_query(
    resolved: DBOpsResolvedQuery,
    sql_content: str,
    *,
    cookie_text: str,
    csrf_token: str,
    limit_num: int,
) -> tuple[list[Any], list[Any], dict[str, Any] | None, str | None]:
    result = post_dbops_form(
        DBOPS_QUERY_URL,
        {
            "instance_name": resolved.instance_name,
            "db_name": resolved.db_name,
            "schema_name": resolved.schema_name,
            "tb_name": resolved.tb_name,
            "sql_content": sql_content,
            "limit_num": str(limit_num),
        },
        cookie_text=cookie_text,
        csrf_token=csrf_token,
    )
    if not result.ok:
        return [], [], None, result.error or result.msg
    columns = result.columns or []
    rows = align_rows_to_columns(columns, result.rows or [])
    return columns, rows, result.data, None


def _base_meta(
    resolved: DBOpsResolvedQuery,
    *,
    generation_reason: str | None,
    executed: bool = True,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "success": True,
        "executed": executed,
        "generation_reason": generation_reason,
        "source": {
            "db_key": resolved.db_key,
            "instance_name": resolved.instance_name,
            "db_name": resolved.db_name,
            "schema_name": resolved.schema_name,
            "tb_name": resolved.tb_name,
            "limit_num": resolved.limit_num,
        },
    }
    return meta


def _format_generation_reason_block(generation_reason: str | None) -> str:
    if not generation_reason or not str(generation_reason).strip():
        return ""
    return f"\n\n**生成依据：**\n{generation_reason.strip()}\n"


def execute_with_volume_routing(
    resolved: DBOpsResolvedQuery,
    *,
    cookie_text: str,
    csrf_token: str,
    generation_reason: str | None = None,
    delivery_cfg: DBOpsDeliveryConfig | None = None,
) -> DeliveryOutcome | str:
    """Run COUNT then deliver via inline, Excel, or DBOps export. Returns error string on failure."""
    cfg = delivery_cfg or load_dbops_delivery_config()

    flow_step("dbops_count_start")
    count_cols, count_rows, count_data, count_err = _run_query(
        resolved,
        build_count_sql(resolved.sql_content),
        cookie_text=cookie_text,
        csrf_token=csrf_token,
        limit_num=1,
    )
    if count_err:
        return count_err

    total = _parse_count_from_rows(count_cols, count_rows)
    if total is None:
        return "Could not parse COUNT result from DBOps"

    meta = _base_meta(resolved, generation_reason=generation_reason)
    meta["total_row_count"] = total
    reason_block = _format_generation_reason_block(generation_reason)

    if total == 0:
        meta["delivery_mode"] = "inline"
        meta["row_count"] = 0
        meta["column_count"] = 0
        meta["agent_instruction"] = _DBOPS_AGENT_INSTRUCTION
        user_display = format_dbops_user_display(
            sql_content=resolved.sql_content,
            instance_name=resolved.instance_name,
            db_name=resolved.db_name,
            columns=[],
            rows=[],
            row_count=0,
            query_time=None,
        )
        return DeliveryOutcome(text=user_display + reason_block + DBOPS_META_MARKER + json.dumps(meta, ensure_ascii=False), meta=meta)

    flow_step("dbops_count_done", total_row_count=total)

    if total >= cfg.export_threshold:
        flow_step("dbops_export_start", total_row_count=total)
        exported = run_dbops_export(
            instance_name=resolved.instance_name,
            db_name=resolved.db_name,
            schema_name=resolved.schema_name,
            tb_name=resolved.tb_name,
            sql_content=resolved.sql_content,
            cookie_text=cookie_text,
            csrf_token=csrf_token,
            poll_interval_sec=cfg.export_poll_interval_sec,
            poll_timeout_sec=cfg.export_poll_timeout_sec,
        )
        if not exported.ok:
            return exported.error or "DBOps export failed"
        flow_step("dbops_export_done", task_id=exported.task_id)

        meta["delivery_mode"] = "dbops_export"
        meta["download_url"] = exported.download_url
        meta["dbops_export_task_id"] = exported.task_id
        meta["row_count"] = total
        meta["agent_instruction"] = (
            "结果行数超过 5000，已通过 DBOps 导出。向用户提供 download_url，"
            "不要编造表格内容。"
        )
        message = (
            f"查询成功，共 {total} 行（超过 {cfg.excel_threshold_max} 行），"
            f"已通过 DBOps 异步导出。\n\n"
            f"**下载链接：** {exported.download_url}"
            f"{reason_block}"
        )
        return DeliveryOutcome(text=message + DBOPS_META_MARKER + json.dumps(meta, ensure_ascii=False), meta=meta)

    if total >= cfg.excel_threshold_min:
        flow_step("dbops_excel_start", total_row_count=total)
        page_size = cfg.pagination_page_size
        all_columns: list[Any] = []
        all_rows: list[list[Any]] = []
        offset = 0
        query_time_f: float | None = None
        while offset < total:
            page_sql = build_paginated_sql(
                resolved.sql_content, offset=offset, limit=page_size
            )
            cols, rows, data, err = _run_query(
                resolved,
                page_sql,
                cookie_text=cookie_text,
                csrf_token=csrf_token,
                limit_num=min(page_size, 1000),
            )
            if err:
                return err
            if not all_columns:
                all_columns = cols
            all_rows.extend(rows)
            if data and data.get("query_time") is not None and query_time_f is None:
                try:
                    query_time_f = float(data.get("query_time"))
                except (TypeError, ValueError):
                    pass
            offset += page_size

        export_uid = uuid.uuid4().hex
        export_path = (cfg.exports_dir or Path(".")) / f"{export_uid}.xlsx"
        try:
            write_query_excel(export_path, columns=all_columns, rows=all_rows)
        except RuntimeError as exc:
            return str(exc)
        flow_step("dbops_excel_done", export_uid=export_uid, row_count=total)

        download_url = build_hermes_download_url(cfg.public_base_url, export_uid)
        meta["delivery_mode"] = "excel"
        meta["download_url"] = download_url
        meta["export_uid"] = export_uid
        meta["row_count"] = total
        meta["column_count"] = len(all_columns)
        meta["agent_instruction"] = (
            f"共 {total} 行，已生成本地 Excel。向用户提供 download_url，"
            "不要逐行列举或编造表格。"
        )
        message = (
            f"查询成功，共 {total} 行（≥ {cfg.excel_threshold_min} 行），"
            f"已导出 Excel。\n\n"
            f"**下载链接：** {download_url}"
            f"{reason_block}"
        )
        return DeliveryOutcome(text=message + DBOPS_META_MARKER + json.dumps(meta, ensure_ascii=False), meta=meta)

    # inline: 1..20 (or up to excel_threshold_min - 1)
    flow_step("dbops_inline_start", total_row_count=total)
    limit = min(total, 1000, max(resolved.limit_num, total))
    cols, rows, data, err = _run_query(
        resolved,
        resolved.sql_content,
        cookie_text=cookie_text,
        csrf_token=csrf_token,
        limit_num=limit,
    )
    if err:
        return err
    flow_step("dbops_inline_done", row_count=len(rows))

    query_time = (data or {}).get("query_time")
    try:
        query_time_f = float(query_time) if query_time is not None else None
    except (TypeError, ValueError):
        query_time_f = None

    meta["delivery_mode"] = "inline"
    meta["row_count"] = len(rows)
    meta["column_count"] = len(cols)
    meta["agent_instruction"] = _DBOPS_AGENT_INSTRUCTION
    meta["query"] = {
        "full_sql": (data or {}).get("full_sql"),
        "query_time": query_time,
        "affected_rows": (data or {}).get("affected_rows"),
        "seconds_behind_master": (data or {}).get("seconds_behind_master"),
    }
    user_display = format_dbops_user_display(
        sql_content=resolved.sql_content,
        instance_name=resolved.instance_name,
        db_name=resolved.db_name,
        columns=cols,
        rows=rows,
        row_count=len(rows),
        query_time=query_time_f,
    )
    return DeliveryOutcome(
        text=user_display + reason_block + DBOPS_META_MARKER + json.dumps(meta, ensure_ascii=False),
        meta=meta,
    )
