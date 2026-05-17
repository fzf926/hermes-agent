#!/usr/bin/env python3
"""DBOps SQL query tool via HTTP endpoint."""

from __future__ import annotations

import json
import logging
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from hermes_constants import display_hermes_home, get_hermes_home
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

DBOPS_QUERY_URL = "https://dbops.codemao.cn/query/"
COOKIE_KEYS = (
    "csrftoken",
    "cluouser_name",
    "admin-authorization",
    "internal_account_token",
)


def _dbops_config_dir() -> Path:
    return get_hermes_home() / "config"


def get_dbops_cookie_path() -> Path:
    return _dbops_config_dir() / "dbops_cookie.json"


def get_dbops_db_config_path() -> Path:
    return _dbops_config_dir() / "dbops_db_config.json"


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


def _normalize_limit(value: Any, default: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 1), 1000)


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
                "limit_num": _normalize_limit(item.get("limit_num", 100)),
            }
        )
    return valid


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


@dataclass
class DBOpsQueryInput:
    sql_content: str
    db_key: str = ""
    instance_name: str = ""
    db_name: str = ""
    schema_name: str = ""
    tb_name: str = ""
    limit_num: int = 100

    @classmethod
    def from_args(cls, args: dict[str, Any]) -> "DBOpsQueryInput":
        return cls(
            sql_content=str(args.get("sql_content", "")).strip(),
            db_key=str(args.get("db_key", "")).strip(),
            instance_name=str(args.get("instance_name", "")).strip(),
            db_name=str(args.get("db_name", "")).strip(),
            schema_name=str(args.get("schema_name", "")).strip(),
            tb_name=str(args.get("tb_name", "")).strip(),
            limit_num=_normalize_limit(args.get("limit_num", 100)),
        )


@dataclass
class DBOpsResolvedQuery:
    sql_content: str
    db_key: str
    instance_name: str
    db_name: str
    schema_name: str
    tb_name: str
    limit_num: int

    @classmethod
    def resolve(cls, query_input: DBOpsQueryInput) -> "DBOpsResolvedQuery | str":
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

        return cls(
            sql_content=query_input.sql_content,
            db_key=resolved_db_key,
            instance_name=resolved_instance,
            db_name=resolved_db,
            schema_name=resolved_schema,
            tb_name=resolved_table,
            limit_num=resolved_limit,
        )


def _format_records(columns: list[Any], rows: list[Any]) -> list[dict[str, Any]]:
    if not columns or not rows:
        return []
    return [
        {str(col): row[idx] if idx < len(row) else None for idx, col in enumerate(columns)}
        for row in rows
        if isinstance(row, list)
    ]


def _decode_http_body(raw_bytes: bytes, content_encoding: str) -> str:
    """Decode HTTP body with transparent gzip support."""
    body = raw_bytes or b""
    encoding = (content_encoding or "").lower()
    try:
        if "gzip" in encoding:
            body = gzip.decompress(body)
        elif body.startswith(b"\x1f\x8b"):
            # Some upstreams forget Content-Encoding, but payload is still gzipped.
            body = gzip.decompress(body)
    except Exception:
        # Fall back to raw bytes decode so caller can inspect response_preview.
        pass
    return body.decode("utf-8", errors="replace")


def _build_dbops_schema_overrides() -> dict[str, Any]:
    configs = _load_dbops_db_configs()
    home = display_hermes_home()
    config_path = f"{home}/config/dbops_db_config.json"

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
            f"{config_path}; the model should choose the most suitable db_key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql_content": {
                    "type": "string",
                    "description": "SQL to execute in DBOps, e.g. select * from tbl_term where id = 17660",
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
            },
            "required": ["sql_content"],
        },
    }


def check_dbops_requirements() -> bool:
    """Expose tool and ensure runtime config skeleton exists."""
    _ensure_default_cookie_file()
    _ensure_default_db_config_file()
    return True


def dbops_query_tool(args: dict[str, Any]) -> str:
    query_input = DBOpsQueryInput.from_args(args)
    resolved = DBOpsResolvedQuery.resolve(query_input)
    if isinstance(resolved, str):
        return tool_error(resolved)

    cookie_text = _load_dbops_cookie_text()
    if not cookie_text:
        return tool_error("dbops cookie is empty in ~/.hermes/config/dbops_cookie.json")

    payload = {
        "instance_name": resolved.instance_name,
        "db_name": resolved.db_name,
        "schema_name": resolved.schema_name,
        "tb_name": resolved.tb_name,
        "sql_content": resolved.sql_content,
        "limit_num": str(resolved.limit_num),
    }
    body = urlencode(payload).encode("utf-8")
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://dbops.codemao.cn",
        "Referer": "https://dbops.codemao.cn/sqlquery/",
        "Cookie": cookie_text,
        "X-CSRFToken": _extract_csrf_token(cookie_text),
    }

    _source = {
        "db_key": resolved.db_key,
        "instance_name": resolved.instance_name,
        "db_name": resolved.db_name,
        "schema_name": resolved.schema_name,
        "tb_name": resolved.tb_name,
        "limit_num": resolved.limit_num,
    }
    _query_meta = {"full_sql": resolved.sql_content}

    request = Request(DBOPS_QUERY_URL, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            raw_bytes = response.read()
            raw_text = _decode_http_body(
                raw_bytes=raw_bytes,
                content_encoding=response.headers.get("Content-Encoding", ""),
            )
    except HTTPError as exc:
        return tool_error(
            f"DBOps HTTP error: {exc.code} {exc.reason}",
            success=False,
            source=_source,
            query=_query_meta,
        )
    except URLError as exc:
        return tool_error(
            f"DBOps request failed: {exc.reason}",
            success=False,
            source=_source,
            query=_query_meta,
        )
    except Exception as exc:
        return tool_error(
            f"DBOps request failed: {exc}",
            success=False,
            source=_source,
            query=_query_meta,
        )

    try:
        parsed = json.loads(raw_text)
    except Exception:
        return tool_error(
            "DBOps returned non-JSON response",
            success=False,
            response_preview=raw_text[:500],
            source=_source,
            query=_query_meta,
        )

    status = parsed.get("status")
    msg = parsed.get("msg")
    data = parsed.get("data") or {}
    if status != 0:
        return tool_error(
            f"DBOps query failed: {msg or 'unknown error'}",
            success=False,
            status=status,
            data_error=data.get("error"),
            warning=data.get("warning"),
            source=_source,
            query=_query_meta,
        )

    columns = data.get("column_list") if isinstance(data.get("column_list"), list) else []
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    records = _format_records(columns, rows)
    return tool_result(
        success=True,
        status=status,
        msg=msg,
        query={
            "full_sql": data.get("full_sql"),
            "query_time": data.get("query_time"),
            "affected_rows": data.get("affected_rows"),
            "seconds_behind_master": data.get("seconds_behind_master"),
        },
        columns=columns,
        row_count=len(rows),
        records=records,
        warning=data.get("warning"),
        error=data.get("error"),
        source={
            "db_key": resolved.db_key,
            "instance_name": resolved.instance_name,
            "db_name": resolved.db_name,
            "schema_name": resolved.schema_name,
            "tb_name": resolved.tb_name,
            "limit_num": resolved.limit_num,
        },
    )


DBOPS_QUERY_SCHEMA = {
    "name": "dbops_query",
    "description": "Run SQL query by calling DBOps HTTP endpoint.",
    "parameters": {
        "type": "object",
        "properties": {
            "sql_content": {
                "type": "string",
                "description": "SQL to execute in DBOps.",
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
