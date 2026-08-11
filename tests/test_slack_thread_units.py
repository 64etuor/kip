from __future__ import annotations

import pytest

from kip.adapters.connectors.slack import SlackConnector


def _connector(monkeypatch: pytest.MonkeyPatch, responses: dict[str, list[dict]]) -> SlackConnector:
    monkeypatch.setenv("KIP_SLACK_BOT_TOKEN", "test-token")
    connector = SlackConnector("W1", ["C1"])
    calls: dict[str, int] = {}

    def fake_call(method: str, payload: dict) -> dict:
        index = calls.get(method, 0)
        calls[method] = index + 1
        return responses[method][min(index, len(responses[method]) - 1)]

    connector._call = fake_call  # type: ignore[method-assign]
    return connector


_ROOT = {"ts": "100.0", "user": "U_ASK", "text": "정산 증빙 제출기한이 언제인가요?", "reply_count": 2}
_REPLIES = [
    _ROOT | {"thread_ts": "100.0"},
    {"ts": "101.0", "thread_ts": "100.0", "user": "U_A", "text": "8월 15일입니다."},
    {"ts": "102.0", "thread_ts": "100.0", "user": "U_B", "text": "9월 30일로 연장됐어요. SEKR-QMS-W601 참고."},
]


def test_thread_becomes_one_semantic_event(monkeypatch: pytest.MonkeyPatch) -> None:
    connector = _connector(
        monkeypatch,
        {
            "conversations.history": [
                {
                    "ok": True,
                    "messages": [
                        _ROOT,
                        {"ts": "102.0", "thread_ts": "100.0", "user": "U_B", "text": "broadcast copy"},
                        {"ts": "200.0", "user": "U_C", "text": "독립 공지"},
                    ],
                }
            ],
            "conversations.replies": [{"ok": True, "messages": _REPLIES}],
        },
    )

    events = list(connector.pull_messages())

    assert len(events) == 2
    thread = events[0]
    assert thread.external_id == "W1:C1:100.0"
    assert thread.payload["thread"] is True
    assert thread.payload["message_count"] == 3
    text = thread.payload["text"]
    assert "제출기한이 언제인가요" in text
    assert "8월 15일입니다" in text
    assert "SEKR-QMS-W601" in text  # identifiers preserved verbatim
    standalone = events[1]
    assert standalone.payload["text"] == "독립 공지"


def test_new_reply_changes_the_event_id_but_not_the_external_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = {"ok": True, "messages": [_ROOT]}
    before = _connector(
        monkeypatch,
        {
            "conversations.history": [history],
            "conversations.replies": [{"ok": True, "messages": _REPLIES[:2]}],
        },
    )
    after = _connector(
        monkeypatch,
        {
            "conversations.history": [history],
            "conversations.replies": [{"ok": True, "messages": _REPLIES}],
        },
    )

    first = next(iter(before.pull_messages()))
    second = next(iter(after.pull_messages()))

    assert first.external_id == second.external_id
    assert first.event_id != second.event_id
