"""DBOps query input / resolved target models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def normalize_limit(value: Any, default: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 1), 1000)


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
            limit_num=normalize_limit(args.get("limit_num", 100)),
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
