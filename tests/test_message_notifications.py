"""Тесты уведомлений о новых сообщениях ЛК (задача C.2).

Сеть замокана: lk_client.get_message_api подменяется фейком, не ходящим в ЛК.
Отправка в Telegram замокана фейковым messages_service.bot.
"""
import asyncio

import lk_client
import messages_service
from messages_service import check_new_messages_for_user


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({'chat_id': chat_id, 'text': text})


class _FakeApi:
    def __init__(self, messages):
        self._messages = messages

    async def get_messages_page(self, page=1):
        return {'messages': self._messages, 'total_pages': 1}


def _msg(msg_id, sender="Деканат", title="Тема"):
    return {'id': msg_id, 'sender': sender, 'title': title}


def _patch(monkeypatch, messages):
    """Замокать get_message_api и bot; вернуть фейковый bot для проверок."""
    fake_bot = _FakeBot()
    monkeypatch.setattr(messages_service, "bot", fake_bot)

    async def fake_get_message_api(user_id):
        return _FakeApi(messages) if messages is not None else None

    monkeypatch.setattr(lk_client, "get_message_api", fake_get_message_api)
    return fake_bot


def _add_user(temp_db, user_id):
    temp_db.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, 'e', 'p')", (user_id,)
    )
    temp_db.commit()


# --- первый опрос ------------------------------------------------------------

def test_first_poll_records_baseline_without_notifying(monkeypatch, temp_db):
    import db
    _add_user(temp_db, 1)
    fake_bot = _patch(monkeypatch, [_msg("m3"), _msg("m2"), _msg("m1")])

    notified = asyncio.run(check_new_messages_for_user(1))

    assert notified == 0
    assert fake_bot.sent == []
    assert db.get_last_seen_message_id(1) == "m3"


# --- нет новых ---------------------------------------------------------------

def test_no_new_messages_does_not_notify(monkeypatch, temp_db):
    import db
    _add_user(temp_db, 1)
    db.set_last_seen_message_id(1, "m3")
    fake_bot = _patch(monkeypatch, [_msg("m3"), _msg("m2"), _msg("m1")])

    notified = asyncio.run(check_new_messages_for_user(1))

    assert notified == 0
    assert fake_bot.sent == []


# --- есть новые --------------------------------------------------------------

def test_new_messages_are_notified(monkeypatch, temp_db):
    import db
    _add_user(temp_db, 1)
    db.set_last_seen_message_id(1, "m1")
    fake_bot = _patch(monkeypatch, [_msg("m3"), _msg("m2"), _msg("m1")])

    notified = asyncio.run(check_new_messages_for_user(1))

    assert notified == 2
    assert len(fake_bot.sent) == 1
    assert "Новых сообщений в ЛК: 2" in fake_bot.sent[0]['text']
    assert db.get_last_seen_message_id(1) == "m3"


def test_last_seen_not_on_page_treats_all_as_new(monkeypatch, temp_db):
    import db
    _add_user(temp_db, 1)
    db.set_last_seen_message_id(1, "very-old-id")
    _patch(monkeypatch, [_msg("m3"), _msg("m2"), _msg("m1")])

    notified = asyncio.run(check_new_messages_for_user(1))

    assert notified == 3


# --- крайние случаи ----------------------------------------------------------

def test_no_api_returns_zero(monkeypatch, temp_db):
    _add_user(temp_db, 1)
    fake_bot = _patch(monkeypatch, None)

    assert asyncio.run(check_new_messages_for_user(1)) == 0
    assert fake_bot.sent == []


def test_empty_inbox_returns_zero(monkeypatch, temp_db):
    _add_user(temp_db, 1)
    _patch(monkeypatch, [])

    assert asyncio.run(check_new_messages_for_user(1)) == 0
