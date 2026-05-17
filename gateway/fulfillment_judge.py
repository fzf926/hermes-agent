"""Judge whether an API chat turn satisfied the user's intent (auxiliary LLM)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from agent.auxiliary_client import call_llm, extract_content_or_reasoning

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"satisfied", "partial", "unsatisfied", "unknown"})
_STATUS_RE = re.compile(
    r'"fulfillment_status"\s*:\s*"(satisfied|partial|unsatisfied|unknown)"',
    re.IGNORECASE,
)
_IS_FINAL_RE = re.compile(r'"is_final"\s*:\s*(true|false)', re.IGNORECASE)
_REASON_RE = re.compile(
    r'"fulfillment_reason"\s*:\s*"(.*?)"\s*,\s*"is_final"',
    re.DOTALL | re.IGNORECASE,
)

_JUDGE_SYSTEM = (
    "You evaluate whether an AI assistant's reply satisfied the user's request. "
    "Consider the user question, assistant answer, any SQL executions, and technical "
    "turn status (answered, error, interrupted). "
    "Return ONLY a single JSON object with these keys:\n"
    '- "fulfillment_status": one of satisfied, partial, unsatisfied, unknown\n'
    '- "fulfillment_reason": brief explanation in the same language as the user question\n'
    '- "is_final": boolean — true if this turn fully addresses the user question '
    "without requiring follow-up for that specific ask\n"
    "satisfied = user intent met; partial = helpful but incomplete or SQL/results "
    "do not fully answer; unsatisfied = failed to address intent; unknown = cannot tell.\n"
    "Output must be one raw JSON object only — no markdown fences, no preamble."
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


def is_fulfillment_judge_enabled() -> bool:
    flag = os.getenv("HERMES_FULFILLMENT_JUDGE_ENABLED", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    yaml_cfg = _load_mysql_chat_yaml()
    if yaml_cfg.get("fulfillment_judge_enabled") is False:
        return False
    if yaml_cfg.get("fulfillment_judge_enabled") is True:
        return True
    return True


def _default_fulfillment(*, reason: str = "", is_final: bool = True) -> Dict[str, Any]:
    return {
        "fulfillment_status": "unknown",
        "fulfillment_reason": reason or "Fulfillment judge did not run.",
        "is_final": is_final,
    }


def _strip_markdown_fences(text: str) -> str:
    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
    return raw.strip()


def _extract_braced_json_object(text: str) -> Optional[Dict[str, Any]]:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start : idx + 1]
                try:
                    parsed = json.loads(snippet)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _parse_judge_json_heuristic(text: str) -> Optional[Dict[str, Any]]:
    status_match = _STATUS_RE.search(text)
    if not status_match:
        return None
    status = status_match.group(1).lower()
    reason = ""
    reason_match = _REASON_RE.search(text)
    if reason_match:
        reason = reason_match.group(1).replace('\\"', '"').strip()
    is_final = None
    final_match = _IS_FINAL_RE.search(text)
    if final_match:
        is_final = final_match.group(1).lower() == "true"
    return {
        "fulfillment_status": status,
        "fulfillment_reason": reason,
        "is_final": is_final if is_final is not None else True,
    }


def _parse_judge_json(text: str) -> Optional[Dict[str, Any]]:
    if not text or not text.strip():
        return None
    raw = _strip_markdown_fences(text)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    parsed = _extract_braced_json_object(raw)
    if parsed and "fulfillment_status" in parsed:
        return parsed
    return _parse_judge_json_heuristic(raw)


def _normalize_fulfillment(
    parsed: Optional[Dict[str, Any]],
    *,
    turn_status: str,
) -> Dict[str, Any]:
    if not parsed:
        return _default_fulfillment(
            reason="Judge returned unparseable output.",
            is_final=turn_status == "answered",
        )
    status = str(parsed.get("fulfillment_status") or "unknown").strip().lower()
    if status not in VALID_STATUSES:
        status = "unknown"
    reason = str(parsed.get("fulfillment_reason") or "").strip()[:512]
    is_final = parsed.get("is_final")
    if not isinstance(is_final, bool):
        is_final = turn_status == "answered" and status in {"satisfied", "partial"}
    return {
        "fulfillment_status": status,
        "fulfillment_reason": reason or status,
        "is_final": is_final,
    }


def conversation_for_api(fulfillment: Dict[str, Any]) -> Dict[str, Any]:
    """Shape fulfillment dict for HTTP ``conversation`` object."""
    return {
        "fulfillment_status": fulfillment.get("fulfillment_status", "unknown"),
        "fulfillment_reason": fulfillment.get("fulfillment_reason", ""),
        "is_final": bool(fulfillment.get("is_final", True)),
    }


def _format_sql_summary(sql_executions: Optional[List[Dict[str, Any]]]) -> str:
    if not sql_executions:
        return "(none)"
    lines: List[str] = []
    for i, rec in enumerate(sql_executions[:10], 1):
        lines.append(
            f"{i}. status={rec.get('status')} db={rec.get('database')} "
            f"instance={rec.get('instance')} sql={str(rec.get('sql_content', ''))[:200]}"
        )
    if len(sql_executions) > 10:
        lines.append(f"... and {len(sql_executions) - 10} more")
    return "\n".join(lines)


def judge_fulfillment(
    *,
    question_text: str,
    answer_text: str,
    sql_executions: Optional[List[Dict[str, Any]]] = None,
    turn_status: str = "answered",
    timeout: float = 45.0,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run auxiliary LLM judge; returns fulfillment_status, fulfillment_reason, is_final."""
    if not is_fulfillment_judge_enabled():
        return _default_fulfillment(reason="Fulfillment judge disabled.", is_final=True)

    q = (question_text or "")[:4000]
    a = (answer_text or "")[:8000]
    user_content = (
        f"Turn status: {turn_status}\n\n"
        f"User question:\n{q}\n\n"
        f"Assistant answer:\n{a}\n\n"
        f"SQL executions:\n{_format_sql_summary(sql_executions)}"
    )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    try:
        response = call_llm(
            task="fulfillment_judge",
            messages=messages,
            max_tokens=400,
            temperature=0.1,
            timeout=timeout,
            main_runtime=main_runtime,
        )
        content = extract_content_or_reasoning(response).strip()
        if not content:
            logger.warning(
                "Fulfillment judge: empty model output (provider may use reasoning-only fields)"
            )
        parsed = _parse_judge_json(content)
        if not parsed and content:
            logger.warning(
                "Fulfillment judge: unparseable output (first 300 chars): %s",
                content[:300],
            )
        return _normalize_fulfillment(parsed, turn_status=turn_status)
    except Exception as exc:
        logger.warning("Fulfillment judge failed: %s", exc)
        logger.debug("Fulfillment judge traceback", exc_info=True)
        return _default_fulfillment(
            reason=f"Judge error: {exc}"[:512],
            is_final=turn_status == "answered",
        )
