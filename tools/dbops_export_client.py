"""DBOps async export API (POST export + poll GET)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from tools.dbops_http import (
    DBOPS_EXPORT_DOWN_BASE,
    DBOPS_EXPORT_GET_URL,
    DBOPS_EXPORT_URL,
    get_dbops_json,
    post_dbops_form,
)


@dataclass
class DBOpsExportResult:
    ok: bool
    task_id: str = ""
    download_url: str = ""
    error: str = ""


def run_dbops_export(
    *,
    instance_name: str,
    db_name: str,
    schema_name: str,
    tb_name: str,
    sql_content: str,
    cookie_text: str,
    csrf_token: str,
    poll_interval_sec: float = 2.0,
    poll_timeout_sec: float = 180.0,
) -> DBOpsExportResult:
    payload = {
        "instance_name": instance_name,
        "db_name": db_name,
        "schema_name": schema_name,
        "tb_name": tb_name,
        "sql_content": sql_content,
    }
    created = post_dbops_form(
        DBOPS_EXPORT_URL,
        payload,
        cookie_text=cookie_text,
        csrf_token=csrf_token,
        timeout=60,
    )
    if not created.ok:
        return DBOpsExportResult(ok=False, error=created.error or created.msg)

    data = created.data or {}
    task_id = str(data.get("id") or data.get("task_id") or "").strip()
    if not task_id:
        return DBOpsExportResult(ok=False, error="DBOps export did not return task id")

    deadline = time.monotonic() + max(1.0, float(poll_timeout_sec))
    interval = max(0.5, float(poll_interval_sec))
    while time.monotonic() < deadline:
        polled = get_dbops_json(
            f"{DBOPS_EXPORT_GET_URL}?id={task_id}",
            cookie_text=cookie_text,
            csrf_token=csrf_token,
            timeout=30,
        )
        if not polled.ok:
            return DBOpsExportResult(ok=False, task_id=task_id, error=polled.error or polled.msg)

        poll_data = polled.data or {}
        state = str(poll_data.get("state") or poll_data.get("status") or "").lower()
        if state in {"success", "done", "finished", "complete", "completed"}:
            return DBOpsExportResult(
                ok=True,
                task_id=task_id,
                download_url=f"{DBOPS_EXPORT_DOWN_BASE}?id={task_id}",
            )
        if state in {"fail", "failed", "error"}:
            err = str(poll_data.get("error") or poll_data.get("msg") or "export failed")
            return DBOpsExportResult(ok=False, task_id=task_id, error=err)
        time.sleep(interval)

    return DBOpsExportResult(
        ok=False,
        task_id=task_id,
        error=f"DBOps export timed out after {int(poll_timeout_sec)}s",
    )
