"""Conversation mode helpers for chat / responses / runs APIs.

Modes:
  1 — history (default): existing session + message history behavior
  2 — favorite: inject SQL from a saved favorite into the agent system context
  3 — direct: user supplies SQL; agent executes it (body ``sql`` or SQL-like message)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

CONVERSATION_TYPE_HISTORY = 1
CONVERSATION_TYPE_FAVORITE = 2
CONVERSATION_TYPE_DIRECT = 3

_VALID_TYPES = frozenset(
    {CONVERSATION_TYPE_HISTORY, CONVERSATION_TYPE_FAVORITE, CONVERSATION_TYPE_DIRECT}
)

# Shown when conversation_type=3 but no SQL was provided (no agent run).
DIRECT_SQL_MISSING_REPLY = (
    "当前为**直查 SQL** 模式。请先提供要执行的实际 SQL 语句。\n\n"
    "您可以在请求 body 中设置 `sql` 字段，或在消息中直接粘贴完整 SQL（支持 ```sql ... ``` 代码块）。\n\n"
    "收到 SQL 后，我会执行查询并为您解读结果。"
)

_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.IGNORECASE)
_SQL_START_RE = re.compile(
    r"(?is)^\s*(?:with\b|select\b|show\b|describe\b|desc\b|explain\b)",
)
_MAX_SQL_LEN = 8000


class ConversationContextResult(NamedTuple):
    """Outcome of resolving conversation_type for one request."""

    extra_prompt: Optional[str]
    early_reply: Optional[str]
    error_response: Any  # aiohttp web.Response or None


def parse_conversation_type(body: Optional[Dict[str, Any]] = None) -> int:
    """Resolve conversation type from request body; default 1 (history)."""
    body = body or {}

    for key in ("conversation_type", "conversationType"):
        val = body.get(key)
        if val is not None:
            try:
                value = int(val)
                if value in _VALID_TYPES:
                    return value
            except (TypeError, ValueError):
                pass

    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        for key in ("conversation_type", "conversationType"):
            val = metadata.get(key)
            if val is not None:
                try:
                    value = int(val)
                    if value in _VALID_TYPES:
                        return value
                except (TypeError, ValueError):
                    pass

    return CONVERSATION_TYPE_HISTORY


def parse_favorite_id(body: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve favorite id (favorite_uid or numeric id) from request body."""
    body = body or {}

    for key in ("favorite_id", "favoriteId"):
        val = body.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()[:128]

    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        for key in ("favorite_id", "favoriteId"):
            val = metadata.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()[:128]

    return None


def _history_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return " ".join(parts)
    return str(content) if content is not None else ""


def extract_sql_from_text(text: str) -> Optional[str]:
    """Return SQL extracted from plain text or a fenced ```sql block."""
    text = (text or "").strip()
    if not text:
        return None

    fence = _SQL_FENCE_RE.search(text)
    if fence:
        inner = fence.group(1).strip()
        if inner:
            return inner[:_MAX_SQL_LEN]

    if _SQL_START_RE.match(text):
        return text[:_MAX_SQL_LEN]

    return None


def message_looks_like_sql(text: str) -> bool:
    return extract_sql_from_text(text) is not None


def parse_direct_sql(
    body: Optional[Dict[str, Any]],
    user_message_text: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Resolve SQL for direct-query mode: body field, then current message, then history."""
    body = body or {}

    for key in ("sql", "direct_sql", "query_sql", "querySql"):
        val = body.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()[:_MAX_SQL_LEN]

    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        for key in ("sql", "direct_sql", "query_sql", "querySql"):
            val = metadata.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()[:_MAX_SQL_LEN]

    sql = extract_sql_from_text(user_message_text)
    if sql:
        return sql

    for msg in reversed(conversation_history or []):
        if str(msg.get("role", "")).lower() != "user":
            continue
        sql = extract_sql_from_text(_history_content_to_text(msg.get("content")))
        if sql:
            return sql

    return None


def merge_ephemeral_system_prompt(
    base: Optional[str],
    extra: Optional[str],
) -> Optional[str]:
    if not extra:
        return base
    if not base:
        return extra
    return f"{base.rstrip()}\n\n{extra}"


def build_favorite_system_context(
    favorite: Dict[str, Any],
    sql_list: List[Dict[str, Any]],
) -> str:
    """Build system-prompt block for favorite-based follow-up chat."""
    fav_uid = favorite.get("favorite_uid") or favorite.get("id") or "?"
    question = (favorite.get("question_summary") or "").strip()
    answer = (favorite.get("answer_summary") or "").strip()

    lines = [
        "## SQL bookmark context",
        (
            "The user is starting or continuing a conversation based on a saved SQL "
            f"bookmark (favorite_id={fav_uid}). Treat the saved question/answer summaries "
            "and SQL below as the starting point for follow-up questions."
        ),
    ]
    if question:
        lines.append(f"\n**Bookmarked question (summary):** {question}")
    if answer:
        lines.append(f"\n**Bookmarked answer (summary):** {answer}")

    if sql_list:
        lines.append("\n**Saved SQL executions (in order):**")
        for idx, row in enumerate(sql_list, start=1):
            sql_text = (row.get("sql_content") or row.get("sql") or "").strip()
            db_name = (row.get("database") or row.get("db_name") or "").strip()
            instance = (row.get("instance") or row.get("instance_name") or "").strip()
            status = (row.get("status") or "").strip()
            meta_parts = [p for p in (instance, db_name, status) if p]
            meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
            if sql_text:
                lines.append(f"\n{idx}.{meta}\n```sql\n{sql_text}\n```")
            else:
                lines.append(f"\n{idx}.{meta} (no SQL text recorded)")
    else:
        lines.append("\n(No SQL rows linked to this bookmark.)")

    lines.append(
        "\nWhen the user asks follow-up questions, prefer reusing or adapting these "
        "queries rather than inventing unrelated SQL."
    )
    return "\n".join(lines)


def build_direct_sql_system_context(sql: str) -> str:
    """Build system-prompt block for direct SQL execution mode."""
    sql = (sql or "").strip()
    return (
        "## Direct SQL query mode\n"
        "The user is in **direct SQL** mode. They have provided SQL to run.\n"
        "1. Execute the SQL below with the `dbops_query` tool. Do not substitute a "
        "different query unless the user explicitly asks to modify it.\n"
        "2. Present results clearly (tables, counts, errors).\n"
        "3. If the user's latest message is only a follow-up question (no new SQL), "
        "answer using the SQL and results from this thread.\n\n"
        f"**SQL to execute:**\n```sql\n{sql}\n```"
    )


def load_favorite_system_context(
    store: Any,
    *,
    favorite_ref: str,
    user_id: Optional[str],
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Load favorite SQL and return (context_text, error_payload)."""
    result = store.list_sql_favorite_sql(favorite_ref, user_id=user_id or "")
    if not result.get("ok"):
        status = int(result.get("http_status") or 404)
        return None, {
            "message": result.get("error") or "Favorite not found",
            "type": "invalid_request_error" if status == 400 else "not_found_error",
            "code": None,
            "http_status": status,
        }

    favorite = result.get("favorite") or {}
    sql_list = result.get("sql") or []
    return build_favorite_system_context(favorite, sql_list), None


def _resolve_user_id_from_request(request: Any, body: Dict[str, Any]) -> Optional[str]:
    for key in ("user_id", "userId"):
        val = body.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()[:64]
    for header in ("X-Hermes-User-Id", "X-User-Id"):
        raw = (request.headers.get(header) or "").strip()
        if raw and not re.search(r"[\r\n\x00]", raw):
            return raw[:64]
    return None


def resolve_conversation_context(
    request: Any,
    body: Optional[Dict[str, Any]],
    *,
    user_message_text: str = "",
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    mysql_store: Any,
    openai_error_factory: Any,
) -> ConversationContextResult:
    """Resolve conversation mode: extra system prompt, early reply, or HTTP error."""
    from aiohttp import web

    body = body or {}
    conv_type = parse_conversation_type(body)

    if conv_type == CONVERSATION_TYPE_HISTORY:
        return ConversationContextResult(None, None, None)

    if conv_type == CONVERSATION_TYPE_DIRECT:
        direct_sql = parse_direct_sql(body, user_message_text, conversation_history)
        if not direct_sql:
            return ConversationContextResult(None, DIRECT_SQL_MISSING_REPLY, None)
        return ConversationContextResult(build_direct_sql_system_context(direct_sql), None, None)

    if conv_type != CONVERSATION_TYPE_FAVORITE:
        err = openai_error_factory(
            "Invalid conversation_type; use 1 (history), 2 (favorite), or 3 (direct)",
        )
        return ConversationContextResult(None, None, web.json_response(err, status=400))

    favorite_id = parse_favorite_id(body)
    if not favorite_id:
        err = openai_error_factory(
            "favorite_id is required in the request body when conversation_type=2",
        )
        return ConversationContextResult(None, None, web.json_response(err, status=400))

    if not mysql_store:
        err = openai_error_factory(
            "Favorite-based chat requires MySQL chat storage (HERMES_MYSQL_*)",
            err_type="service_unavailable",
        )
        return ConversationContextResult(None, None, web.json_response(err, status=503))

    user_id = _resolve_user_id_from_request(request, body)
    context_text, error_payload = load_favorite_system_context(
        mysql_store,
        favorite_ref=favorite_id,
        user_id=user_id,
    )
    if error_payload:
        status = int(error_payload.get("http_status") or 400)
        err = openai_error_factory(
            error_payload.get("message") or "Favorite lookup failed",
            err_type=error_payload.get("type"),
            code=error_payload.get("code"),
        )
        return ConversationContextResult(None, None, web.json_response(err, status=status))

    return ConversationContextResult(context_text, None, None)


def resolve_conversation_context_prompt(
    request: Any,
    body: Optional[Dict[str, Any]],
    *,
    user_message_text: str = "",
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    mysql_store: Any,
    openai_error_factory: Any,
) -> Tuple[Optional[str], Optional["web.Response"]]:
    """Backward-compatible wrapper: (extra_prompt, http_error) only."""
    result = resolve_conversation_context(
        request,
        body,
        user_message_text=user_message_text,
        conversation_history=conversation_history,
        mysql_store=mysql_store,
        openai_error_factory=openai_error_factory,
    )
    if result.error_response is not None:
        return None, result.error_response
    if result.early_reply:
        return None, None
    return result.extra_prompt, None
