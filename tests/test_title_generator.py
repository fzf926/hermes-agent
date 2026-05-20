from types import SimpleNamespace

from agent import title_generator


def test_generate_title_uses_first_user_message_only(monkeypatch):
    captured = {}

    def _fake_call_llm(**kwargs):
        captured["messages"] = kwargs.get("messages")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="数据库查询优化")
                )
            ]
        )

    monkeypatch.setattr(title_generator, "call_llm", _fake_call_llm)

    title = title_generator.generate_title(
        user_message="帮我优化这个 SQL",
        assistant_response="这里是助手回复，不应作为标题依据",
    )

    assert title == "数据库查询优化"
    assert captured["messages"][1]["content"] == "User first message: 帮我优化这个 SQL"


def test_maybe_auto_title_allows_empty_assistant_response(monkeypatch):
    invoked = {}

    class _InlineThread:
        def __init__(self, target=None, args=(), kwargs=None, **_unused):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    def _fake_auto_title_session(
        session_db,
        session_id,
        user_message,
        assistant_response,
        **kwargs,
    ):
        invoked["session_db"] = session_db
        invoked["session_id"] = session_id
        invoked["user_message"] = user_message
        invoked["assistant_response"] = assistant_response
        invoked["kwargs"] = kwargs

    monkeypatch.setattr(title_generator.threading, "Thread", _InlineThread)
    monkeypatch.setattr(title_generator, "auto_title_session", _fake_auto_title_session)

    db = object()
    title_generator.maybe_auto_title(
        session_db=db,
        session_id="sess-1",
        user_message="请总结今天的接口改动",
        assistant_response="",
        conversation_history=[{"role": "user", "content": "请总结今天的接口改动"}],
    )

    assert invoked["session_db"] is db
    assert invoked["session_id"] == "sess-1"
    assert invoked["assistant_response"] == ""
