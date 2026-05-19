"""Shared DBOps HTTP helpers."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DBOPS_QUERY_URL = "https://dbops.codemao.cn/query/"
DBOPS_EXPORT_URL = "https://dbops.codemao.cn/query/export/"
DBOPS_EXPORT_GET_URL = "https://dbops.codemao.cn/query/export/get"
DBOPS_EXPORT_DOWN_BASE = "https://dbops.codemao.cn/query/export/down"


@dataclass
class DBOpsHttpResult:
    ok: bool
    status: Any = None
    msg: str = ""
    columns: list[Any] | None = None
    rows: list[Any] | None = None
    data: dict[str, Any] | None = None
    raw_text: str = ""
    error: str = ""


def decode_http_body(raw_bytes: bytes, content_encoding: str = "") -> str:
    body = raw_bytes or b""
    encoding = (content_encoding or "").lower()
    try:
        if "gzip" in encoding:
            body = gzip.decompress(body)
        elif body.startswith(b"\x1f\x8b"):
            body = gzip.decompress(body)
    except Exception:
        pass
    return body.decode("utf-8", errors="replace")


def build_dbops_headers(cookie_text: str, csrf_token: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://dbops.codemao.cn",
        "Referer": "https://dbops.codemao.cn/sqlquery/",
        "Cookie": cookie_text,
        "X-CSRFToken": csrf_token,
    }


def post_dbops_form(
    url: str,
    payload: dict[str, Any],
    *,
    cookie_text: str,
    csrf_token: str,
    timeout: int = 30,
) -> DBOpsHttpResult:
    body = urlencode({k: str(v) for k, v in payload.items()}).encode("utf-8")
    headers = build_dbops_headers(cookie_text, csrf_token)
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_text = decode_http_body(
                response.read(),
                response.headers.get("Content-Encoding", ""),
            )
    except HTTPError as exc:
        return DBOpsHttpResult(ok=False, error=f"DBOps HTTP error: {exc.code} {exc.reason}")
    except URLError as exc:
        return DBOpsHttpResult(ok=False, error=f"DBOps request failed: {exc.reason}")
    except Exception as exc:
        return DBOpsHttpResult(ok=False, error=f"DBOps request failed: {exc}")

    try:
        parsed = json.loads(raw_text)
    except Exception:
        return DBOpsHttpResult(
            ok=False,
            error="DBOps returned non-JSON response",
            raw_text=raw_text[:500],
        )

    if not isinstance(parsed, dict):
        return DBOpsHttpResult(ok=False, error="DBOps returned invalid JSON object")

    status = parsed.get("status")
    msg = str(parsed.get("msg") or "")
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
    if status != 0:
        err = data.get("error") if isinstance(data.get("error"), str) else msg
        return DBOpsHttpResult(ok=False, status=status, msg=msg, data=data, error=err or msg)

    columns = data.get("column_list") if isinstance(data.get("column_list"), list) else []
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    return DBOpsHttpResult(
        ok=True,
        status=status,
        msg=msg,
        columns=columns,
        rows=rows,
        data=data,
        raw_text=raw_text,
    )


def get_dbops_json(
    url: str,
    *,
    cookie_text: str,
    csrf_token: str,
    timeout: int = 30,
) -> DBOpsHttpResult:
    headers = build_dbops_headers(cookie_text, csrf_token)
    headers = {k: v for k, v in headers.items() if k != "Content-Type"}
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_text = decode_http_body(
                response.read(),
                response.headers.get("Content-Encoding", ""),
            )
    except HTTPError as exc:
        return DBOpsHttpResult(ok=False, error=f"DBOps HTTP error: {exc.code} {exc.reason}")
    except URLError as exc:
        return DBOpsHttpResult(ok=False, error=f"DBOps request failed: {exc.reason}")
    except Exception as exc:
        return DBOpsHttpResult(ok=False, error=f"DBOps request failed: {exc}")

    try:
        parsed = json.loads(raw_text)
    except Exception:
        return DBOpsHttpResult(
            ok=False,
            error="DBOps returned non-JSON response",
            raw_text=raw_text[:500],
        )

    if not isinstance(parsed, dict):
        return DBOpsHttpResult(ok=False, error="DBOps returned invalid JSON object")

    status = parsed.get("status")
    msg = str(parsed.get("msg") or "")
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
    if status != 0:
        return DBOpsHttpResult(ok=False, status=status, msg=msg, data=data, error=msg)
    return DBOpsHttpResult(ok=True, status=status, msg=msg, data=data, raw_text=raw_text)
