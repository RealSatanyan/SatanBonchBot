"""Тесты обработчика автоотметки (handlers/autoclick.py, задача C.2 №4).

Контроллер автоотметки замокан фейком; lesson_controller.controllers и
lk_client.apis изолируются reset_registries, БД — temp_db.
"""
import asyncio
from types import SimpleNamespace

from satanbonchbot import db
from satanbonchbot import lesson_controller
from satanbonchbot.handlers.autoclick import  cb_autoclick


class FakeController:
    """Фейк LessonController: фиксирует start/stop, отдаёт статус без сети."""

    def __init__(self, is_running=False):
        self.is_running = is_running
        self.task = None
        self.started = False
        self.stopped = False

    async def start_lesson(self):
        self.started = True

    def start(self):
        """Мимикрия LessonController.start: синхронный, идемпотентный."""
        if self.is_running:
            return False
        self.is_running = True
        self.started = True
        self.task = object()  # sentinel — фоновая задача «создана»
        return True

    async def stop_lesson(self, user_id):
        self.stopped = True
        self.is_running = False

    async def get_status(self):
        return "статус автоотметки"


class FakeMessage:
    def __init__(self, user_id=1):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []
        self.edits = []

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})

    async def edit_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})


class FakeCallbackQuery:
    def __init__(self, data, user_id=1):
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


def test_autoclick_menu_start_enables_and_persists(temp_db, reset_registries):
    """«Включить» из меню запускает автоотметку и пишет autoclick_enabled=1 в БД."""
    _register(temp_db, 1)
    db.set_autoclick_enabled(1, False)
    controller = FakeController(is_running=False)
    lesson_controller.controllers[1] = controller

    asyncio.run(cb_autoclick(FakeCallbackQuery(data="m:auto:start", user_id=1), FakeState()))

    assert db.get_autoclick_enabled(1) is True
    assert controller.task is not None  # задача автоотметки создана


def test_autoclick_menu_stop_disables_and_persists(temp_db, reset_registries):
    """«Выключить» из меню останавливает автоотметку и пишет autoclick_enabled=0."""
    _register(temp_db, 1)
    db.set_autoclick_enabled(1, True)
    controller = FakeController(is_running=True)
    lesson_controller.controllers[1] = controller

    asyncio.run(cb_autoclick(FakeCallbackQuery(data="m:auto:stop", user_id=1), FakeState()))

    assert db.get_autoclick_enabled(1) is False
    assert controller.stopped is True


def test_autoclick_menu_unauthorized_prompts_login(monkeypatch, temp_db, reset_registries):
    """Нет контроллера и автологин не удался — показываем приглашение войти."""
    async def _fail_autologin(user_id):
        return False

    monkeypatch.setattr("satanbonchbot.handlers.autoclick.auto_login_user", _fail_autologin)
    cb = FakeCallbackQuery(data="m:auto:start", user_id=1)

    asyncio.run(cb_autoclick(cb, FakeState()))

    assert any("Не удалось войти" in a["text"] for a in cb.message.answers)


# --- команды автоотметки -----------------------------------------------------

from satanbonchbot.handlers.autoclick import  (  # noqa: E402
    cmd_start_lesson,
    cmd_stop_lesson,
    cmd_status,
    cmd_test_notify,
    cmd_my_account,
    send_autoclick_panel,
)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


def test_cmd_start_lesson_unauthorized_prompts_login(monkeypatch, reset_registries):
    async def _fail_autologin(user_id):
        return False

    monkeypatch.setattr("satanbonchbot.handlers.autoclick.auto_login_user", _fail_autologin)
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_start_lesson(msg))

    assert any("авторизуйтесь" in a["text"] for a in msg.answers)


def test_cmd_start_lesson_starts_controller(temp_db, reset_registries):
    _register(temp_db, 1)
    controller = FakeController(is_running=False)
    lesson_controller.controllers[1] = controller
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_start_lesson(msg))

    assert controller.task is not None
    assert any("запущена" in a["text"] for a in msg.answers)


def test_cmd_start_lesson_already_running(reset_registries):
    lesson_controller.controllers[1] = FakeController(is_running=True)
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_start_lesson(msg))

    assert any("уже запущена" in a["text"] for a in msg.answers)


def test_cmd_stop_lesson_stops_controller(temp_db, reset_registries):
    _register(temp_db, 1)
    controller = FakeController(is_running=True)
    lesson_controller.controllers[1] = controller
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_stop_lesson(msg))

    assert controller.stopped is True
    assert any("остановлена" in a["text"] for a in msg.answers)


def test_cmd_status_reports_controller_status(reset_registries):
    lesson_controller.controllers[1] = FakeController(is_running=True)
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_status(msg))

    assert any("статус автоотметки" in a["text"] for a in msg.answers)


def test_cmd_test_notify_sends_via_bot(monkeypatch):
    fake_bot = FakeBot()
    monkeypatch.setattr("satanbonchbot.handlers.autoclick.bot", fake_bot)
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_test_notify(msg))

    assert len(fake_bot.sent) == 1
    assert any("отправлено" in a["text"] for a in msg.answers)


def test_cmd_my_account_without_saved_account(temp_db, reset_registries):
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_my_account(msg))

    assert any("Нет сохраненного" in a["text"] for a in msg.answers)


def test_send_autoclick_panel_shows_status(monkeypatch, reset_registries):
    fake_bot = FakeBot()
    monkeypatch.setattr("satanbonchbot.handlers.autoclick.bot", fake_bot)
    lesson_controller.controllers[1] = FakeController(is_running=True)

    asyncio.run(send_autoclick_panel(1, chat_id=555))

    assert len(fake_bot.sent) == 1
    assert "Автоотметка" in fake_bot.sent[0]["text"]


def test_cb_autoclick_refresh_branch_keeps_state(temp_db, reset_registries):
    _register(temp_db, 1)
    controller = FakeController(is_running=True)
    lesson_controller.controllers[1] = controller

    asyncio.run(cb_autoclick(FakeCallbackQuery(data="m:auto:refresh", user_id=1), FakeState()))

    assert controller.stopped is False
    assert controller.task is None  # «обновить» не запускает автоотметку
