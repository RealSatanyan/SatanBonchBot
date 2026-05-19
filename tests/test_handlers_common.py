"""Тесты обработчиков handlers/common.py (задача C.2 №5–6).

Сценарии: /login (успех/неуспех/rate-limit), /start (онбординг/возврат),
/cancel (сброс FSM), fallback на нераспознанный ввод. perform_login и
rate-limit замоканы; парсинг учётных данных — настоящий.
"""
import asyncio
from types import SimpleNamespace

from handlers import common
from handlers.common import cmd_login, cmd_start, cmd_cancel, fallback_handler


class FakeMessage:
    def __init__(self, text="", user_id=1):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=user_id)
        self.answers = []
        self.replies = []

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})
        reply = FakeMessage(user_id=self.from_user.id)
        self.replies.append(reply)
        return reply

    async def edit_text(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})

    async def delete(self):
        pass


class FakeState:
    def __init__(self, state=None):
        self._state = state
        self._data = {}

    async def get_state(self):
        return self._state

    async def set_state(self, value):
        self._state = value

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def clear(self):
        self._state = None
        self._data = {}


def _register(conn, user_id):
    conn.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, 'e', 'p')", (user_id,)
    )
    conn.commit()


# --- C.2 №5: /login ----------------------------------------------------------

def test_login_success_reports_done(monkeypatch, reset_rate_limit):
    async def _ok_login(user_id, email, password):
        return True

    monkeypatch.setattr(common, "perform_login", _ok_login)
    msg = FakeMessage(text="/login user@sut.ru secret123", user_id=1)

    asyncio.run(cmd_login(msg, FakeState()))

    status = msg.replies[0]
    assert any("вошёл в личный кабинет" in a["text"] for a in status.answers)


def test_login_failure_reports_error(monkeypatch, reset_rate_limit):
    async def _fail_login(user_id, email, password):
        return False

    monkeypatch.setattr(common, "perform_login", _fail_login)
    msg = FakeMessage(text="/login user@sut.ru secret123", user_id=1)

    asyncio.run(cmd_login(msg, FakeState()))

    status = msg.replies[0]
    assert any("Не удалось войти" in a["text"] for a in status.answers)


def test_login_rate_limited_skips_login(monkeypatch, reset_rate_limit):
    """При сработавшем rate-limit вход не выполняется."""
    login_calls = []

    async def _track_login(user_id, email, password):
        login_calls.append(user_id)
        return True

    monkeypatch.setattr(common, "perform_login", _track_login)
    monkeypatch.setattr(common, "check_login_rate_limit", lambda uid: 120)
    msg = FakeMessage(text="/login user@sut.ru secret123", user_id=1)

    asyncio.run(cmd_login(msg, FakeState()))

    assert login_calls == []
    assert any("много попыток" in a["text"] for a in msg.answers)


def test_login_without_credentials_shows_prompt(reset_rate_limit):
    msg = FakeMessage(text="/login", user_id=1)

    asyncio.run(cmd_login(msg, FakeState()))

    assert any("login" in a["text"].lower() for a in msg.answers)


# --- C.2 №6: /start, /cancel, fallback ---------------------------------------

def test_start_onboards_new_user(temp_db):
    msg = FakeMessage(text="/start", user_id=1)

    asyncio.run(cmd_start(msg, FakeState()))

    assert any("Привет" in a["text"] for a in msg.answers)


def test_start_greets_returning_user(temp_db):
    _register(temp_db, 1)
    msg = FakeMessage(text="/start", user_id=1)

    asyncio.run(cmd_start(msg, FakeState()))

    assert any("С возвращением" in a["text"] for a in msg.answers)


def test_cancel_clears_active_fsm_state():
    state = FakeState(state="UIStates:write_text")
    msg = FakeMessage(text="/cancel", user_id=1)

    asyncio.run(cmd_cancel(msg, state))

    assert state._state is None
    assert any("отменил" in a["text"] for a in msg.answers)


def test_cancel_without_state_says_nothing_to_cancel():
    msg = FakeMessage(text="/cancel", user_id=1)

    asyncio.run(cmd_cancel(msg, FakeState(state=None)))

    assert any("нечего отменять" in a["text"] for a in msg.answers)


def test_fallback_handler_suggests_menu():
    msg = FakeMessage(text="абракадабра", user_id=1)

    asyncio.run(fallback_handler(msg))

    assert any("Не понял" in a["text"] for a in msg.answers)


# --- C.2 №6 (доп.): меню, help, отмена, вход через кнопку --------------------

from handlers.common import (  # noqa: E402
    menu_schedule,
    menu_autoclick,
    menu_messages,
    menu_profile,
    cmd_help,
    cb_cancel,
    cb_login,
    fsm_login_email,
    fsm_login_password,
)
from states import UIStates  # noqa: E402


class FakeCallbackQuery:
    def __init__(self, data="", user_id=1):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage(user_id=user_id)
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)

    async def edit_reply_markup(self, **kwargs):
        pass


def test_menu_schedule_shows_schedule_menu(temp_db):
    msg = FakeMessage(text="📅 Расписание", user_id=1)

    asyncio.run(menu_schedule(msg, FakeState()))

    assert any("расписание" in a["text"].lower() for a in msg.answers)


def test_menu_autoclick_unauthorized_prompts_login(temp_db):
    msg = FakeMessage(text="✅ Автоотметка", user_id=1)

    asyncio.run(menu_autoclick(msg, FakeState()))

    assert any("личный кабинет" in a["text"] for a in msg.answers)


def test_menu_messages_unauthorized_prompts_login(temp_db):
    msg = FakeMessage(text="✉️ Сообщения", user_id=1)

    asyncio.run(menu_messages(msg, FakeState()))

    assert any("личный кабинет" in a["text"] for a in msg.answers)


def test_menu_profile_shows_profile_for_registered(temp_db):
    _register(temp_db, 1)
    msg = FakeMessage(text="👤 Профиль", user_id=1)

    asyncio.run(menu_profile(msg, FakeState()))

    assert any("Профиль" in a["text"] for a in msg.answers)


def test_cmd_help_sends_help_text():
    msg = FakeMessage(text="/help", user_id=1)

    asyncio.run(cmd_help(msg, FakeState()))

    assert msg.answers


def test_cb_cancel_clears_state_and_shows_menu():
    state = FakeState(state="UIStates:login_email")
    cb = FakeCallbackQuery(data="m:cancel", user_id=1)

    asyncio.run(cb_cancel(cb, state))

    assert state._state is None
    assert any("Отменено" in (a or "") for a in cb.answers)


def test_cb_login_sets_email_state():
    state = FakeState()
    cb = FakeCallbackQuery(data="m:login", user_id=1)

    asyncio.run(cb_login(cb, state))

    assert state._state == UIStates.login_email


def test_fsm_login_email_rejects_invalid_address():
    state = FakeState(state=UIStates.login_email)
    msg = FakeMessage(text="не-email", user_id=1)

    asyncio.run(fsm_login_email(msg, state))

    assert state._state == UIStates.login_email  # остаёмся на вводе email


def test_fsm_login_email_accepts_valid_address():
    state = FakeState(state=UIStates.login_email)
    msg = FakeMessage(text="user@sut.ru", user_id=1)

    asyncio.run(fsm_login_email(msg, state))

    assert state._state == UIStates.login_password


class _StateWithEmail(FakeState):
    """FSM-фейк с email из предыдущего шага (login_email)."""

    async def get_data(self):
        return {"email": "user@sut.ru"}


def test_fsm_login_password_completes_login(monkeypatch, reset_rate_limit):
    async def _ok_login(user_id, email, password):
        return True

    monkeypatch.setattr(common, "perform_login", _ok_login)
    msg = FakeMessage(text="secret123", user_id=1)

    asyncio.run(fsm_login_password(msg, _StateWithEmail(state=UIStates.login_password)))

    status = msg.replies[0]
    assert any("вошёл" in a["text"] for a in status.answers)
