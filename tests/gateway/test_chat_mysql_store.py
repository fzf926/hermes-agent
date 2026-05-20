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


class _FavoriteCursor:
    def __init__(self):
        self._rows = []
        self.lastrowid = None
        self.inserted_favorite_response_id = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        if "FROM chat_message" in sql:
            if "hermes_response_id" not in sql:
                self._rows = []
                return
            requested = {str(p) for p in (params or ())}
            if requested & {"22", "resp_response_1"}:
                self._rows = [
                    {
                        "id": 22,
                        "session_id": 7,
                        "turn_no": 1,
                        "user_id": "user-1",
                        "role": "assistant",
                        "hermes_response_id": "resp_response_1",
                    }
                ]
            else:
                self._rows = []
        elif "FROM chat_turn" in sql:
            self._rows = [
                {
                    "id": 11,
                    "session_id": 7,
                    "user_id": "user-1",
                    "turn_no": 1,
                    "question_text": "查一下订单",
                    "answer_text": "查询完成",
                    "fulfillment_status": "satisfied",
                    "fulfillment_reason": "All SQL executions completed successfully.",
                    "is_final": 1,
                }
            ]
        elif "FROM chat_sql_favorite f" in sql and "WHERE f.user_id" in sql:
            self._rows = []
        elif "FROM chat_sql_execution" in sql:
            self._rows = [{"id": 31, "status": "success", "query_time_ms": 1200}]
        elif "INSERT INTO chat_sql_favorite " in sql:
            self.inserted_favorite_response_id = params[5]
            self.lastrowid = 41
            self._rows = []
        elif "INSERT INTO chat_sql_favorite_item" in sql:
            self._rows = []
        elif "FROM chat_sql_favorite f" in sql and "WHERE f.id" in sql:
            self._rows = [
                {
                    "id": 41,
                    "favorite_uid": "fav-1",
                    "user_id": "user-1",
                    "session_id": 7,
                    "session_uid": "s1",
                    "hermes_session_id": "hs1",
                    "turn_id": 11,
                    "turn_no": 1,
                    "hermes_response_id": self.inserted_favorite_response_id,
                    "question_summary": "查订单",
                    "answer_summary": "查询完成",
                    "fulfillment_status": "satisfied",
                    "fulfillment_reason": "All SQL executions completed successfully.",
                    "sql_count": 1,
                    "created_at": None,
                    "updated_at": None,
                }
            ]
        elif "SELECT sql_content" in sql:
            self._rows = []
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FavoriteConnection:
    def __init__(self):
        self.cursor_obj = _FavoriteCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_create_sql_favorite_accepts_response_api_hermes_response_id(monkeypatch):
    store = ChatMySQLStore.__new__(ChatMySQLStore)
    conn = _FavoriteConnection()
    store._connect = lambda: conn
    monkeypatch.setattr(
        "gateway.chat_mysql_store.summarize_favorite_turn",
        lambda **_: {"question_summary": "查订单", "answer_summary": "查询完成"},
    )

    result = store.create_sql_favorite(
        user_id="user-1",
        hermes_response_id="resp_response_1",
    )

    assert result["ok"] is True
    assert result["created"] is True
    assert conn.cursor_obj.inserted_favorite_response_id == "resp_response_1"
    assert result["favorite"]["hermes_response_id"] == "resp_response_1"


def test_create_sql_favorite_resolves_message_primary_id_to_hermes_response_id(monkeypatch):
    store = ChatMySQLStore.__new__(ChatMySQLStore)
    conn = _FavoriteConnection()
    store._connect = lambda: conn
    monkeypatch.setattr(
        "gateway.chat_mysql_store.summarize_favorite_turn",
        lambda **_: {"question_summary": "查订单", "answer_summary": "查询完成"},
    )

    result = store.create_sql_favorite(
        user_id="user-1",
        hermes_response_id="22",
    )

    assert result["ok"] is True
    assert result["created"] is True
    assert conn.cursor_obj.inserted_favorite_response_id == "resp_response_1"
    assert result["favorite"]["hermes_response_id"] == "resp_response_1"
