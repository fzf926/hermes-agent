"""Format DBOps query results as markdown tables for end-user display."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence


def _cell_text(value: Any, *, max_len: int = 300) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("|", "\\|").replace("\n", " ")
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def columns_and_rows_from_records(
    columns: Sequence[Any],
    records: List[dict[str, Any]],
) -> tuple[list[str], list[list[Any]]]:
    col_names = [str(c) for c in columns]
    if not col_names and records:
        col_names = [str(k) for k in records[0].keys()]
    rows: list[list[Any]] = []
    for rec in records:
        rows.append([rec.get(col) for col in col_names])
    return col_names, rows


def format_markdown_table(
    columns: Sequence[Any],
    rows: Sequence[Sequence[Any]],
) -> str:
    """Render columns as header row and all data rows in one markdown table."""
    col_names = [str(c) for c in columns]
    if not col_names:
        return "_（无列信息）_"

    header = "| " + " | ".join(_cell_text(c, max_len=80) for c in col_names) + " |"
    separator = "| " + " | ".join("---" for _ in col_names) + " |"
    if not rows:
        empty_row = "| " + " | ".join("_（无数据）_" if i == 0 else "" for i in col_names) + " |"
        return "\n".join([header, separator, empty_row])

    body_lines = []
    for row in rows:
        cells = []
        for idx, _col in enumerate(col_names):
            val = row[idx] if idx < len(row) else None
            cells.append(_cell_text(val))
        body_lines.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, separator, *body_lines])


def format_dbops_user_display(
    *,
    sql_content: str = "",
    instance_name: str = "",
    db_name: str = "",
    columns: Sequence[Any],
    records: Optional[List[dict[str, Any]]] = None,
    rows: Optional[Sequence[Sequence[Any]]] = None,
    row_count: Optional[int] = None,
    query_time: Optional[float] = None,
    query_label: Optional[str] = None,
    include_query_footer: bool = True,
) -> str:
    """Build user-facing text: one summary line + one markdown table for all rows.

    Multiple result rows are merged into a single table (column headers + data rows).
    Do not format as 「记录 1 / 记录 2」 field lists.
    """
    col_names = [str(c) for c in columns]
    data_rows: list[list[Any]]
    if rows is not None:
        data_rows = [list(r) for r in rows]
    elif records:
        col_names, data_rows = columns_and_rows_from_records(col_names, records)
    else:
        data_rows = []

    count = row_count if row_count is not None else len(data_rows)
    col_count = len(col_names)
    prefix = f"【{query_label}】" if query_label else ""
    if col_count:
        summary = f"{prefix}查询成功，返回 {count} 条记录（共 {col_count} 个字段，见下表）。"
    else:
        summary = f"{prefix}查询成功，返回 {count} 条记录。"
    lines = [summary, "", format_markdown_table(col_names, data_rows)]
    if include_query_footer and (sql_content or instance_name or db_name):
        footer_parts = []
        if instance_name or db_name:
            footer_parts.append(f"实例 `{instance_name or '-'}` / 库 `{db_name or '-'}`")
        if sql_content:
            footer_parts.append(f"SQL: `{sql_content}`")
        if query_time is not None:
            footer_parts.append(f"耗时 {query_time:.4f}s")
        lines.extend(["", f"（{'；'.join(footer_parts)}）"])
    return "\n".join(lines)


def align_rows_to_columns(
    columns: Sequence[Any],
    rows: Sequence[Sequence[Any]],
) -> list[list[Any]]:
    """Pad or trim each row so cell count matches column_list length."""
    n = len(columns)
    if n == 0:
        return [list(r) for r in rows]
    aligned: list[list[Any]] = []
    for row in rows:
        cells = list(row) if isinstance(row, (list, tuple)) else [row]
        if len(cells) < n:
            cells = cells + [None] * (n - len(cells))
        elif len(cells) > n:
            cells = cells[:n]
        aligned.append(cells)
    return aligned


def format_multiple_dbops_displays(sections: List[str]) -> str:
    """Join multiple query result blocks (one per SQL execution)."""
    cleaned = [s.strip() for s in sections if s and s.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return "\n\n---\n\n".join(cleaned)
