"""
Persist Hermes API chat turns to MySQL.

Configure via environment variables (do not commit passwords to git):

  HERMES_MYSQL_ENABLED=1
  HERMES_MYSQL_HOST=...
  HERMES_MYSQL_PORT=3306
  HERMES_MYSQL_USER=root
  HERMES_MYSQL_PASSWORD=...
  HERMES_MYSQL_DATABASE=hermes_agent
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from gateway.favorite_summarizer import (
    get_favorite_summarizer_history_turns,
    summarize_favorite_turn,
)
from gateway.fulfillment_judge import conversation_for_api
from gateway.sql_execution import sql_records_for_turn_api
from gateway.tool_call_log import json_dumps_for_mysql, tool_call_rows_for_turn_api

logger = logging.getLogger(__name__)

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # pragma: no cover
    pymysql = None  # type: ignore[assignment]
    DictCursor = None  # type: ignore[assignment,misc]


def _load_mysql_chat_yaml() -> Dict[str, Any]:
    """Read ``mysql_chat`` section from ~/.hermes/config.yaml."""
    try:
        from hermes_constants import get_hermes_home

        config_path = get_hermes_home() / "config.yaml"
        if not config_path.exists():
            return {}
        import yaml

        with open(config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        section = cfg.get("mysql_chat", {})
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load mysql_chat from config.yaml: %s", exc)
        return {}


def is_mysql_store_enabled() -> bool:
    flag = os.getenv("HERMES_MYSQL_ENABLED", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    yaml_cfg = _load_mysql_chat_yaml()
    if yaml_cfg.get("enabled") is True:
        return True
    if yaml_cfg.get("enabled") is False:
        return False
    # Auto-enable when host is configured (env or config.yaml)
    if os.getenv("HERMES_MYSQL_HOST", "").strip():
        return True
    return bool(str(yaml_cfg.get("host", "")).strip())


@dataclass
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_env(cls) -> "MySQLConfig":
        yaml_cfg = _load_mysql_chat_yaml()

        def _pick(env_key: str, yaml_key: str, default: Any) -> Any:
            env_val = os.getenv(env_key, "").strip()
            if env_val:
                return env_val
            yaml_val = yaml_cfg.get(yaml_key)
            if yaml_val is not None and str(yaml_val).strip() != "":
                return yaml_val
            return default

        return cls(
            host=str(_pick("HERMES_MYSQL_HOST", "host", "127.0.0.1")),
            port=int(_pick("HERMES_MYSQL_PORT", "port", 3306)),
            user=str(_pick("HERMES_MYSQL_USER", "user", "root")),
            password=str(_pick("HERMES_MYSQL_PASSWORD", "password", "")),
            database=str(_pick("HERMES_MYSQL_DATABASE", "database", "hermes_agent")),
        )


def _json_value(value: Any) -> Any:
    """Convert MySQL row values to JSON-serializable types."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row_to_dict(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {key: _json_value(val) for key, val in row.items()}


def _rows_to_dicts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_row_to_dict(row) for row in rows]  # type: ignore[misc]


class ChatMySQLStore:
    """Synchronous MySQL access for chat persistence tables."""

    def __init__(self, config: MySQLConfig):
        if pymysql is None:
            raise RuntimeError("pymysql is required: pip install 'pymysql>=1.1,<2'")
        self._config = config

    @classmethod
    def from_env(cls) -> "ChatMySQLStore":
        return cls(MySQLConfig.from_env())

    def _connect(self):
        return pymysql.connect(
            host=self._config.host,
            port=self._config.port,
            user=self._config.user,
            password=self._config.password,
            database=self._config.database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def _ensure_session(
        self,
        cur,
        *,
        user_id: str,
        hermes_session_id: str,
        channel: str,
        tenant_id: Optional[str] = None,
    ) -> int:
        cur.execute(
            """
            SELECT id FROM chat_session
            WHERE hermes_session_id = %s AND user_id = %s
            LIMIT 1
            """,
            (hermes_session_id, user_id),
        )
        row = cur.fetchone()
        if row:
            return int(row["id"])

        session_uid = uuid.uuid4().hex
        cur.execute(
            """
            INSERT INTO chat_session (
                session_uid, hermes_session_id, user_id, tenant_id, channel, status
            ) VALUES (%s, %s, %s, %s, %s, 1)
            """,
            (session_uid, hermes_session_id, user_id, tenant_id, channel),
        )
        return int(cur.lastrowid)

    def _next_turn_no(self, cur, session_id: int) -> int:
        cur.execute(
            "SELECT COALESCE(MAX(turn_no), 0) + 1 AS n FROM chat_turn WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        return int(row["n"]) if row else 1

    def save_qa_turn(
        self,
        *,
        user_id: str,
        hermes_session_id: str,
        question_text: str,
        answer_text: Optional[str],
        model: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        channel: str = "api_server",
        tenant_id: Optional[str] = None,
        completion_id: Optional[str] = None,
        response_id: Optional[str] = None,
        run_id: Optional[str] = None,
        status: str = "answered",
        error_message: Optional[str] = None,
        fulfillment_status: Optional[str] = None,
        fulfillment_reason: Optional[str] = None,
        is_final: Optional[bool] = None,
    ) -> Dict[str, int]:
        usage = usage or {}
        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion_tokens = int(
            usage.get("output_tokens") or usage.get("completion_tokens") or 0
        )
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

        if status not in {"answered", "timeout", "error", "interrupted"}:
            status = "error" if error_message else "answered"

        fs = (fulfillment_status or "").strip().lower() or None
        if fs and fs not in {"satisfied", "partial", "unsatisfied", "unknown"}:
            fs = "unknown"
        fr = (fulfillment_reason or "")[:512] if fulfillment_reason else None
        is_final_db = None
        if is_final is not None:
            is_final_db = 1 if is_final else 0

        hermes_response_ref = response_id or completion_id

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                session_id = self._ensure_session(
                    cur,
                    user_id=user_id,
                    hermes_session_id=hermes_session_id,
                    channel=channel,
                    tenant_id=tenant_id,
                )
                turn_no = self._next_turn_no(cur, session_id)

                q_uid = uuid.uuid4().hex
                cur.execute(
                    """
                    INSERT INTO chat_message (
                        message_uid, session_id, user_id, tenant_id, turn_no, role,
                        content, model, prompt_tokens, completion_tokens, total_tokens,
                        hermes_response_id, hermes_run_id
                    ) VALUES (%s, %s, %s, %s, %s, 'user', %s, %s, 0, 0, 0, %s, %s)
                    """,
                    (
                        q_uid,
                        session_id,
                        user_id,
                        tenant_id,
                        turn_no,
                        question_text,
                        model,
                        hermes_response_ref,
                        run_id,
                    ),
                )
                question_message_id = int(cur.lastrowid)

                answer_message_id = None
                if answer_text is not None:
                    a_uid = uuid.uuid4().hex
                    cur.execute(
                        """
                        INSERT INTO chat_message (
                            message_uid, session_id, user_id, tenant_id, turn_no, role,
                            content, model, prompt_tokens, completion_tokens, total_tokens,
                            hermes_response_id, hermes_run_id
                        ) VALUES (%s, %s, %s, %s, %s, 'assistant', %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            a_uid,
                            session_id,
                            user_id,
                            tenant_id,
                            turn_no,
                            answer_text,
                            model,
                            prompt_tokens,
                            completion_tokens,
                            total_tokens,
                            hermes_response_ref,
                            run_id,
                        ),
                    )
                    answer_message_id = int(cur.lastrowid)

                cur.execute(
                    """
                    INSERT INTO chat_turn (
                        session_id, user_id, tenant_id, turn_no,
                        question_message_id, answer_message_id,
                        question_text, answer_text, status, error_message,
                        fulfillment_status, fulfillment_reason, is_final
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        session_id,
                        user_id,
                        tenant_id,
                        turn_no,
                        question_message_id,
                        answer_message_id,
                        question_text,
                        answer_text,
                        status,
                        (error_message or "")[:512] if error_message else None,
                        fs,
                        fr,
                        is_final_db,
                    ),
                )
                turn_id = int(cur.lastrowid)

                cur.execute(
                    "UPDATE chat_session SET updated_at = CURRENT_TIMESTAMP(3) WHERE id = %s",
                    (session_id,),
                )
            conn.commit()
            return {"session_id": session_id, "turn_id": turn_id}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_sql_executions(
        self,
        *,
        session_id: int,
        turn_id: Optional[int],
        user_id: str,
        executions: List[Dict[str, Any]],
    ) -> None:
        """Persist SQL executions linked to a chat turn."""
        if not executions:
            return

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                for rec in executions:
                    sql_content = str(rec.get("sql_content") or "").strip()
                    db_name = str(rec.get("database") or rec.get("db_name") or "").strip()
                    instance_name = str(
                        rec.get("instance") or rec.get("instance_name") or ""
                    ).strip()
                    if not sql_content or (not db_name and not instance_name):
                        continue
                    status = rec.get("status", "success")
                    if status not in {"success", "error"}:
                        status = "error" if rec.get("error_message") else "success"
                    err_msg = rec.get("error_message")
                    query_time_ms = rec.get("query_time_ms")
                    row_count = rec.get("row_count")
                    cur.execute(
                        """
                        INSERT INTO chat_sql_execution (
                            session_id, turn_id, user_id, tool_call_id,
                            sql_content, db_name, instance_name, status,
                            error_message, query_time_ms, row_count
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            session_id,
                            turn_id,
                            user_id,
                            (rec.get("tool_call_id") or "")[:128] or None,
                            sql_content,
                            db_name,
                            instance_name,
                            status,
                            (str(err_msg)[:512] if err_msg else None),
                            query_time_ms,
                            int(row_count) if row_count is not None else None,
                        ),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_tool_calls(
        self,
        *,
        session_id: int,
        turn_id: Optional[int],
        user_id: str,
        calls: List[Dict[str, Any]],
    ) -> None:
        """Persist generic tool calls linked to a chat turn."""
        if not calls:
            return

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                for rec in calls:
                    tool_name = str(rec.get("tool_name") or "").strip()[:128]
                    if not tool_name:
                        continue
                    status = str(rec.get("status") or "success").strip().lower()
                    if status not in {"success", "failed", "timeout"}:
                        status = "failed" if rec.get("error") else "success"
                    latency_ms = rec.get("latency_ms")
                    cur.execute(
                        """
                        INSERT INTO chat_tool_call (
                            session_id, turn_id, message_id, user_id,
                            tool_name, tool_args, tool_result, status, latency_ms
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            session_id,
                            turn_id,
                            rec.get("message_id"),
                            user_id,
                            tool_name,
                            json_dumps_for_mysql(rec.get("tool_args")),
                            json_dumps_for_mysql(rec.get("tool_result")),
                            status,
                            int(latency_ms) if latency_ms is not None else None,
                        ),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_sessions_by_user_id(
        self,
        user_id: str,
        *,
        limit: int = 20,
        page: int = 1,
    ) -> Dict[str, Any]:
        """List chat sessions for a user, newest activity first."""
        limit = max(1, min(int(limit), 100))
        page = max(1, int(page))
        offset = (page - 1) * limit

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM chat_session WHERE user_id = %s",
                    (user_id,),
                )
                total = int((cur.fetchone() or {}).get("n", 0))

                cur.execute(
                    """
                    SELECT
                        s.id,
                        s.session_uid,
                        s.hermes_session_id,
                        s.user_id,
                        s.tenant_id,
                        s.channel,
                        s.title,
                        s.status,
                        s.started_at,
                        s.ended_at,
                        s.created_at,
                        s.updated_at,
                        (
                            SELECT COUNT(*)
                            FROM chat_turn t
                            WHERE t.session_id = s.id
                        ) AS turn_count,
                        (
                            SELECT t.question_text
                            FROM chat_turn t
                            WHERE t.session_id = s.id
                            ORDER BY t.turn_no DESC
                            LIMIT 1
                        ) AS last_question
                    FROM chat_session s
                    WHERE s.user_id = %s
                    ORDER BY s.updated_at DESC, s.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, limit, offset),
                )
                sessions = _rows_to_dicts(list(cur.fetchall()))
        finally:
            conn.close()

        return {
            "object": "list",
            "user_id": user_id,
            "total": total,
            "limit": limit,
            "page": page,
            "sessions": sessions,
        }

    def resolve_session(self, session_ref: str) -> Optional[Dict[str, Any]]:
        """Resolve a session by internal id, session_uid, or hermes_session_id."""
        ref = (session_ref or "").strip()
        if not ref:
            return None

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                if ref.isdigit():
                    cur.execute(
                        "SELECT * FROM chat_session WHERE id = %s LIMIT 1",
                        (int(ref),),
                    )
                    row = cur.fetchone()
                    if row:
                        return _row_to_dict(row)

                cur.execute(
                    """
                    SELECT * FROM chat_session
                    WHERE session_uid = %s OR hermes_session_id = %s
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (ref, ref),
                )
                row = cur.fetchone()
                return _row_to_dict(row)
        finally:
            conn.close()

    def list_turns_by_session_ref(self, session_ref: str) -> Dict[str, Any]:
        """List Q&A turns for a session, ordered by turn_no ascending."""
        session = self.resolve_session(session_ref)
        if not session:
            return {
                "object": "list",
                "session": None,
                "turns": [],
                "total": 0,
            }

        session_id = int(session["id"])
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        session_id,
                        user_id,
                        tenant_id,
                        turn_no,
                        question_message_id,
                        answer_message_id,
                        question_text,
                        answer_text,
                        status,
                        error_code,
                        error_message,
                        fulfillment_status,
                        fulfillment_reason,
                        is_final,
                        feedback_score,
                        created_at
                    FROM chat_turn
                    WHERE session_id = %s
                    ORDER BY turn_no ASC, id ASC
                    """,
                    (session_id,),
                )
                turns = _rows_to_dicts(list(cur.fetchall()))

                sql_by_turn: Dict[int, List[Dict[str, Any]]] = {}
                tool_calls_by_turn: Dict[int, List[Dict[str, Any]]] = {}
                if turns:
                    cur.execute(
                        """
                        SELECT
                            id, turn_id, tool_call_id, sql_content, db_name, instance_name,
                            status, error_message, query_time_ms, row_count, created_at
                        FROM chat_sql_execution
                        WHERE session_id = %s
                        ORDER BY turn_id ASC, id ASC
                        """,
                        (session_id,),
                    )
                    for row in cur.fetchall():
                        tid = row.get("turn_id")
                        if tid is None:
                            continue
                        sql_by_turn.setdefault(int(tid), []).append(_row_to_dict(row))

                    cur.execute(
                        """
                        SELECT
                            id, turn_id, tool_name, tool_args, tool_result,
                            status, latency_ms, created_at
                        FROM chat_tool_call
                        WHERE session_id = %s
                        ORDER BY turn_id ASC, id ASC
                        """,
                        (session_id,),
                    )
                    for row in cur.fetchall():
                        tid = row.get("turn_id")
                        if tid is None:
                            continue
                        tool_calls_by_turn.setdefault(int(tid), []).append(_row_to_dict(row))

                for turn in turns:
                    tid = turn.get("id")
                    rows = sql_by_turn.get(int(tid), []) if tid is not None else []
                    turn["sql"] = sql_records_for_turn_api(rows)
                    tool_rows = (
                        tool_calls_by_turn.get(int(tid), []) if tid is not None else []
                    )
                    turn["tool_calls"] = tool_call_rows_for_turn_api(tool_rows)
                    if turn.get("fulfillment_status"):
                        _is_final = turn.get("is_final")
                        turn["conversation"] = conversation_for_api(
                            {
                                "fulfillment_status": turn.get("fulfillment_status"),
                                "fulfillment_reason": turn.get("fulfillment_reason") or "",
                                "is_final": bool(_is_final) if _is_final is not None else True,
                            }
                        )
        finally:
            conn.close()

        return {
            "object": "list",
            "session": session,
            "total": len(turns),
            "turns": turns,
        }

    def _resolve_favorite(self, cur, favorite_ref: str) -> Optional[Dict[str, Any]]:
        ref = (favorite_ref or "").strip()
        if not ref:
            return None
        if ref.isdigit():
            cur.execute(
                """
                SELECT f.*, s.hermes_session_id, s.session_uid
                FROM chat_sql_favorite f
                JOIN chat_session s ON s.id = f.session_id
                WHERE f.id = %s AND f.status = 1
                LIMIT 1
                """,
                (int(ref),),
            )
            row = cur.fetchone()
            if row:
                return _row_to_dict(row)
        cur.execute(
            """
            SELECT f.*, s.hermes_session_id, s.session_uid
            FROM chat_sql_favorite f
            JOIN chat_session s ON s.id = f.session_id
            WHERE f.favorite_uid = %s AND f.status = 1
            LIMIT 1
            """,
            (ref,),
        )
        return _row_to_dict(cur.fetchone())

    def get_turn_context_for_favorite_summary(
        self,
        cur,
        *,
        session_id: int,
        turn_id: int,
        turn_no: int,
        history_turns: int = 5,
    ) -> Dict[str, Any]:
        """Load up to N turns of Q&A ending at turn_no, plus SQL on the bookmarked turn."""
        window = max(1, min(int(history_turns), 10))
        min_turn = max(1, int(turn_no) - window + 1)
        cur.execute(
            """
            SELECT turn_no, question_text, answer_text
            FROM chat_turn
            WHERE session_id = %s AND turn_no >= %s AND turn_no <= %s
            ORDER BY turn_no ASC
            """,
            (session_id, min_turn, turn_no),
        )
        history = []
        for row in cur.fetchall():
            history.append(
                {
                    "turn_no": int(row["turn_no"]),
                    "question_text": str(row.get("question_text") or ""),
                    "answer_text": str(row.get("answer_text") or ""),
                    "is_current": int(row["turn_no"]) == int(turn_no),
                }
            )

        cur.execute(
            """
            SELECT sql_content, db_name, instance_name, status
            FROM chat_sql_execution
            WHERE turn_id = %s
            ORDER BY id ASC
            LIMIT 20
            """,
            (turn_id,),
        )
        sql_rows = [
            {
                "sql_content": str(r.get("sql_content") or ""),
                "database": str(r.get("db_name") or ""),
                "instance": str(r.get("instance_name") or ""),
                "status": str(r.get("status") or ""),
            }
            for r in cur.fetchall()
        ]
        return {"history_turns": history, "sql_executions": sql_rows}

    def _favorite_summary(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row.get("id"),
            "favorite_uid": row.get("favorite_uid"),
            "user_id": row.get("user_id"),
            "session_id": row.get("session_id"),
            "session_uid": row.get("session_uid"),
            "hermes_session_id": row.get("hermes_session_id"),
            "turn_id": row.get("turn_id"),
            "turn_no": row.get("turn_no"),
            "hermes_response_id": row.get("hermes_response_id"),
            "question_summary": row.get("question_summary"),
            "answer_summary": row.get("answer_summary"),
            "fulfillment_status": row.get("fulfillment_status"),
            "fulfillment_reason": row.get("fulfillment_reason"),
            "sql_count": row.get("sql_count", 0),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def create_sql_favorite(
        self,
        *,
        user_id: str,
        hermes_response_id: str,
    ) -> Dict[str, Any]:
        """Favorite SQL from a satisfied turn identified by hermes_response_id."""
        uid = (user_id or "").strip()
        resp_id = (hermes_response_id or "").strip()
        if not uid:
            return {"ok": False, "http_status": 400, "error": "user_id is required"}
        if not resp_id:
            return {"ok": False, "http_status": 400, "error": "hermes_response_id is required"}

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, session_id, turn_no, user_id, role
                    FROM chat_message
                    WHERE hermes_response_id = %s
                    ORDER BY FIELD(role, 'assistant', 'user'), id DESC
                    LIMIT 1
                    """,
                    (resp_id,),
                )
                msg = cur.fetchone()
                if not msg:
                    return {
                        "ok": False,
                        "http_status": 404,
                        "error": f"Message not found for hermes_response_id: {resp_id}",
                    }

                msg_user = str(msg.get("user_id") or "").strip()
                if msg_user and msg_user != uid:
                    return {
                        "ok": False,
                        "http_status": 403,
                        "error": "hermes_response_id does not belong to this user",
                    }

                session_id = int(msg["session_id"])
                turn_no = int(msg["turn_no"])

                cur.execute(
                    """
                    SELECT
                        id, session_id, user_id, turn_no,
                        question_text, answer_text,
                        fulfillment_status, fulfillment_reason, is_final
                    FROM chat_turn
                    WHERE session_id = %s AND turn_no = %s
                    LIMIT 1
                    """,
                    (session_id, turn_no),
                )
                turn = cur.fetchone()
                if not turn:
                    return {
                        "ok": False,
                        "http_status": 404,
                        "error": "Chat turn not found for this response",
                    }

                turn_user = str(turn.get("user_id") or "").strip()
                if turn_user and turn_user != uid:
                    return {
                        "ok": False,
                        "http_status": 403,
                        "error": "Turn does not belong to this user",
                    }

                fulfillment = (turn.get("fulfillment_status") or "").strip().lower()
                if fulfillment != "satisfied":
                    return {
                        "ok": False,
                        "http_status": 400,
                        "error": (
                            "Only turns with fulfillment_status=satisfied can be favorited "
                            f"(current: {fulfillment or 'unknown'})"
                        ),
                    }

                turn_id = int(turn["id"])

                cur.execute(
                    """
                    SELECT f.*, s.hermes_session_id, s.session_uid,
                           (SELECT COUNT(*) FROM chat_sql_favorite_item i
                            WHERE i.favorite_id = f.id) AS sql_count
                    FROM chat_sql_favorite f
                    JOIN chat_session s ON s.id = f.session_id
                    WHERE f.user_id = %s AND f.hermes_response_id = %s AND f.status = 1
                    LIMIT 1
                    """,
                    (uid, resp_id),
                )
                existing = cur.fetchone()
                if existing:
                    conn.commit()
                    fav = _row_to_dict(existing)
                    return {
                        "ok": True,
                        "created": False,
                        "favorite": self._favorite_summary(fav),
                    }

                cur.execute(
                    """
                    SELECT id
                    FROM chat_sql_execution
                    WHERE turn_id = %s
                    ORDER BY id ASC
                    """,
                    (turn_id,),
                )
                sql_rows = list(cur.fetchall())
                if not sql_rows:
                    return {
                        "ok": False,
                        "http_status": 400,
                        "error": "No SQL executions found for this turn",
                    }

                ctx = self.get_turn_context_for_favorite_summary(
                    cur,
                    session_id=session_id,
                    turn_id=turn_id,
                    turn_no=turn_no,
                    history_turns=get_favorite_summarizer_history_turns(),
                )
                summaries = summarize_favorite_turn(
                    question_text=str(turn.get("question_text") or ""),
                    answer_text=str(turn.get("answer_text") or ""),
                    history_turns=ctx.get("history_turns"),
                    sql_executions=ctx.get("sql_executions"),
                )

                favorite_uid = uuid.uuid4().hex
                cur.execute(
                    """
                    INSERT INTO chat_sql_favorite (
                        favorite_uid, user_id, session_id, turn_id, turn_no,
                        hermes_response_id, question_summary, answer_summary,
                        fulfillment_status, fulfillment_reason, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        favorite_uid,
                        uid,
                        session_id,
                        turn_id,
                        turn_no,
                        resp_id,
                        summaries.get("question_summary"),
                        summaries.get("answer_summary"),
                        turn.get("fulfillment_status"),
                        turn.get("fulfillment_reason"),
                    ),
                )
                favorite_id = int(cur.lastrowid)

                for idx, sql_row in enumerate(sql_rows):
                    cur.execute(
                        """
                        INSERT INTO chat_sql_favorite_item (
                            favorite_id, sql_execution_id, sort_order
                        ) VALUES (%s, %s, %s)
                        """,
                        (favorite_id, int(sql_row["id"]), idx),
                    )

                cur.execute(
                    """
                    SELECT f.*, s.hermes_session_id, s.session_uid,
                           %s AS sql_count
                    FROM chat_sql_favorite f
                    JOIN chat_session s ON s.id = f.session_id
                    WHERE f.id = %s
                    LIMIT 1
                    """,
                    (len(sql_rows), favorite_id),
                )
                created = _row_to_dict(cur.fetchone())
            conn.commit()
            return {
                "ok": True,
                "created": True,
                "favorite": self._favorite_summary(created),
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_sql_favorites_by_user(
        self,
        user_id: str,
        *,
        limit: int = 20,
        page: int = 1,
    ) -> Dict[str, Any]:
        """List active SQL favorites for a user."""
        uid = (user_id or "").strip()
        offset = (max(1, page) - 1) * limit

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM chat_sql_favorite WHERE user_id = %s AND status = 1",
                    (uid,),
                )
                total = int((cur.fetchone() or {}).get("n") or 0)

                cur.execute(
                    """
                    SELECT f.*, s.hermes_session_id, s.session_uid,
                           (SELECT COUNT(*) FROM chat_sql_favorite_item i
                            WHERE i.favorite_id = f.id) AS sql_count
                    FROM chat_sql_favorite f
                    JOIN chat_session s ON s.id = f.session_id
                    WHERE f.user_id = %s AND f.status = 1
                    ORDER BY f.created_at DESC, f.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (uid, limit, offset),
                )
                rows = _rows_to_dicts(list(cur.fetchall()))
        finally:
            conn.close()

        favorites = [self._favorite_summary(row) for row in rows]
        return {
            "object": "list",
            "user_id": uid,
            "total": total,
            "limit": limit,
            "page": page,
            "favorites": favorites,
        }

    def list_sql_favorite_sql(self, favorite_ref: str, *, user_id: str) -> Dict[str, Any]:
        """Return SQL execution details linked to a favorite."""
        uid = (user_id or "").strip()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                favorite = self._resolve_favorite(cur, favorite_ref)
                if not favorite:
                    return {
                        "ok": False,
                        "http_status": 404,
                        "error": f"Favorite not found: {favorite_ref}",
                    }

                fav_user = str(favorite.get("user_id") or "").strip()
                if fav_user and uid and fav_user != uid:
                    return {
                        "ok": False,
                        "http_status": 403,
                        "error": "Favorite does not belong to this user",
                    }

                favorite_id = int(favorite["id"])
                cur.execute(
                    """
                    SELECT
                        e.id, e.turn_id, e.tool_call_id, e.sql_content, e.db_name,
                        e.instance_name, e.status, e.error_message,
                        e.query_time_ms, e.row_count, e.created_at,
                        i.sort_order
                    FROM chat_sql_favorite_item i
                    JOIN chat_sql_execution e ON e.id = i.sql_execution_id
                    WHERE i.favorite_id = %s
                    ORDER BY i.sort_order ASC, i.id ASC
                    """,
                    (favorite_id,),
                )
                sql_rows = _rows_to_dicts(list(cur.fetchall()))
        finally:
            conn.close()

        return {
            "ok": True,
            "favorite": self._favorite_summary(favorite),
            "sql": sql_records_for_turn_api(sql_rows),
            "total": len(sql_rows),
        }
