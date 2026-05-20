from decimal import Decimal

from gateway.chat_mysql_store import ChatMySQLStore, validate_sql_favorite_eligibility


def test_sql_favorite_allows_successful_queries_under_five_seconds():
    error = validate_sql_favorite_eligibility(
        [
            {"id": 1, "status": "success", "query_time_ms": Decimal("1200.500")},
            {"id": 2, "status": "success", "query_time_ms": 5000},
        ]
    )

    assert error is None


def test_sql_favorite_rejects_missing_sql_executions():
    error = validate_sql_favorite_eligibility([])

    assert error == "No SQL executions found for this turn"


def test_sql_favorite_rejects_failed_sql_execution():
    error = validate_sql_favorite_eligibility(
        [{"id": 1, "status": "error", "query_time_ms": 100}]
    )

    assert "Only successful SQL executions can be favorited" in error


def test_sql_favorite_rejects_null_query_time():
    error = validate_sql_favorite_eligibility(
        [{"id": 1, "status": "success", "query_time_ms": None}]
    )

    assert "missing query_time_ms" in error


def test_sql_favorite_rejects_query_over_five_seconds():
    error = validate_sql_favorite_eligibility(
        [{"id": 1, "status": "success", "query_time_ms": Decimal("5000.001")}]
    )

    assert "within 5 seconds" in error


class _TurnsCursor:
    def __init__(self):
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        if "FROM chat_turn" in sql:
            row = {
                "id": 11,
                "session_id": 7,
                "user_id": "user-1",
                "tenant_id": None,
                "turn_no": 1,
                "question_message_id": 21,
                "answer_message_id": 22,
                "question_text": "查一下订单",
                "answer_text": "查询完成",
                "status": "answered",
                "error_code": None,
                "error_message": None,
                "fulfillment_status": "unknown",
                "fulfillment_reason": "Fulfillment judge did not run.",
                "is_final": 1,
                "feedback_score": None,
                "created_at": None,
            }
            if "hermes_response_id" in sql:
                row["hermes_response_id"] = "chatcmpl_turn_1"
            self._rows = [row]
        elif "FROM chat_sql_execution" in sql or "FROM chat_tool_call" in sql:
            self._rows = []
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)


class _TurnsConnection:
    def __init__(self):
        self.cursor_obj = _TurnsCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def test_list_turns_includes_assistant_hermes_response_id():
    store = ChatMySQLStore.__new__(ChatMySQLStore)
    conn = _TurnsConnection()
    store._connect = lambda: conn
    store.resolve_session = lambda _ref: {"id": 7, "session_uid": "s1"}

    result = store.list_turns_by_session_ref("s1")

    assert result["turns"][0]["hermes_response_id"] == "chatcmpl_turn_1"
