#!/usr/bin/env python3
"""DBOps SQL query tool via HTTP endpoint."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from hermes_constants import display_hermes_home, get_hermes_home
from tools.dbops_config import is_dbops_execute_enabled, load_dbops_yaml_config
from tools.dbops_delivery import DBOPS_META_MARKER, execute_with_volume_routing
from tools.dbops_models import DBOpsQueryInput, DBOpsResolvedQuery, normalize_limit as _normalize_limit
from tools.registry import registry, tool_error
from tools.sql_audit import SqlAuditor

logger = logging.getLogger(__name__)

_sql_auditor = SqlAuditor()

COOKIE_KEYS = (
    "csrftoken",
    "cluouser_name",
    "admin-authorization",
    "internal_account_token",
)

_SQL_IDENTIFIER_RE = r"(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_$]*)"
_SQL_TABLE_RE = re.compile(
    rf"\b(?:from|join)\s+({_SQL_IDENTIFIER_RE}(?:\s*\.\s*{_SQL_IDENTIFIER_RE})?)",
    re.IGNORECASE,
)
_INDEXED_OPERATOR_RE = re.compile(
    rf"(?:^|[\s(])(?:\w+\.)?`?{{column}}`?\s*(?:=|in\b|between\b|>=|<=|>|<|like\b)",
    re.IGNORECASE,
)


def _dbops_config_dir() -> Path:
    return get_hermes_home() / "config"


def get_dbops_cookie_path() -> Path:
    return _dbops_config_dir() / "dbops_cookie.json"


def get_dbops_db_config_path() -> Path:
    return _dbops_config_dir() / "dbops_db_config.json"


_load_dbops_yaml_config = load_dbops_yaml_config


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _load_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse JSON file: %s", path)
        return None


def _extract_cookie_map(raw_cookie: Any) -> dict[str, str]:
    if isinstance(raw_cookie, dict):
        source = raw_cookie
    elif isinstance(raw_cookie, str):
        source = {}
        cookie_str = raw_cookie.strip()
        if cookie_str.lower().startswith("cookie:"):
            cookie_str = cookie_str.split(":", 1)[1].strip()
        for item in cookie_str.split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            source[key.strip()] = value.strip()
    else:
        source = {}

    return {
        key: str(source.get(key, "")).strip()
        for key in COOKIE_KEYS
        if str(source.get(key, "")).strip()
    }


def parse_dbops_cookie_input(raw_cookie: str) -> dict[str, str]:
    return _extract_cookie_map(raw_cookie)


def save_dbops_cookie_from_raw(raw_cookie: str) -> dict[str, Any]:
    cookie_text = str(raw_cookie or "").strip()
    if not cookie_text:
        return {
            "ok": False,
            "error": "cookie cannot be empty",
            "saved_path": str(get_dbops_cookie_path()),
        }
    path = get_dbops_cookie_path()
    _ensure_parent_dir(path)
    content = {"cookie": cookie_text}
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "saved_path": str(path)}


def _ensure_default_cookie_file() -> None:
    path = get_dbops_cookie_path()
    if path.exists():
        return
    _ensure_parent_dir(path)
    empty = {"cookie": ""}
    path.write_text(json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_default_db_config_file() -> None:
    path = get_dbops_db_config_path()
    if path.exists():
        return
    _ensure_parent_dir(path)
    path.write_text("[]\n", encoding="utf-8")


def _extract_csrf_token(cookie_text: str) -> str:
    parsed = _extract_cookie_map(cookie_text)
    return parsed.get("csrftoken", "")


def _load_dbops_cookie_text() -> str:
    _ensure_default_cookie_file()
    data = _load_json_file(get_dbops_cookie_path()) or {}
    if isinstance(data, dict):
        # New format: raw cookie string as-is
        if isinstance(data.get("cookie"), str):
            return data.get("cookie", "").strip()
        # Backward compatibility: old split-cookie format
        if "cookies" in data:
            cookie_map = _extract_cookie_map(data.get("cookies"))
            return "; ".join(f"{k}={v}" for k, v in cookie_map.items() if v)
    if isinstance(data, str):
        return data.strip()
    return ""


def _load_dbops_db_configs() -> list[dict[str, Any]]:
    _ensure_default_db_config_file()
    raw = _load_json_file(get_dbops_db_config_path())
    if not isinstance(raw, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        instance_name = str(item.get("instance_name", "")).strip()
        db_name = str(item.get("db_name", "")).strip()
        if not key or not instance_name or not db_name:
            continue
        valid.append(
            {
                "key": key,
                "label": str(item.get("label", "")).strip(),
                "description": str(item.get("description", "")).strip(),
                "instance_name": instance_name,
                "db_name": db_name,
                "schema_name": str(item.get("schema_name", "")).strip(),
                "tb_name": str(item.get("tb_name", "")).strip(),
                "table_indexes": item.get("table_indexes") if isinstance(item.get("table_indexes"), dict) else {},
                "indexes": item.get("indexes") if isinstance(item.get("indexes"), dict) else {},
                "limit_num": _normalize_limit(item.get("limit_num", 100)),
            }
        )
    return valid


def _clean_sql_identifier(identifier: str) -> str:
    return re.sub(r"[`\s]", "", identifier or "").split(".")[-1].lower()


def _extract_sql_tables(sql: str) -> set[str]:
    return {
        _clean_sql_identifier(match.group(1))
        for match in _SQL_TABLE_RE.finditer(sql or "")
        if _clean_sql_identifier(match.group(1))
    }


def _normalize_table_indexes(raw: Any) -> dict[str, list[list[str]]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, list[list[str]]] = {}
    for table, indexes in raw.items():
        table_name = _clean_sql_identifier(str(table))
        if not table_name:
            continue
        normalized_indexes: list[list[str]] = []
        if isinstance(indexes, dict):
            iterable = indexes.values()
        elif isinstance(indexes, list):
            iterable = indexes
        else:
            iterable = []
        for item in iterable:
            columns: list[Any]
            if isinstance(item, dict):
                columns = item.get("columns") or item.get("fields") or []
            else:
                columns = item
            if isinstance(columns, str):
                columns = [part.strip() for part in columns.split(",")]
            if not isinstance(columns, list):
                continue
            cleaned = [_clean_sql_identifier(str(col)) for col in columns]
            cleaned = [col for col in cleaned if col]
            if cleaned:
                normalized_indexes.append(cleaned)
        if normalized_indexes:
            normalized[table_name] = normalized_indexes
    return normalized


def _sql_references_index_column(sql: str, column: str) -> bool:
    pattern = _INDEXED_OPERATOR_RE.pattern.format(column=re.escape(_clean_sql_identifier(column)))
    return re.search(pattern, sql or "", flags=re.IGNORECASE) is not None


def _check_sql_index_requirements(sql: str, table_indexes: dict[str, list[list[str]]]) -> list[str]:
    tables = _extract_sql_tables(sql)
    violations: list[str] = []
    for table in sorted(tables):
        indexes = table_indexes.get(table)
        if not indexes:
            continue
        if any(_sql_references_index_column(sql, index_columns[0]) for index_columns in indexes if index_columns):
            continue
        formatted = ", ".join(f"({', '.join(cols)})" for cols in indexes)
        violations.append(
            f"SQL for table `{table}` must filter or join on the leftmost column of an available index: {formatted}"
        )
    return violations


def _dbops_source(resolved: DBOpsResolvedQuery) -> dict[str, Any]:
    return {
        "db_key": resolved.db_key,
        "instance_name": resolved.instance_name,
        "db_name": resolved.db_name,
        "schema_name": resolved.schema_name,
        "tb_name": resolved.tb_name,
        "limit_num": resolved.limit_num,
    }


def _pick_db_config(db_key: str | None) -> dict[str, Any] | None:
    configs = _load_dbops_db_configs()
    if not configs:
        return None
    if db_key:
        for item in configs:
            if item["key"] == db_key:
                return item
        return None
    return configs[0]


def resolve_dbops_query(query_input: DBOpsQueryInput) -> DBOpsResolvedQuery | str:
    if not query_input.sql_content:
        return "sql_content is required"

    selected = _pick_db_config(query_input.db_key)
    if query_input.db_key and selected is None:
        return f"db_key not found in dbops_db_config.json: {query_input.db_key}"
    if selected is None and not query_input.instance_name:
        return (
            "No database config found. Please create ~/.hermes/config/dbops_db_config.json "
            "with a list of database entries, or pass instance_name/db_name in tool args."
        )

    base = selected or {}
    resolved_instance = query_input.instance_name or str(base.get("instance_name", "")).strip()
    resolved_db = query_input.db_name or str(base.get("db_name", "")).strip()
    resolved_schema = query_input.schema_name or str(base.get("schema_name", "")).strip()
    resolved_table = query_input.tb_name or str(base.get("tb_name", "")).strip()
    resolved_limit = _normalize_limit(
        query_input.limit_num, _normalize_limit(base.get("limit_num", 100))
    )
    resolved_db_key = query_input.db_key or str(base.get("key", "")).strip()

    if not resolved_instance:
        return "instance_name is required (from db config or tool args)"
    if not resolved_db:
        return "db_name is required (from db config or tool args)"

    return DBOpsResolvedQuery(
        sql_content=query_input.sql_content,
        db_key=resolved_db_key,
        instance_name=resolved_instance,
        db_name=resolved_db,
        schema_name=resolved_schema,
        tb_name=resolved_table,
        limit_num=resolved_limit,
    )


def _build_dbops_schema_overrides() -> dict[str, Any]:
    configs = _load_dbops_db_configs()
    home = display_hermes_home()
    config_path = f"{home}/config/dbops_db_config.json"
    index_hint_lines: list[str] = []
    for item in configs:
        table_indexes = _normalize_table_indexes(item.get("table_indexes") or item.get("indexes"))
        for table, indexes in sorted(table_indexes.items()):
            formatted = ", ".join(f"({', '.join(cols)})" for cols in indexes)
            index_hint_lines.append(f"{item['key']}.{table}: {formatted}")
    index_hint = ""
    if index_hint_lines:
        index_hint = " Available table indexes: " + "; ".join(index_hint_lines) + "."

    db_key_prop = {
        "type": "string",
        "description": (
            "Database key from dbops_db_config.json. "
            f"Config path: {config_path}. If omitted, defaults to first config entry."
        ),
    }
    if configs:
        db_key_prop["enum"] = [item["key"] for item in configs]

    return {
        "description": (
            "Run SQL query by calling DBOps HTTP endpoint. Cookies are loaded from "
            f"{home}/config/dbops_cookie.json. Database candidates are loaded from "
            f"{config_path}; the model should choose the most suitable db_key. "
            "Execution is disabled by default unless HERMES_DBOPS_EXECUTE_ENABLED=1 "
            "or config.yaml dbops.execute_enabled=true; when disabled, this tool returns "
            "audited SQL only and does not access the online database. "
            "When execution is enabled: ≤20 rows inline table; ≥21 rows local Excel download; "
            ">5000 rows DBOps async export. Pass generation_reason to explain SQL provenance. "
            "Before generating sql_content, check the target table indexes from dbops_db_config.json. "
            "The generated SQL must satisfy index requirements: every indexed table must use at least "
            "one available index's leftmost column in WHERE or JOIN conditions. "
            "If the user request lacks an indexed filter, ask a clarifying question instead of generating a full scan. "
            "On success, copy ``user_display`` verbatim to the user (one summary line + one table with "
            "all rows). Never list rows as 记录1/记录2 field-by-field. See ``agent_instruction``."
            f"{index_hint}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql_content": {
                    "type": "string",
                    "description": (
                        "SQL to execute in DBOps (read-only). Only SELECT and EXPLAIN are allowed; "
                        "must satisfy table index requirements by filtering/joining on an available "
                        "index leftmost column; "
                        "e.g. select * from tbl_term where id = 17660"
                    ),
                },
                "user_id": {
                    "type": "string",
                    "description": "可选，业务用户 ID，供后续 SQL 权限审核使用。",
                },
                "db_key": db_key_prop,
                "instance_name": {
                    "type": "string",
                    "description": "Optional override for instance name.",
                },
                "db_name": {
                    "type": "string",
                    "description": "Optional override for database name.",
                },
                "schema_name": {
                    "type": "string",
                    "description": "Optional override for schema name.",
                },
                "tb_name": {
                    "type": "string",
                    "description": "Optional table context for DBOps.",
                },
                "limit_num": {
                    "type": "integer",
                    "description": "Query row limit, range 1-1000, default 100.",
                    "default": 100,
                },
                "generation_reason": {
                    "type": "string",
                    "description": (
                        "说明本次 SQL 的生成依据（参考了哪些表/字段/业务规则/历史对话等），"
                        "会展示给用户并写入执行记录。"
                    ),
                },
            },
            "required": ["sql_content"],
        },
    }


def check_dbops_requirements() -> bool:
    """Expose tool and ensure runtime config skeleton exists."""
    _ensure_default_cookie_file()
    _ensure_default_db_config_file()
    return True


def _format_generation_reason_block(generation_reason: str | None) -> str:
    if not generation_reason or not str(generation_reason).strip():
        return ""
    return f"\n\n**生成依据：**\n{generation_reason.strip()}\n"


def _format_dbops_sql_only_result(
    resolved: DBOpsResolvedQuery,
    *,
    generation_reason: str | None = None,
) -> str:
    source = _dbops_source(resolved)
    reason_block = _format_generation_reason_block(generation_reason)
    message = (
        "已生成 SQL，DBOps 执行开关关闭，未执行查询，未访问线上库。\n\n"
        f"```sql\n{resolved.sql_content}\n```"
        f"{reason_block}"
    )
    meta = {
        "success": True,
        "executed": False,
        "delivery_mode": "sql_only",
        "generation_reason": generation_reason,
        "status": None,
        "msg": "DBOps execution disabled",
        "row_count": 0,
        "column_count": 0,
        "agent_instruction": (
            "DBOps 执行开关关闭。本次只向用户展示生成的 SQL，"
            "不要编造查询结果或声称已经访问数据库。"
        ),
        "query": {
            "full_sql": resolved.sql_content,
            "query_time": None,
            "affected_rows": None,
            "seconds_behind_master": None,
        },
        "warning": "DBOps execution disabled",
        "error": None,
        "source": source,
    }
    return message + DBOPS_META_MARKER + json.dumps(meta, ensure_ascii=False)


def dbops_query_tool(args: dict[str, Any]) -> str:
    query_input = DBOpsQueryInput.from_args(args)
    resolved = resolve_dbops_query(query_input)
    if isinstance(resolved, str):
        return tool_error(resolved)

    selected_cfg = _pick_db_config(query_input.db_key)
    table_indexes = _normalize_table_indexes(
        (selected_cfg or {}).get("table_indexes") or (selected_cfg or {}).get("indexes")
    )
    index_violations = _check_sql_index_requirements(resolved.sql_content, table_indexes)
    if index_violations:
        return tool_error(
            "SQL index requirement failed: " + "; ".join(index_violations),
            success=False,
            sql_index_audit={
                "passed": False,
                "violations": index_violations,
                "table_indexes": table_indexes,
            },
            source={
                "db_key": resolved.db_key,
                "instance_name": resolved.instance_name,
                "db_name": resolved.db_name,
                "schema_name": resolved.schema_name,
                "tb_name": resolved.tb_name,
                "limit_num": resolved.limit_num,
            },
            query={"full_sql": resolved.sql_content},
        )

    user_id = str(args.get("user_id", "")).strip() or None
    audit = _sql_auditor.audit(
        resolved.sql_content,
        user_id=user_id,
        context={
            "db_key": resolved.db_key,
            "instance_name": resolved.instance_name,
            "db_name": resolved.db_name,
        },
    )
    if not audit.passed:
        return tool_error(
            f"SQL audit failed: {audit.message}",
            success=False,
            sql_audit=audit.to_dict(),
            source=_dbops_source(resolved),
            query={"full_sql": resolved.sql_content},
        )

    generation_reason = str(args.get("generation_reason", "")).strip() or None

    if not is_dbops_execute_enabled():
        return _format_dbops_sql_only_result(resolved, generation_reason=generation_reason)

    cookie_text = _load_dbops_cookie_text()
    if not cookie_text:
        return tool_error("dbops cookie is empty in ~/.hermes/config/dbops_cookie.json")

    _source = _dbops_source(resolved)
    _query_meta = {"full_sql": resolved.sql_content}

    outcome = execute_with_volume_routing(
        resolved,
        cookie_text=cookie_text,
        csrf_token=_extract_csrf_token(cookie_text),
        generation_reason=generation_reason,
    )
    if isinstance(outcome, str):
        return tool_error(
            outcome,
            success=False,
            source=_source,
            query=_query_meta,
        )
    return outcome.text


DBOPS_QUERY_SCHEMA = {
    "name": "dbops_query",
    "description": (
        "通过 DBOps 生成或执行只读 SQL。默认只生成已审核 SQL，不访问线上库；"
        "仅当 HERMES_DBOPS_EXECUTE_ENABLED=1 或 dbops.execute_enabled=true 时执行查询。"
        "成功后必须把 user_display（一行摘要 + 整张表）原样给用户，禁止按「记录1/记录2」逐条写字段。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql_content": {
                "type": "string",
                "description": "SQL to execute in DBOps (SELECT or EXPLAIN only).",
            }
        },
        "required": ["sql_content"],
    },
}


def _handle_dbops_query(args: dict, **_kw) -> str:
    return dbops_query_tool(args)


registry.register(
    name="dbops_query",
    toolset="dbops",
    schema=DBOPS_QUERY_SCHEMA,
    handler=_handle_dbops_query,
    check_fn=check_dbops_requirements,
    dynamic_schema_overrides=_build_dbops_schema_overrides,
    emoji="🗄️",
)
