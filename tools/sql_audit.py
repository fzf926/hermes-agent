"""SQL audit for DBOps and other database query entry points.

Direction 1 (implemented): compliance — statement type and safety rules.
方向 2（预留）：权限审核 — 校验用户是否有权在目标实例/库上执行 SQL。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Sequence


class SqlAuditPhase(str, Enum):
    COMPLIANCE = "compliance"  # 合规审核
    PERMISSION = "permission"  # 权限审核


@dataclass(frozen=True)
class SqlAuditViolation:
    phase: SqlAuditPhase
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "phase": self.phase.value,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class SqlAuditResult:
    passed: bool
    violations: List[SqlAuditViolation] = field(default_factory=list)
    normalized_sql: str = ""

    @property
    def message(self) -> str:
        if self.passed:
            return "SQL audit passed"
        return "; ".join(v.message for v in self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "normalized_sql": self.normalized_sql,
        }


class SqlAuditChecker(ABC):
    """Base class for a single audit dimension (compliance, permission, ...)."""

    phase: SqlAuditPhase

    @abstractmethod
    def check(
        self,
        sql: str,
        *,
        user_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> SqlAuditResult:
        """Return passed=True when this dimension approves the SQL."""


# Word-boundary patterns applied after comments/strings are stripped.
_FORBIDDEN_KEYWORDS: Sequence[tuple[str, str]] = (
    (r"\bINSERT\b", "insert_not_allowed"),
    (r"\bUPDATE\b", "update_not_allowed"),
    (r"\bDELETE\b", "delete_not_allowed"),
    (r"\bDROP\b", "drop_not_allowed"),
    (r"\bCREATE\b", "create_not_allowed"),
    (r"\bALTER\b", "alter_not_allowed"),
    (r"\bTRUNCATE\b", "truncate_not_allowed"),
    (r"\bREPLACE\b", "replace_not_allowed"),
    (r"\bMERGE\b", "merge_not_allowed"),
    (r"\bCALL\b", "call_not_allowed"),
    (r"\bEXECUTE\b", "execute_not_allowed"),
    (r"\bEXEC\b", "exec_not_allowed"),
    (r"\bGRANT\b", "grant_not_allowed"),
    (r"\bREVOKE\b", "revoke_not_allowed"),
    (r"\bLOAD\s+DATA\b", "load_data_not_allowed"),
    (r"\bLOAD\s+FILE\b", "load_file_not_allowed"),
    (r"\bINTO\s+OUTFILE\b", "into_outfile_not_allowed"),
    (r"\bINTO\s+DUMPFILE\b", "into_dumpfile_not_allowed"),
    (r"\bLOCK\s+TABLES\b", "lock_tables_not_allowed"),
    (r"\bUNLOCK\s+TABLES\b", "unlock_tables_not_allowed"),
    (r"\bHANDLER\b", "handler_not_allowed"),
    (r"\bPREPARE\b", "prepare_not_allowed"),
    (r"\bDEALLOCATE\b", "deallocate_not_allowed"),
    (r"\bKILL\b", "kill_not_allowed"),
    (r"\bSHUTDOWN\b", "shutdown_not_allowed"),
    (r"\bSELECT\b[\s\S]*?\bINTO\b", "select_into_not_allowed"),
    (r"\bFOR\s+UPDATE\b", "for_update_not_allowed"),
    (r"\bLOCK\s+IN\s+SHARE\s+MODE\b", "lock_in_share_mode_not_allowed"),
)

_COMMENT_LINE_RE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_RE = re.compile(
    r"('(?:''|[^'])*')|(\"(?:\"\"|[^\"])*\")|`(?:``|[^`])*`",
    re.DOTALL,
)


def _strip_comments(sql: str) -> str:
    without_block = _COMMENT_BLOCK_RE.sub(" ", sql)
    return _COMMENT_LINE_RE.sub(" ", without_block)


def _strip_string_literals(sql: str) -> str:
    return _STRING_RE.sub("''", sql)


def _normalize_for_audit(sql: str) -> str:
    text = _strip_comments(sql)
    text = _strip_string_literals(text)
    return re.sub(r"\s+", " ", text).strip()


def _split_statements(sql: str) -> list[str]:
    """Split on semicolons outside quoted strings."""
    parts: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    in_backtick = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double and not in_backtick:
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                current.append("''")
                i += 2
                continue
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single and not in_backtick:
            if in_double and i + 1 < len(sql) and sql[i + 1] == '"':
                current.append('""')
                i += 2
                continue
            in_double = not in_double
            current.append(ch)
        elif ch == "`" and not in_single and not in_double:
            if in_backtick and i + 1 < len(sql) and sql[i + 1] == "`":
                current.append("``")
                i += 2
                continue
            in_backtick = not in_backtick
            current.append(ch)
        elif ch == ";" and not in_single and not in_double and not in_backtick:
            stmt = "".join(current).strip()
            if stmt:
                parts.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _statement_root_type(statement: str) -> tuple[Optional[str], Optional[str]]:
    """Return (kind, error_message). kind is 'select' or 'explain'."""
    normalized = _normalize_for_audit(statement)
    if not normalized:
        return None, "SQL statement is empty"
    upper = normalized.upper()
    if upper.startswith("SELECT ") or upper == "SELECT":
        return "select", None
    if upper.startswith("EXPLAIN "):
        remainder = normalized[8:].lstrip()
        rem_upper = remainder.upper()
        if rem_upper.startswith("ANALYZE "):
            remainder = remainder[8:].lstrip()
            rem_upper = remainder.upper()
        if rem_upper.startswith("SELECT ") or rem_upper == "SELECT":
            return "explain", None
        return None, "EXPLAIN is only allowed before SELECT"
    return None, "Only SELECT and EXPLAIN statements are allowed"


class SqlComplianceChecker(SqlAuditChecker):
    """Direction 1: SQL syntax / safety compliance (read-only, single statement)."""

    phase = SqlAuditPhase.COMPLIANCE

    def check(
        self,
        sql: str,
        *,
        user_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> SqlAuditResult:
        del user_id, context
        raw = (sql or "").strip()
        if not raw:
            return SqlAuditResult(
                passed=False,
                violations=[
                    SqlAuditViolation(
                        SqlAuditPhase.COMPLIANCE,
                        "empty_sql",
                        "sql_content cannot be empty",
                    )
                ],
            )

        statements = _split_statements(raw)
        if len(statements) > 1:
            return SqlAuditResult(
                passed=False,
                violations=[
                    SqlAuditViolation(
                        SqlAuditPhase.COMPLIANCE,
                        "multiple_statements",
                        "Multiple SQL statements are not allowed",
                    )
                ],
            )

        statement = statements[0]
        kind, err = _statement_root_type(statement)
        if kind is None:
            return SqlAuditResult(
                passed=False,
                violations=[
                    SqlAuditViolation(
                        SqlAuditPhase.COMPLIANCE,
                        "statement_type_not_allowed",
                        err or "Statement type not allowed",
                    )
                ],
            )

        normalized = _normalize_for_audit(statement)
        violations: list[SqlAuditViolation] = []
        for pattern, code in _FORBIDDEN_KEYWORDS:
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                violations.append(
                    SqlAuditViolation(
                        SqlAuditPhase.COMPLIANCE,
                        code,
                        _violation_message_for_code(code),
                    )
                )

        if violations:
            return SqlAuditResult(passed=False, violations=violations, normalized_sql=normalized)

        return SqlAuditResult(passed=True, normalized_sql=normalized)


def _violation_message_for_code(code: str) -> str:
    messages = {
        "insert_not_allowed": "INSERT statements are not allowed",
        "update_not_allowed": "UPDATE statements are not allowed",
        "delete_not_allowed": "DELETE statements are not allowed",
        "drop_not_allowed": "DROP statements are not allowed",
        "create_not_allowed": "CREATE statements are not allowed",
        "alter_not_allowed": "ALTER statements are not allowed",
        "truncate_not_allowed": "TRUNCATE statements are not allowed",
        "replace_not_allowed": "REPLACE statements are not allowed",
        "merge_not_allowed": "MERGE statements are not allowed",
        "call_not_allowed": "CALL statements are not allowed",
        "execute_not_allowed": "EXECUTE statements are not allowed",
        "exec_not_allowed": "EXEC statements are not allowed",
        "grant_not_allowed": "GRANT statements are not allowed",
        "revoke_not_allowed": "REVOKE statements are not allowed",
        "load_data_not_allowed": "LOAD DATA is not allowed",
        "load_file_not_allowed": "LOAD FILE is not allowed",
        "into_outfile_not_allowed": "INTO OUTFILE is not allowed",
        "into_dumpfile_not_allowed": "INTO DUMPFILE is not allowed",
        "lock_tables_not_allowed": "LOCK TABLES is not allowed",
        "unlock_tables_not_allowed": "UNLOCK TABLES is not allowed",
        "handler_not_allowed": "HANDLER statements are not allowed",
        "prepare_not_allowed": "PREPARE statements are not allowed",
        "deallocate_not_allowed": "DEALLOCATE statements are not allowed",
        "kill_not_allowed": "KILL statements are not allowed",
        "shutdown_not_allowed": "SHUTDOWN statements are not allowed",
        "select_into_not_allowed": "SELECT INTO is not allowed",
        "for_update_not_allowed": "FOR UPDATE is not allowed",
        "lock_in_share_mode_not_allowed": "LOCK IN SHARE MODE is not allowed",
    }
    return messages.get(code, "SQL violates read-only policy")


class SqlPermissionChecker(SqlAuditChecker):
    """方向 2（预留）：校验用户是否有权在指定实例/库上执行 SQL。"""

    phase = SqlAuditPhase.PERMISSION

    def check(
        self,
        sql: str,
        *,
        user_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> SqlAuditResult:
        del sql, user_id, context
        # 预留入口：权限规则尚未实现，当前一律放行。
        return SqlAuditResult(passed=True)


class SqlAuditor:
    """Runs registered SQL audit checkers in order; stops at first failure."""

    def __init__(self, checkers: Optional[Sequence[SqlAuditChecker]] = None) -> None:
        self._checkers: list[SqlAuditChecker] = list(
            checkers
            if checkers is not None
            else (SqlComplianceChecker(), SqlPermissionChecker())
        )

    def register(self, checker: SqlAuditChecker) -> None:
        self._checkers.append(checker)

    def audit(
        self,
        sql: str,
        *,
        user_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> SqlAuditResult:
        ctx = dict(context or {})
        if user_id and "user_id" not in ctx:
            ctx["user_id"] = user_id
        last_normalized = ""
        for checker in self._checkers:
            result = checker.check(sql, user_id=user_id, context=ctx)
            if result.normalized_sql:
                last_normalized = result.normalized_sql
            if not result.passed:
                if not result.normalized_sql and last_normalized:
                    result.normalized_sql = last_normalized
                return result
        return SqlAuditResult(passed=True, normalized_sql=last_normalized)
