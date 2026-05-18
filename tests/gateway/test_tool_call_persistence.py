import asyncio
import json

from gateway.chat_mysql_store import ChatMySQLStore
from gateway.platforms.api_server import APIServerAdapter
from gateway.config import PlatformConfig


class _FakeCursor:
    def __init__(self):
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executions.append((sql, params))


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
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


def test_save_tool_calls_inserts_chat_tool_call_rows():
    store = ChatMySQLStore.__new__(ChatMySQLStore)
    conn = _FakeConnection()
    store._connect = lambda: conn

    store.save_tool_calls(
        session_id=7,
        turn_id=11,
        user_id="biz-user-42",
        calls=[
            {
                "tool_name": "read_file",
                "tool_args": {"path": "/tmp/a.txt"},
                "tool_result": {"content": "hello"},
                "status": "success",
                "latency_ms": 12,
            }
        ],
    )

    assert conn.committed is True
    assert conn.closed is True
    sql, params = conn.cursor_obj.executions[0]
    assert "INSERT INTO chat_tool_call" in sql
    assert params[0:5] == (7, 11, None, "biz-user-42", "read_file")
    assert json.loads(params[5]) == {"path": "/tmp/a.txt"}
    assert json.loads(params[6]) == {"content": "hello"}
    assert params[7:9] == ("success", 12)


def test_persist_chat_to_mysql_saves_tool_calls_with_turn_metadata():
    adapter = APIServerAdapter(PlatformConfig(enabled=True))

    class _Store:
        def __init__(self):
            self.saved_tool_calls = None

        def save_qa_turn(self, **kwargs):
            return {"session_id": 101, "turn_id": 202}

        def save_tool_calls(self, **kwargs):
            self.saved_tool_calls = kwargs

    store = _Store()
    adapter._get_chat_mysql_store = lambda: store

    asyncio.run(
        adapter._persist_chat_to_mysql(
            user_id="business-user",
            hermes_session_id="session-1",
            question_text="use a tool",
            answer_text="done",
            model="hermes-agent",
            usage={},
            tool_calls=[
                {
                    "tool_name": "calculator",
                    "tool_args": {"expression": "6*7"},
                    "tool_result": "42",
                    "status": "success",
                    "latency_ms": 5,
                }
            ],
        )
    )

    assert store.saved_tool_calls == {
        "session_id": 101,
        "turn_id": 202,
        "user_id": "business-user",
        "calls": [
            {
                "tool_name": "calculator",
                "tool_args": {"expression": "6*7"},
                "tool_result": "42",
                "status": "success",
                "latency_ms": 5,
            }
        ],
    }
