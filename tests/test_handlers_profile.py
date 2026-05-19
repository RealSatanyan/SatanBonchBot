"""Тесты обработчиков профиля (handlers/profile.py, задача C.2 №7–8).

№7 — напоминания о парах (вкл/выкл, выбор минут), выход из ЛК.
№8 — тумблеры уведомлений бота B.1 (изменения расписания / сообщения ЛК).
БД — temp_db; реестры lk_client.apis / controllers — reset_registries.
"""
import asyncio
from types import SimpleNamespace

from satanbonchbot import db
from satanbonchbot import lk_client
from satanbonchbot.handlers.profile import  (
    cb_notify_toggle,
    cb_notify_minutes,
    cb_logout,
    cb_subs_settings,
    cb_subs_toggle,
)


class FakeMessage:
    def __init__(self, user_id=1):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []
        self.edits = []

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})

    async def edit_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})

    async def edit_reply_markup(self, **kwargs):
        pass


class FakeCallbackQuery:
    def __init__(self, data="", user_id=1):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage(user_id=user_id)
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


class FakeState:
    async def clear(self):
        pass


def _register(conn, user_id):
    conn.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, 'e', 'p')", (user_id,)
    )
    conn.commit()


# --- C.2 №7: напоминания о парах + выход -------------------------------------

def test_notify_toggle_flips_db_flag(temp_db):
    """Тумблер напоминаний о парах переключает notify_enabled в БД."""
    _register(temp_db, 1)
    assert db.get_notify_settings(1)[0] is True

    asyncio.run(cb_notify_toggle(FakeCallbackQuery(data="m:notify:toggle", user_id=1), FakeState()))

    assert db.get_notify_settings(1)[0] is False


def test_notify_minutes_sets_chosen_value(temp_db):
    _register(temp_db, 1)

    asyncio.run(cb_notify_minutes(FakeCallbackQuery(data="m:notify:min:15", user_id=1), FakeState()))

    assert db.get_notify_settings(1)[1] == 15


def test_logout_removes_user_and_session(temp_db, reset_registries):
    """Выход из ЛК удаляет пользователя из БД и из реестра API-сессий."""
    _register(temp_db, 1)
    lk_client.apis[1] = object()

    asyncio.run(cb_logout(FakeCallbackQuery(data="m:profile:logout", user_id=1), FakeState()))

    assert db.is_registered(1) is False
    assert 1 not in lk_client.apis


# --- C.2 №8: тумблеры уведомлений бота (B.1) ---------------------------------

def test_subs_settings_screen_opens(temp_db):
    _register(temp_db, 1)
    cb = FakeCallbackQuery(data="m:profile:subs", user_id=1)

    asyncio.run(cb_subs_settings(cb, FakeState()))

    assert any("Уведомления бота" in a["text"] for a in cb.message.answers)


def test_subs_toggle_schedule_flips_db_flag(temp_db):
    """Тумблер уведомлений об изменении расписания переключает флаг в БД."""
    _register(temp_db, 1)
    assert db.get_notify_schedule_enabled(1) is True

    asyncio.run(cb_subs_toggle(FakeCallbackQuery(data="m:subs:toggle:schedule", user_id=1), FakeState()))

    assert db.get_notify_schedule_enabled(1) is False
    assert db.get_notify_messages_enabled(1) is True  # второй тумблер не задет


def test_subs_toggle_messages_flips_db_flag(temp_db):
    """Тумблер уведомлений о новых сообщениях ЛК переключает флаг в БД."""
    _register(temp_db, 1)

    asyncio.run(cb_subs_toggle(FakeCallbackQuery(data="m:subs:toggle:messages", user_id=1), FakeState()))

    assert db.get_notify_messages_enabled(1) is False
    assert db.get_notify_schedule_enabled(1) is True
