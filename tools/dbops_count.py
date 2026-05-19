"""Build COUNT / paginated SQL wrappers for DBOps."""

from __future__ import annotations


def build_count_sql(sql_content: str) -> str:
    inner = (sql_content or "").strip().rstrip(";")
    return f"SELECT COUNT(*) AS cnt FROM ({inner}) AS _hermes_cnt"


def build_paginated_sql(sql_content: str, *, offset: int, limit: int) -> str:
    inner = (sql_content or "").strip().rstrip(";")
    safe_offset = max(0, int(offset))
    safe_limit = max(1, int(limit))
    return f"SELECT * FROM ({inner}) AS _hermes_page LIMIT {safe_limit} OFFSET {safe_offset}"
