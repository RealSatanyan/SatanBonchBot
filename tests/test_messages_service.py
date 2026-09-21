"""Тесты messages_service: счётчик, свежесть кэша, отрисовка списка сообщений.

Отправка в Telegram замокана — messages_service.bot подменяется фейком,
записывающим вызовы send_message. message_states изолируется фикстурой
reset_message_states.
"""
import asyncio

from satanbonchbot import messages_service
from satanbonchbot.messages_service import  (
    format_message_count,
    _messages_cache_fresh,
    _build_message_state,
    _invalidate_messages_cache,
    show_message_list,
)


class _FakeBot:
    """Фейк aiogram Bot: send_message только записывает вызовы."""

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({'chat_id': chat_id, 'text': text, **kwargs})


def _msg(msg_id="1", **overrides):
    base = {'id': msg_id, 'title': 'Тема', 'sender': 'Деканат', 'date': '2026-05-19'}
    base.update(overrides)
    return base


# --- format_message_count ----------------------------------------------------

def test_format_message_count_exact_when_no_more_pages():
    assert format_message_count(loaded=7, total_pages=1, per_page=20, has_more=False) == "7"


def test_format_message_count_estimate_when_more_pages():
    assert format_message_count(loaded=20, total_pages=3, per_page=20, has_more=True) == "≈60"


def test_format_message_count_plus_when_more_but_no_page_info():
    assert format_message_count(loaded=20, total_pages=1, per_page=0, has_more=True) == "20+"


# --- _messages_cache_fresh ---------------------------------------------------

def test_messages_cache_fresh_true_within_ttl():
    state = {'messages': [_msg()], 'fetched_at': 1000.0}
    assert _messages_cache_fresh(state, now_ts=1100.0, ttl_sec=300) is True


def test_messages_cache_fresh_false_when_stale():
    state = {'messages': [_msg()], 'fetched_at': 1000.0}
    assert _messages_cache_fresh(state, now_ts=2000.0, ttl_sec=300) is False


def test_messages_cache_fresh_false_when_no_fetched_at():
    state = {'messages': [_msg()], 'fetched_at': None}
    assert _messages_cache_fresh(state, now_ts=1100.0, ttl_sec=300) is False


def test_messages_cache_fresh_false_for_empty_state():
    assert _messages_cache_fresh(None, now_ts=1.0, ttl_sec=300) is False
    assert _messages_cache_fresh({'messages': []}, now_ts=1.0, ttl_sec=300) is False


# --- _build_message_state ----------------------------------------------------

def test_build_message_state_shape():
    first_page = {'messages': [_msg("1"), _msg("2")], 'total_pages': 4}
    state = _build_message_state(api="API", first_page=first_page)

    assert state['api'] == "API"
    assert state['total_pages'] == 4
    assert state['loaded_pages'] == 1
    assert state['current_index'] == 0
    assert state['per_page'] == 2
    assert isinstance(state['fetched_at'], float)


# --- _invalidate_messages_cache ----------------------------------------------

def test_invalidate_messages_cache_clears_fetched_at(reset_message_states):
    reset_message_states[77] = {'messages': [_msg()], 'fetched_at': 1234.0}

    _invalidate_messages_cache(77)

    assert reset_message_states[77]['fetched_at'] is None


def test_invalidate_messages_cache_unknown_user_is_noop(reset_message_states):
    _invalidate_messages_cache(12345)  # не должно бросать


# --- show_message_list -------------------------------------------------------

def test_show_message_list_unknown_user_sends_nothing(monkeypatch, reset_message_states):
    fake_bot = _FakeBot()
    monkeypatch.setattr(messages_service, "bot", fake_bot)

    asyncio.run(show_message_list(user_id=1, chat_id=1, index=0))

    assert fake_bot.sent == []


def test_show_message_list_out_of_range_index_sends_nothing(monkeypatch, reset_message_states):
    fake_bot = _FakeBot()
    monkeypatch.setattr(messages_service, "bot", fake_bot)
    reset_message_states[1] = {'messages': [_msg()], 'total_pages': 1}

    asyncio.run(show_message_list(user_id=1, chat_id=1, index=5))

    assert fake_bot.sent == []


def test_show_message_list_renders_message(monkeypatch, reset_message_states):
    fake_bot = _FakeBot()
    monkeypatch.setattr(messages_service, "bot", fake_bot)
    reset_message_states[1] = {
        'messages': [_msg("1", title="Сессия"), _msg("2")],
        'total_pages': 1,
        'per_page': 2,
    }

    asyncio.run(show_message_list(user_id=1, chat_id=42, index=0))

    assert len(fake_bot.sent) == 1
    sent = fake_bot.sent[0]
    assert sent['chat_id'] == 42
    assert "Сообщение 1 из 2" in sent['text']
    assert "Сессия" in sent['text']


def test_show_message_list_escapes_html_specials_in_sender_and_title(monkeypatch, reset_message_states):
    """Заголовок/отправитель с спецсимволами не должны ломать парсер Telegram.

    Telegram возвращал 400 «can't parse entities» при неэкранированных
    `*`/`_`/`[` в Markdown-режиме (issue inbox.py:190). Рендер должен
    использовать HTML и экранировать <,>,& в динамических полях.
    """
    fake_bot = _FakeBot()
    monkeypatch.setattr(messages_service, "bot", fake_bot)
    reset_message_states[1] = {
        'messages': [_msg("1", title="Тема <с> & *spec*", sender="Иванов <admin>")],
        'total_pages': 1,
        'per_page': 1,
    }

    asyncio.run(show_message_list(user_id=1, chat_id=42, index=0))

    sent = fake_bot.sent[0]
    assert sent.get('parse_mode') == 'HTML'
    # < > & должны быть экранированы; * и _ безопасны в HTML-режиме.
    assert "&lt;с&gt;" in sent['text']
    assert "&lt;admin&gt;" in sent['text']
    assert "&amp;" in sent['text']
    assert "*spec*" in sent['text']  # звёздочки остаются как литералы


def test_show_message_list_first_message_has_no_back_button(monkeypatch, reset_message_states):
    fake_bot = _FakeBot()
    monkeypatch.setattr(messages_service, "bot", fake_bot)
    reset_message_states[1] = {
        'messages': [_msg("1"), _msg("2")],
        'total_pages': 1,
        'per_page': 2,
    }

    asyncio.run(show_message_list(user_id=1, chat_id=1, index=0))

    buttons = [b.text for row in fake_bot.sent[0]['reply_markup'].inline_keyboard for b in row]
    assert not any("Назад" in t for t in buttons)
    assert any("Вперед" in t for t in buttons)
