import json
from pathlib import Path

import pytest

from tools import dbops_delivery
from tools.dbops_delivery import execute_with_volume_routing
from tools.dbops_delivery_config import DBOpsDeliveryConfig
from tools.dbops_http import DBOpsHttpResult
from tools.dbops_models import DBOpsResolvedQuery


def _resolved() -> DBOpsResolvedQuery:
    return DBOpsResolvedQuery(
        sql_content="select id from tbl_term",
        db_key="prod",
        instance_name="inst",
        db_name="db",
        schema_name="",
        tb_name="",
        limit_num=100,
    )


def test_excel_delivery_for_21_rows(monkeypatch, tmp_path):
    cfg = DBOpsDeliveryConfig(
        excel_threshold_min=21,
        excel_threshold_max=5000,
        export_threshold=5001,
        pagination_page_size=1000,
        exports_dir=tmp_path,
        public_base_url="https://hermes.example",
    )
    call = {"n": 0}

    def fake_post(url, payload, **_kwargs):
        call["n"] += 1
        if call["n"] == 1:
            return DBOpsHttpResult(ok=True, columns=["cnt"], rows=[[25]])
        return DBOpsHttpResult(
            ok=True,
            columns=["id"],
            rows=[[i] for i in range(25)],
            data={"query_time": 0.1},
        )

    monkeypatch.setattr(dbops_delivery, "post_dbops_form", fake_post)
    monkeypatch.setattr(
        dbops_delivery,
        "write_query_excel",
        lambda path, *, columns, rows: path.write_bytes(b"xlsx"),
    )

    outcome = execute_with_volume_routing(
        _resolved(),
        cookie_text="c=1",
        csrf_token="t",
        generation_reason="测试 Excel 路由",
        delivery_cfg=cfg,
    )
    assert not isinstance(outcome, str)
    assert outcome.meta["delivery_mode"] == "excel"
    assert outcome.meta["total_row_count"] == 25
    assert "https://hermes.example/api/chat/sql-exports/" in outcome.meta["download_url"]
    assert (tmp_path / f"{outcome.meta['export_uid']}.xlsx").is_file()
    assert "生成依据" in outcome.text


def test_dbops_export_for_large_count(monkeypatch):
    cfg = DBOpsDeliveryConfig(export_threshold=5001)
    monkeypatch.setattr(
        dbops_delivery,
        "post_dbops_form",
        lambda *_a, **_k: DBOpsHttpResult(ok=True, columns=["cnt"], rows=[[6000]]),
    )
    monkeypatch.setattr(
        "tools.dbops_delivery.run_dbops_export",
        lambda **_k: type("R", (), {"ok": True, "task_id": "99", "download_url": "https://dbops/down?id=99"})(),
    )

    outcome = execute_with_volume_routing(
        _resolved(),
        cookie_text="c=1",
        csrf_token="t",
        delivery_cfg=cfg,
    )
    assert outcome.meta["delivery_mode"] == "dbops_export"
    assert outcome.meta["dbops_export_task_id"] == "99"
