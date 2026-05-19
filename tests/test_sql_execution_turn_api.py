from gateway.sql_execution import turn_sql_payload_for_api


def test_turn_sql_payload_matches_responses_shape():
    tool_result = (
        "查询成功，共 1 行。\n\n| id |\n| --- |\n| 1 |\n"
        "\n__DBOPS_META__\n"
        '{"success": true, "executed": true, "row_count": 1, '
        '"delivery_mode": "inline", "source": {"db_name": "db", "instance_name": "inst"}, '
        '"query": {"full_sql": "select id from t"}}'
    )
    payload = turn_sql_payload_for_api(
        [
            {
                "sql_content": "select id from t",
                "db_name": "db",
                "instance_name": "inst",
                "status": "success",
                "tool_call_id": "call_abc",
                "row_count": 1,
            }
        ],
        tool_call_rows=[
            {
                "tool_name": "dbops_query",
                "tool_args": {"sql_content": "select id from t"},
                "tool_result": tool_result,
            }
        ],
    )

    assert len(payload["sql"]) == 1
    item = payload["sql"][0]
    assert item["database"] == "db"
    assert item["instance"] == "inst"
    assert "user_display" in item
    assert "查询成功" in item["user_display"]
    assert "sql_display" in payload
    assert "查询成功" in payload["sql_display"]
