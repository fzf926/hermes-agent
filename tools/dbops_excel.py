"""Write Hermes-local Excel exports for medium result sets."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def write_query_excel(
    path: Path,
    *,
    columns: list[Any],
    rows: list[list[Any]],
) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl is required for Excel delivery (pip install openpyxl)"
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title="data")
    ws.append([str(c) for c in columns])
    for row in rows:
        if isinstance(row, list):
            ws.append([row[i] if i < len(row) else None for i in range(len(columns))])
        else:
            ws.append([row])
    wb.save(path)
