"""Summarize chat turn Q&A for SQL favorites (single auxiliary LLM call)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from agent.auxiliary_client import call_llm, extract_content_or_reasoning

logger = logging.getLogger(__name__)

_SUMMARY_MAX_LEN = 256
_HISTORY_TURN_TEXT_LIMIT = 1200
_SQL_SNIPPET_LIMIT = 500
_DEFAULT_HISTORY_TURNS = 5
_QUESTION_RE = re.compile(
    r'"question_summary"\s*:\s*"(.*?)"\s*,\s*"answer_summary"',
    re.DOTALL | re.IGNORECASE,
)
_ANSWER_RE = re.compile(
    r'"answer_summary"\s*:\s*"(.*?)"\s*\}',
    re.DOTALL | re.IGNORECASE,
)

_SUMMARY_SYSTEM = (
    "You write short labels for a SQL query favorites list in a database assistant app. "
    "You receive recent conversation turns and the SQL being bookmarked. "
    "Your job is to help the user recognize this favorite later, especially what business "
    "purpose the SQL serves. "
    "Return ONLY a JSON object with exactly these keys:\n"
    '- "question_summary": what the user wanted (intent), same language as the user, max 120 chars\n'
    '- "answer_summary": what the SQL/data accomplishes (business outcome), max 120 chars\n'
    "Use conversation history for context; focus answer_summary on what this SQL is for, "
    "not generic assistant prose. "
    "Do not include raw SQL, markdown fences, or preamble. Output raw JSON only."
)


def _load_mysql_chat_yaml() -> Dict[str, Any]:
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
    except Exception:
        return {}


def get_favorite_summarizer_history_turns() -> int:
    raw = os.getenv("HERMES_FAVORITE_SUMMARIZER_HISTORY_TURNS", "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), 10))
    yaml_cfg = _load_mysql_chat_yaml()
    val = yaml_cfg.get("favorite_summarizer_history_turns", _DEFAULT_HISTORY_TURNS)
    try:
        return max(1, min(int(val), 10))
    except (TypeError, ValueError):
        return _DEFAULT_HISTORY_TURNS


def is_favorite_summarizer_enabled() -> bool:
    flag = os.getenv("HERMES_FAVORITE_SUMMARIZER_ENABLED", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    yaml_cfg = _load_mysql_chat_yaml()
    if yaml_cfg.get("favorite_summarizer_enabled") is False:
        return False
    if yaml_cfg.get("favorite_summarizer_enabled") is True:
        return True
    return True


def _truncate_fallback(text: str, *, limit: int = _SUMMARY_MAX_LEN) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return ""
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _clip_turn_text(text: str, *, limit: int = _HISTORY_TURN_TEXT_LIMIT) -> str:
    compact = (text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _format_history_turns(
    history_turns: Optional[List[Dict[str, Any]]],
    *,
    current_turn_no: Optional[int] = None,
) -> str:
    if not history_turns:
        return "(no prior turns in window)"
    lines: List[str] = []
    for item in history_turns:
        tno = item.get("turn_no")
        marker = ""
        if current_turn_no is not None and tno == current_turn_no:
            marker = " [BOOKMARKED TURN]"
        elif item.get("is_current"):
            marker = " [BOOKMARKED TURN]"
        q = _clip_turn_text(str(item.get("question_text") or ""))
        a = _clip_turn_text(str(item.get("answer_text") or ""))
        lines.append(f"--- Turn {tno}{marker} ---")
        lines.append(f"User: {q or '(empty)'}")
        lines.append(f"Assistant: {a or '(empty)'}")
    return "\n".join(lines)


def _format_sql_executions(sql_executions: Optional[List[Dict[str, Any]]]) -> str:
    if not sql_executions:
        return "(none)"
    lines: List[str] = []
    for i, rec in enumerate(sql_executions[:10], 1):
        sql = _clip_turn_text(str(rec.get("sql_content") or ""), limit=_SQL_SNIPPET_LIMIT)
        lines.append(
            f"{i}. db={rec.get('database', '')} instance={rec.get('instance', '')} "
            f"status={rec.get('status', '')}\n   {sql}"
        )
    if len(sql_executions) > 10:
        lines.append(f"... and {len(sql_executions) - 10} more")
    return "\n".join(lines)


def _strip_markdown_fences(text: str) -> str:
    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
    return raw.strip()


def _parse_summary_json(text: str) -> Optional[Dict[str, str]]:
    if not text or not text.strip():
        return None
    raw = _strip_markdown_fences(text)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            q = str(parsed.get("question_summary") or "").strip()
            a = str(parsed.get("answer_summary") or "").strip()
            if q or a:
                return {"question_summary": q, "answer_summary": a}
    except json.JSONDecodeError:
        pass
    q_match = _QUESTION_RE.search(raw)
    a_match = _ANSWER_RE.search(raw)
    if q_match or a_match:
        return {
            "question_summary": (q_match.group(1) if q_match else "").replace('\\"', '"').strip(),
            "answer_summary": (a_match.group(1) if a_match else "").replace('\\"', '"').strip(),
        }
    return None


def _normalize_summaries(
    parsed: Optional[Dict[str, str]],
    *,
    question_text: str,
    answer_text: str,
) -> Dict[str, str]:
    q = _truncate_fallback((parsed or {}).get("question_summary") or "")
    a = _truncate_fallback((parsed or {}).get("answer_summary") or "")
    if not q:
        q = _truncate_fallback(question_text)
    if not a:
        a = _truncate_fallback(answer_text)
    return {"question_summary": q, "answer_summary": a}


def summarize_favorite_turn(
    *,
    question_text: str,
    answer_text: str,
    history_turns: Optional[List[Dict[str, Any]]] = None,
    sql_executions: Optional[List[Dict[str, Any]]] = None,
    timeout: float = 30.0,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """One auxiliary LLM call; returns question_summary and answer_summary only."""
    q_src = (question_text or "")[:4000]
    a_src = (answer_text or "")[:8000]
    fallback = _normalize_summaries(None, question_text=q_src, answer_text=a_src)

    if not is_favorite_summarizer_enabled():
        return fallback

    current_turn_no = None
    if history_turns:
        for item in history_turns:
            if item.get("is_current"):
                current_turn_no = item.get("turn_no")
                break
        if current_turn_no is None:
            current_turn_no = history_turns[-1].get("turn_no")

    history_block = _format_history_turns(
        history_turns,
        current_turn_no=current_turn_no,
    )
    sql_block = _format_sql_executions(sql_executions)

    user_content = (
        f"Recent conversation (up to {len(history_turns or [])} turns, oldest first):\n"
        f"{history_block}\n\n"
        f"SQL being bookmarked (from the marked turn):\n{sql_block}\n\n"
        f"Bookmarked turn — user question (full):\n{q_src}\n\n"
        f"Bookmarked turn — assistant answer (full):\n{a_src or '(empty)'}"
    )
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    try:
        response = call_llm(
            task="favorite_summarizer",
            messages=messages,
            max_tokens=350,
            temperature=0.2,
            timeout=timeout,
            main_runtime=main_runtime,
        )
        content = extract_content_or_reasoning(response).strip()
        parsed = _parse_summary_json(content)
        if not parsed and content:
            logger.warning(
                "Favorite summarizer: unparseable output (first 200 chars): %s",
                content[:200],
            )
        return _normalize_summaries(parsed, question_text=q_src, answer_text=a_src)
    except Exception as exc:
        logger.warning("Favorite summarizer failed, using truncation fallback: %s", exc)
        return fallback
