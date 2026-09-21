"""Тесты LessonController: тик автоотметки, гонка start/stop, переавторизация.

Извлечённый из start_lesson метод _run_tick (C.1) тестируется явными
сценариями — раньше решающая логика была заперта в фоновом цикле. Сеть и БД
замоканы фейками; время передаётся в _run_tick параметром.
"""
import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from satanbonchbot import db
from satanbonchbot import lesson_controller
from satanbonchbot import lk_client
from satanbonchbot.lesson_controller import  LessonController


class _FakeApi:
    """Фейк DebuggableBonchAPI: фиксирует клики/логины без обращения к ЛК."""

    # Класс-атрибут (не аргумент __init__): reauthenticate() создаёт новый
    # экземпляр через lk_client.DebuggableBonchAPI() без аргументов, поэтому
    # неуспешный логин имитируется подклассом с login_result = False.
    login_result = True

    def __init__(self, click_result=0, upcoming_details=None, raw_timetable="",
                 current_details=None):
        self.click_result = click_result
        self.upcoming_details = upcoming_details
        self.raw_timetable = raw_timetable
        # Детали пары, которая сейчас идёт у пользователя (для gate автоотметки):
        # None ≡ «у юзера нет пары с этим номером сегодня».
        self.current_details = current_details
        self.click_calls = 0
        self.current_details_calls = 0
        self.logged_in = None

    async def click_start_lesson(self, user_id):
        self.click_calls += 1
        if isinstance(self.click_result, BaseException):
            raise self.click_result
        return self.click_result

    async def get_upcoming_start_lesson_details(self, **kwargs):
        if isinstance(self.upcoming_details, BaseException):
            raise self.upcoming_details
        return self.upcoming_details

    async def get_current_lesson_details(self, now_dt, target_pair_index):
        self.current_details_calls += 1
        if isinstance(self.current_details, BaseException):
            raise self.current_details
        return self.current_details

    async def get_raw_timetable(self):
        if isinstance(self.raw_timetable, BaseException):
            raise self.raw_timetable
        return self.raw_timetable

    async def login(self, email, password):
        self.logged_in = (email, password)
        return type(self).login_result


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id, text):
        self.sent.append((user_id, text))


def _notify(monkeypatch, enabled, minutes=10):
    monkeypatch.setattr(lesson_controller, "get_notify_settings",
                        lambda user_id: (enabled, minutes))


def _controller(api=None, bot=None):
    return LessonController(api=api, bot=bot, user_id=1)


# --- _run_tick: клик/простой -------------------------------------------------

def test_tick_idle_outside_lesson_does_not_click(monkeypatch):
    """Вне времени пар тик не кликает и не шлёт сообщений, сбрасывает notified."""
    _notify(monkeypatch, enabled=False)
    api, bot = _FakeApi(), _FakeBot()
    c = _controller(api, bot)
    c.notified = True

    asyncio.run(c._run_tick(datetime(2026, 5, 19, 7, 0)))

    assert api.click_calls == 0
    assert bot.sent == []
    assert c.notified is False


def _lesson_details(pair_number=1, subject="Матан", room="ауд. 101", teacher="Иванов И.И."):
    """Хелпер: типичный ответ get_current_lesson_details для существующей пары."""
    return {"pair_number": pair_number, "subject": subject, "room": room, "teacher": teacher}


def test_tick_clicks_during_lesson_and_notifies_success(monkeypatch):
    """В интервале пары тик кликает; при clicked>0 шлёт сообщение об автоотметке."""
    _notify(monkeypatch, enabled=False)
    api, bot = _FakeApi(click_result=1, current_details=_lesson_details()), _FakeBot()
    c = _controller(api, bot)

    asyncio.run(c._run_tick(datetime(2026, 5, 19, 9, 30)))

    assert api.click_calls == 1
    assert len(bot.sent) == 1
    assert "Автоотметка" in bot.sent[0][1]


def test_tick_skips_click_when_pair_absent_from_schedule(monkeypatch):
    """Регрессия 2026-05-20: время попало в сетку пар, но у пользователя нет
    пары с этим номером сегодня → клик НЕ делаем и ✅ НЕ шлём.

    До фикса click_start_lesson постила все 'Начать занятие' с недельной
    страницы, что в 9:00 кликало вечерние пары и спамило ложные ✅.
    """
    _notify(monkeypatch, enabled=False)
    api, bot = _FakeApi(click_result=1, current_details=None), _FakeBot()
    c = _controller(api, bot)

    asyncio.run(c._run_tick(datetime(2026, 5, 19, 9, 30)))

    assert api.click_calls == 0
    assert bot.sent == []


def test_tick_click_returns_zero_no_success_message(monkeypatch):
    """clicked==0 (кандидатов нет) — клик был, но сообщения об успехе нет."""
    _notify(monkeypatch, enabled=False)
    api, bot = _FakeApi(click_result=0, current_details=_lesson_details()), _FakeBot()
    c = _controller(api, bot)

    asyncio.run(c._run_tick(datetime(2026, 5, 19, 9, 30)))

    assert api.click_calls == 1
    assert bot.sent == []


def test_tick_propagates_value_error_from_click(monkeypatch):
    """ValueError из click_start_lesson (истёкшая сессия) пробрасывается из тика —
    цикл start_lesson ловит его и запускает переавторизацию."""
    _notify(monkeypatch, enabled=False)
    api = _FakeApi(click_result=ValueError("Session expired"),
                   current_details=_lesson_details())
    c = _controller(api, _FakeBot())

    with pytest.raises(ValueError):
        asyncio.run(c._run_tick(datetime(2026, 5, 19, 9, 30)))


def test_tick_propagates_value_error_from_current_lesson_details(monkeypatch):
    """Регрессия хотфикса 'gate автоотметки по расписанию' (5f2bfba): gate дергает
    get_current_lesson_details ДО click_start_lesson, и его bare except Exception
    глотал ValueError об истёкшей сессии так же, как и обычное 'пары сегодня нет' —
    переавторизация никогда не запускалась, автоклик молча умирал навсегда.
    ValueError должен пробрасываться из тика точно так же, как из click."""
    _notify(monkeypatch, enabled=False)
    api = _FakeApi(current_details=ValueError("Session expired - ERR_MSG from LK."))
    c = _controller(api, _FakeBot())

    with pytest.raises(ValueError):
        asyncio.run(c._run_tick(datetime(2026, 5, 19, 9, 30)))


# --- _run_tick: напоминание о паре -------------------------------------------

def test_tick_sends_upcoming_reminder(monkeypatch):
    """За 9 минут до пары, что есть в расписании, тик шлёт напоминание."""
    _notify(monkeypatch, enabled=True, minutes=10)
    details = {"room": "ауд. 101", "subject": "Матан", "teacher": "Иванов И.И."}
    api, bot = _FakeApi(upcoming_details=details), _FakeBot()
    c = _controller(api, bot)

    asyncio.run(c._run_tick(datetime(2026, 5, 19, 8, 51)))

    assert len(bot.sent) == 1
    assert "пара" in bot.sent[0][1]


def test_tick_propagates_value_error_from_upcoming_reminder(monkeypatch):
    """Тот же класс регрессии, что и у gate'а автоклика (см. тест выше), но в
    блоке напоминания 'за N минут': его except Exception глотал ValueError об
    истёкшей сессии, если она обнаруживалась именно в окне напоминания, до
    того как её успевал поймать gate текущей пары."""
    _notify(monkeypatch, enabled=True, minutes=10)
    api = _FakeApi(upcoming_details=ValueError("Session expired - ERR_MSG from LK."))
    c = _controller(api, _FakeBot())

    with pytest.raises(ValueError):
        asyncio.run(c._run_tick(datetime(2026, 5, 19, 8, 51)))


def test_tick_no_reminder_when_pair_absent(monkeypatch):
    """Окно напоминания, но пары нет в расписании — молчим, ключ не фиксируем."""
    _notify(monkeypatch, enabled=True, minutes=10)
    api, bot = _FakeApi(upcoming_details=None), _FakeBot()
    c = _controller(api, bot)

    asyncio.run(c._run_tick(datetime(2026, 5, 19, 8, 51)))

    assert bot.sent == []
    assert c._last_upcoming_lesson_key is None


def test_tick_does_not_repeat_reminder(monkeypatch):
    """Повторный тик в ту же минуту не шлёт второе напоминание о той же паре."""
    _notify(monkeypatch, enabled=True, minutes=10)
    details = {"room": "ауд. 101", "subject": "Матан", "teacher": "Иванов И.И."}
    api, bot = _FakeApi(upcoming_details=details), _FakeBot()
    c = _controller(api, bot)

    asyncio.run(c._run_tick(datetime(2026, 5, 19, 8, 51)))
    asyncio.run(c._run_tick(datetime(2026, 5, 19, 8, 51)))

    assert len(bot.sent) == 1


# --- start(): идемпотентность и отсутствие гонки -----------------------------

def test_start_returns_true_and_creates_task(monkeypatch):
    _notify(monkeypatch, enabled=False)

    async def scenario():
        c = _controller(_FakeApi(), _FakeBot())
        created = c.start()
        task = c.task
        await c.stop_lesson(1)
        return created, task

    created, task = asyncio.run(scenario())
    assert created is True
    assert task is not None


def test_start_twice_does_not_spawn_second_task(monkeypatch):
    """Повторный start при уже работающем контроллере не плодит вторую задачу
    и не теряет handle первой (C.1 — устранение гонки start)."""
    _notify(monkeypatch, enabled=False)

    async def scenario():
        c = _controller(_FakeApi(), _FakeBot())
        assert c.start() is True
        first_task = c.task
        assert c.start() is False  # повторный старт отклонён
        assert c.task is first_task  # handle первой задачи сохранён
        await c.stop_lesson(1)
        return first_task

    first_task = asyncio.run(scenario())
    assert first_task.done()  # задача корректно завершилась после stop


def test_stop_lesson_waits_for_running_task():
    """stop_lesson возвращает «остановлено» только ПОСЛЕ фактического завершения
    задачи — а не пока тик ещё висит в click_start_lesson (C.1)."""
    progress = []

    async def scenario():
        c = _controller(_FakeApi(), _FakeBot())

        async def hanging_tick():
            try:
                await asyncio.sleep(3600)  # имитация зависшего click_start_lesson
            finally:
                progress.append("task_finished")

        c.is_running = True
        c.task = asyncio.create_task(hanging_tick())
        await asyncio.sleep(0)  # дать задаче дойти до await

        result = await c.stop_lesson(1)
        progress.append("stop_returned")
        return result

    result = asyncio.run(scenario())
    assert result == "Автокликалка остановлена."
    assert progress == ["task_finished", "stop_returned"]


def test_stop_lesson_idempotent_when_not_running():
    c = _controller(_FakeApi(), _FakeBot())
    assert asyncio.run(c.stop_lesson(1)) == "Автокликалка уже остановлена."


# --- reauthenticate ----------------------------------------------------------

def test_reauthenticate_success(monkeypatch, temp_db, reset_registries):
    """Успешная переавторизация создаёт новый API, логинится и обновляет self.api."""
    db.cursor.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)",
        (1, "user@sut.ru", "encrypted"),
    )
    db.conn.commit()
    monkeypatch.setattr(lesson_controller, "decrypt_password", lambda p: "plain-pw")
    monkeypatch.setattr(lk_client, "DebuggableBonchAPI", _FakeApi)

    c = _controller(api=_FakeApi(), bot=_FakeBot())
    asyncio.run(c.reauthenticate())

    assert isinstance(c.api, _FakeApi)
    assert c.api.logged_in == ("user@sut.ru", "plain-pw")
    assert lk_client.apis[1] is c.api


def test_reauthenticate_raises_when_user_missing(temp_db):
    """Нет данных пользователя в БД — переавторизация падает с ValueError."""
    c = _controller(api=_FakeApi(), bot=_FakeBot())
    with pytest.raises(ValueError):
        asyncio.run(c.reauthenticate())


def test_reauthenticate_raises_when_login_fails(monkeypatch, temp_db, reset_registries):
    """login() вернул False (неверный/сменившийся пароль, сбой ЛК) — реальный
    провал переавторизации раньше проглатывался: reauthenticate() отбрасывал
    bool от login() и всегда «успешно» подменял self.api на неавторизованный
    клиент, из-за чего start_lesson() рапортовал 'Переавторизация успешна' и
    каждую минуту заново ловил тот же 'Session expired', никогда не доходя
    до уведомления пользователю про /login."""
    db.cursor.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)",
        (1, "user@sut.ru", "encrypted"),
    )
    db.conn.commit()
    monkeypatch.setattr(lesson_controller, "decrypt_password", lambda p: "plain-pw")
    _FailingLoginApi = type("_FailingLoginApi", (_FakeApi,), {"login_result": False})
    monkeypatch.setattr(lk_client, "DebuggableBonchAPI", _FailingLoginApi)

    c = _controller(api=_FakeApi(), bot=_FakeBot())

    with pytest.raises(ValueError):
        asyncio.run(c.reauthenticate())


# --- dump_timetable_snapshot / capture_debug_artifacts -----------------------

def test_dump_snapshot_raises_on_expired_session():
    """Редирект на login=no в расписании — снимок не сохраняется, бросается ValueError."""
    api = _FakeApi(raw_timetable="<html>index.php?login=no</html>")
    c = _controller(api=api, bot=_FakeBot())

    with pytest.raises(ValueError):
        asyncio.run(c.dump_timetable_snapshot("test"))


def test_dump_snapshot_saves_via_save_debug_dump(monkeypatch):
    """Нормальное расписание сохраняется через save_debug_dump."""
    saved = []
    monkeypatch.setattr(
        lesson_controller, "save_debug_dump",
        lambda uid, html: saved.append((uid, html)) or Path("dump.html"),
    )
    api = _FakeApi(raw_timetable="<html>расписание</html>")
    c = _controller(api=api, bot=_FakeBot())

    result = asyncio.run(c.dump_timetable_snapshot("reason"))

    assert result == Path("dump.html")
    assert saved == [("1", "<html>расписание</html>")]


def test_capture_artifacts_skips_on_expired_session(monkeypatch):
    """При истёкшей сессии capture_debug_artifacts не снимает дамп расписания."""
    calls = []

    async def _fake_dump(reason):
        calls.append(reason)

    c = _controller(api=_FakeApi(), bot=_FakeBot())
    monkeypatch.setattr(c, "dump_timetable_snapshot", _fake_dump)

    asyncio.run(c.capture_debug_artifacts(ValueError("Session expired")))

    assert calls == []


def test_capture_artifacts_dumps_on_nonetype_attribute_error(monkeypatch):
    """AttributeError по NoneType (сломан парсер недели) — снимаем дамп расписания."""
    calls = []

    async def _fake_dump(reason):
        calls.append(reason)

    c = _controller(api=_FakeApi(), bot=_FakeBot())
    monkeypatch.setattr(c, "dump_timetable_snapshot", _fake_dump)

    asyncio.run(c.capture_debug_artifacts(
        AttributeError("'NoneType' object has no attribute 'group'")))

    assert calls == ["parser_get_week_failed"]
