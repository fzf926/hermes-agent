import json

from gateway.sql_execution import capture_dbops_sql_execution
from tools import dbops_config, dbops_delivery, dbops_tool
from tools.dbops_delivery_config import DBOpsDeliveryConfig
from tools.dbops_http import DBOpsHttpResult
from tools.dbops_models import DBOpsResolvedQuery


def _meta_from_result(result: str) -> dict:
    assert dbops_tool.DBOPS_META_MARKER in result
    _, raw_meta = result.split(dbops_tool.DBOPS_META_MARKER, 1)
    return json.loads(raw_meta)


def test_dbops_default_generates_sql_without_http(monkeypatch):
    monkeypatch.delenv("HERMES_DBOPS_EXECUTE_ENABLED", raising=False)
    monkeypatch.setattr(
        dbops_tool,
        "_pick_db_config",
        lambda _db_key: {
            "key": "prod",
            "instance_name": "online-instance",
            "db_name": "codecamp",
            "schema_name": "",
            "tb_name": "",
            "limit_num": 100,
        },
    )

    def fail_post(*_args, **_kwargs):
        raise AssertionError("post_dbops_form must not be called when execution is disabled")

    monkeypatch.setattr(dbops_delivery, "post_dbops_form", fail_post)

    result = dbops_tool.dbops_query_tool(
        {
            "sql_content": "select id, name from tbl_term where id = 1",
            "db_key": "prod",
        }
    )

    meta = _meta_from_result(result)
    assert meta["success"] is True
    assert meta["executed"] is False
    assert meta["delivery_mode"] == "sql_only"
    assert meta["query"]["full_sql"] == "select id, name from tbl_term where id = 1"
    assert meta["source"]["db_key"] == "prod"
    assert "未执行" in result


def test_dbops_sql_only_includes_generation_reason(monkeypatch):
    monkeypatch.delenv("HERMES_DBOPS_EXECUTE_ENABLED", raising=False)
    monkeypatch.setattr(
        dbops_tool,
        "_pick_db_config",
        lambda _db_key: {
            "key": "prod",
            "instance_name": "online-instance",
            "db_name": "codecamp",
            "schema_name": "",
            "tb_name": "",
            "limit_num": 100,
        },
    )
    monkeypatch.setattr(dbops_delivery, "post_dbops_form", lambda *_a, **_k: None)

    reason = "参考 tbl_term 表结构与用户问题中的学期 ID"
    result = dbops_tool.dbops_query_tool(
        {
            "sql_content": "select id from tbl_term where id = 1",
            "db_key": "prod",
            "generation_reason": reason,
        }
    )

    meta = _meta_from_result(result)
    assert meta["generation_reason"] == reason
    assert "生成依据" in result
    assert reason in result


def test_dbops_env_enabled_inline_after_count(monkeypatch):
    monkeypatch.setenv("HERMES_DBOPS_EXECUTE_ENABLED", "1")
    monkeypatch.setattr(
        dbops_tool,
        "_pick_db_config",
        lambda _db_key: {
            "key": "prod",
            "instance_name": "online-instance",
            "db_name": "codecamp",
            "schema_name": "",
            "tb_name": "",
            "limit_num": 100,
        },
    )
    monkeypatch.setattr(dbops_tool, "_load_dbops_cookie_text", lambda: "csrftoken=test")

    call = {"n": 0}

    def fake_post(url, payload, **_kwargs):
        call["n"] += 1
        if call["n"] == 1:
            return DBOpsHttpResult(ok=True, columns=["cnt"], rows=[[1]])
        return DBOpsHttpResult(
            ok=True,
            columns=["id"],
            rows=[[1]],
            data={
                "column_list": ["id"],
                "rows": [[1]],
                "full_sql": payload.get("sql_content"),
                "query_time": 0.01,
            },
        )

    monkeypatch.setattr(dbops_delivery, "post_dbops_form", fake_post)

    result = dbops_tool.dbops_query_tool(
        {
            "sql_content": "select id from tbl_term where id = 1",
            "db_key": "prod",
            "generation_reason": "COUNT 后 inline",
        }
    )

    meta = _meta_from_result(result)
    assert call["n"] == 2
    assert meta["success"] is True
    assert meta["executed"] is True
    assert meta["delivery_mode"] == "inline"
    assert meta["total_row_count"] == 1
    assert meta["generation_reason"] == "COUNT 后 inline"


def test_dbops_excel_delivery_preserves_query_time_for_favorite(monkeypatch, tmp_path):
    call = {"n": 0}

    def fake_post(_url, payload, **_kwargs):
        call["n"] += 1
        if call["n"] == 1:
            return DBOpsHttpResult(ok=True, columns=["cnt"], rows=[[25]])
        return DBOpsHttpResult(
            ok=True,
            columns=["id"],
            rows=[[1]],
            data={
                "column_list": ["id"],
                "rows": [[1]],
                "full_sql": payload.get("sql_content"),
                "query_time": 0.123,
            },
        )

    monkeypatch.setattr(dbops_delivery, "post_dbops_form", fake_post)
    monkeypatch.setattr(dbops_delivery, "write_query_excel", lambda *_a, **_k: None)

    outcome = dbops_delivery.execute_with_volume_routing(
        DBOpsResolvedQuery(
            sql_content="select id from tbl_term",
            db_key="prod",
            instance_name="online-instance",
            db_name="codecamp",
            schema_name="",
            tb_name="",
            limit_num=100,
        ),
        cookie_text="csrftoken=test",
        csrf_token="test",
        delivery_cfg=DBOpsDeliveryConfig(
            excel_threshold_min=21,
            excel_threshold_max=5000,
            export_threshold=5001,
            pagination_page_size=100,
            exports_dir=tmp_path,
        ),
    )

    record = capture_dbops_sql_execution(
        "call_excel",
        "dbops_query",
        {"sql_content": "select id from tbl_term"},
        outcome.text,
    )

    assert record["query_time_ms"] == 123.0


def test_dbops_yaml_enabled_allows_execution_when_env_unset(monkeypatch):
    monkeypatch.delenv("HERMES_DBOPS_EXECUTE_ENABLED", raising=False)
    monkeypatch.setattr(
        dbops_config, "load_dbops_yaml_config", lambda: {"execute_enabled": True}
    )
    assert dbops_tool.is_dbops_execute_enabled() is True


def test_dbops_disabled_still_rejects_unsafe_sql(monkeypatch):
    monkeypatch.delenv("HERMES_DBOPS_EXECUTE_ENABLED", raising=False)
    monkeypatch.setattr(
        dbops_tool,
        "_pick_db_config",
        lambda _db_key: {
            "key": "prod",
            "instance_name": "online-instance",
            "db_name": "codecamp",
            "schema_name": "",
            "tb_name": "",
            "limit_num": 100,
        },
    )

    result = dbops_tool.dbops_query_tool(
        {
            "sql_content": "delete from tbl_term where id = 1",
            "db_key": "prod",
        }
    )

    payload = json.loads(result)
    assert payload["success"] is False
    assert payload["sql_audit"]["passed"] is False
