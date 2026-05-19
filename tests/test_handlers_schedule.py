"""Точечные тесты обработчиков расписания (handlers/schedule/).

Хэндлеры aiogram вызываются напрямую с фейковыми Message / CallbackQuery /
FSMContext. Сеть и реестры замоканы; кэш расписания и lk_client.apis
изолируются фикстурами reset_timetable_service / reset_registries.
"""
import asyncio
import base64
from types import SimpleNamespace

import lk_client
from handlers.schedule import personal, group, teacher, room
from handlers.schedule import common as sched_common


def _enc(text):
    """Кодирование имени в base64 — как в keyboards для callback_data."""
    return base64.b64encode(text.encode('utf-8')).decode('utf-8')


class FakeMessage:
    """Фейк aiogram Message: answer/edit_text записывают вызовы."""

    def __init__(self, text="", user_id=1):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []
        self.edits = []
        self.photos = []
        self.replies = []  # FakeMessage-объекты, возвращённые answer()

    async def answer(self, text, **kwargs):
        self.answers.append({'text': text, **kwargs})
        reply = FakeMessage(user_id=self.from_user.id)
        self.replies.append(reply)
        return reply

    async def edit_text(self, text, **kwargs):
        self.edits.append({'text': text, **kwargs})

    async def answer_photo(self, photo, **kwargs):
        self.photos.append({'photo': photo, **kwargs})

    async def delete(self):
        pass


class FakeCallbackQuery:
    """Фейк aiogram CallbackQuery."""

    def __init__(self, data="", user_id=1):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage(user_id=user_id)
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


class FakeState:
    """Фейк aiogram FSMContext."""

    def __init__(self):
        self.state = None

    async def clear(self):
        self.state = None

    async def set_state(self, value):
        self.state = value


class _FakeUserAPI:
    """Фейк DebuggableBonchAPI: личное расписание без сети."""

    async def get_timetable(self, week_offset=0):
        return []


def _lesson(teacher="Иванов И.И.", room_no="ауд. 401", week=1):
    return {
        'Группа': 'ИКВ-11', 'Число': '2026.05.18', 'День недели': 'Понедельник',
        'Номер недели': week, 'Номер дня недели': 0, 'Номер занятия': 1,
        'Время занятия': '09:00-10:35', 'Предмет': 'Базы данных',
        'Тип занятия': 'Лекция', 'ФИО преподавателя': teacher, 'Номер кабинета': room_no,
    }


# --- personal: cmd_timetable -------------------------------------------------

def test_cmd_timetable_unauthorized_prompts_login(monkeypatch, reset_registries):
    async def _fail_autologin(uid):
        return False

    monkeypatch.setattr(personal, "auto_login_user", _fail_autologin)
    msg = FakeMessage(user_id=1)

    asyncio.run(personal.cmd_timetable(msg))

    assert any("авторизуйтесь" in a['text'] for a in msg.answers)


def test_cmd_timetable_authorized_sends_schedule(reset_registries):
    lk_client.apis[1] = _FakeUserAPI()
    msg = FakeMessage(user_id=1)

    asyncio.run(personal.cmd_timetable(msg, uid=1))

    assert len(msg.answers) == 1
    assert msg.answers[0].get('reply_markup') is not None


# --- personal: cb_sched_my ---------------------------------------------------

def test_cb_sched_my_unregistered_shows_login_prompt(temp_db):
    cb = FakeCallbackQuery(data="m:sched:my", user_id=1)

    asyncio.run(personal.cb_sched_my(cb, FakeState()))

    assert any("войди в ЛК" in a['text'] for a in cb.message.answers)


# --- teacher -----------------------------------------------------------------

def test_cmd_teacher_timetable_no_args_shows_usage():
    msg = FakeMessage(text="/teacher_timetable", user_id=1)

    asyncio.run(teacher.cmd_teacher_timetable(msg))

    assert any("Используйте" in a['text'] for a in msg.answers)


def test_cmd_teacher_timetable_found(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson(teacher="Петров П.П.")]}
    msg = FakeMessage(user_id=1)

    asyncio.run(teacher.cmd_teacher_timetable(msg, override="Петров"))

    assert len(msg.answers) == 1
    assert "Петров" in msg.answers[0]['text']


def test_cmd_teacher_timetable_not_found(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson(teacher="Петров П.П.")]}
    msg = FakeMessage(user_id=1)

    asyncio.run(teacher.cmd_teacher_timetable(msg, override="Сидоров"))

    assert any("Не найдено занятий" in a['text'] for a in msg.answers)


def test_cmd_teachers_lists_unique_teachers(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {
        'ИКВ-11': [_lesson(teacher="Петров П.П."), _lesson(teacher="Иванов И.И.")],
    }
    msg = FakeMessage(user_id=1)

    asyncio.run(teacher.cmd_teachers(msg))

    text = msg.answers[-1]['text']
    assert "Петров П.П." in text and "Иванов И.И." in text


# --- room --------------------------------------------------------------------

def test_cmd_classroom_timetable_no_args_shows_usage():
    msg = FakeMessage(text="/classroom_timetable", user_id=1)

    asyncio.run(room.cmd_classroom_timetable(msg))

    assert any("Используйте" in a['text'] for a in msg.answers)


def test_cmd_classroom_timetable_found(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson(room_no="ауд. 512")]}
    msg = FakeMessage(user_id=1)

    asyncio.run(room.cmd_classroom_timetable(msg, override="512"))

    assert len(msg.answers) == 1
    assert "512" in msg.answers[0]['text']


def test_cmd_classrooms_lists_unique_rooms(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {
        'ИКВ-11': [_lesson(room_no="ауд. 401"), _lesson(room_no="ауд. 512")],
    }
    msg = FakeMessage(user_id=1)

    asyncio.run(room.cmd_classrooms(msg))

    text = msg.answers[-1]['text']
    assert "ауд. 401" in text and "ауд. 512" in text


# --- group -------------------------------------------------------------------

def test_cmd_groups_lists_groups(monkeypatch):
    async def _fake_api():
        return SimpleNamespace(groups_id={'1': 'ИКВ-11', '2': 'ИКВ-12'})

    monkeypatch.setattr(group, "get_timetable_api", _fake_api)
    msg = FakeMessage(user_id=1)

    asyncio.run(group.cmd_groups(msg))

    assert "ИКВ-11" in msg.answers[-1]['text']


def test_cmd_group_timetable_no_args_shows_usage():
    msg = FakeMessage(text="/group_timetable", user_id=1)

    asyncio.run(group.cmd_group_timetable(msg))

    assert any("Используйте" in a['text'] for a in msg.answers)


def test_cmd_group_timetable_unknown_group(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson()]}

    async def _fake_api():
        return SimpleNamespace(groups_id={'1': 'ИКВ-11'})

    monkeypatch.setattr(group, "get_timetable_api", _fake_api)
    msg = FakeMessage(user_id=1)

    asyncio.run(group.cmd_group_timetable(msg, override="НЕТ-99"))

    assert any("не найдена" in a['text'] for a in msg.answers)


# --- common: cmd_reload_timetable --------------------------------------------

def test_cmd_reload_timetable_reports_count(monkeypatch):
    async def _fake_reload(**kwargs):
        return {'ИКВ-11': [], 'ИКВ-12': []}

    monkeypatch.setattr(sched_common, "get_all_groups_timetable", _fake_reload)
    msg = FakeMessage(user_id=1)

    asyncio.run(sched_common.cmd_reload_timetable(msg))

    # cmd_reload_timetable обновляет статус-сообщение, возвращённое message.answer().
    status_msg = msg.replies[0]
    assert any("2 групп" in e['text'] for e in status_msg.edits)


# --- навигация по неделям (callback-хэндлеры) --------------------------------

def test_process_teacher_week_navigation_renders(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson(teacher="Петров П.П.")]}
    cb = FakeCallbackQuery(data="all_teacher_weeks_" + _enc("Петров"), user_id=1)

    asyncio.run(teacher.process_teacher_week_navigation(cb))

    assert any("Петров" in e['text'] for e in cb.message.edits)


def test_process_teacher_week_navigation_cache_not_loaded(reset_timetable_service):
    cb = FakeCallbackQuery(data="all_teacher_weeks_" + _enc("Петров"), user_id=1)

    asyncio.run(teacher.process_teacher_week_navigation(cb))

    assert any(a and "не загружено" in a for a in cb.answers)


def test_process_classroom_week_navigation_renders(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson(room_no="ауд. 512")]}
    cb = FakeCallbackQuery(data="all_classroom_weeks_" + _enc("512"), user_id=1)

    asyncio.run(room.process_classroom_week_navigation(cb))

    assert any("512" in e['text'] for e in cb.message.edits)


def test_process_group_week_navigation_renders(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson()]}
    cb = FakeCallbackQuery(data=f"prev_group_week_{_enc('ИКВ-11')}_1", user_id=1)

    asyncio.run(group.process_group_week_navigation(cb))

    assert any("ИКВ-11" in e['text'] for e in cb.message.edits)


def test_process_group_day_renders(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson()]}
    cb = FakeCallbackQuery(data=f"group_day_{_enc('ИКВ-11')}_0", user_id=1)

    asyncio.run(group.process_group_day(cb))

    assert len(cb.message.edits) == 1


def test_process_week_navigation_personal(reset_registries):
    lk_client.apis[1] = _FakeUserAPI()
    cb = FakeCallbackQuery(data="current_week_0", user_id=1)

    asyncio.run(personal.process_week_navigation(cb))

    assert len(cb.message.edits) == 1


def test_process_week_navigation_unauthorized(reset_registries):
    cb = FakeCallbackQuery(data="current_week_0", user_id=1)

    asyncio.run(personal.process_week_navigation(cb))

    assert any(a and "авторизуйтесь" in a for a in cb.answers)


# --- меню расписания (callback-хэндлеры) -------------------------------------

def test_cb_sched_group_sets_fsm_state():
    cb = FakeCallbackQuery(data="m:sched:group", user_id=1)
    state = FakeState()

    asyncio.run(group.cb_sched_group(cb, state))

    assert state.state is not None
    assert cb.message.answers


def test_cb_sched_teacher_sets_fsm_state():
    cb = FakeCallbackQuery(data="m:sched:teacher", user_id=1)
    state = FakeState()

    asyncio.run(teacher.cb_sched_teacher(cb, state))

    assert state.state is not None


def test_cb_sched_room_sets_fsm_state():
    cb = FakeCallbackQuery(data="m:sched:room", user_id=1)
    state = FakeState()

    asyncio.run(room.cb_sched_room(cb, state))

    assert state.state is not None


def test_fsm_ask_teacher_delegates_to_command(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson(teacher="Петров П.П.")]}
    msg = FakeMessage(text="Петров", user_id=1)
    state = FakeState()

    asyncio.run(teacher.fsm_ask_teacher(msg, state))

    assert msg.answers


def test_fsm_ask_group_delegates_to_command(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson()]}

    async def _fake_api():
        return SimpleNamespace(groups_id={'1': 'ИКВ-11'})

    monkeypatch.setattr(group, "get_timetable_api", _fake_api)
    msg = FakeMessage(text="ИКВ-11", user_id=1)

    asyncio.run(group.fsm_ask_group(msg, FakeState()))

    assert msg.answers


def test_process_group_week_navigation_cache_not_loaded(reset_timetable_service):
    cb = FakeCallbackQuery(data=f"next_group_week_{_enc('ИКВ-11')}_1", user_id=1)

    asyncio.run(group.process_group_week_navigation(cb))

    assert any(a and "не загружено" in a for a in cb.answers)


def test_process_group_week_navigation_image_branch(reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson()]}
    cb = FakeCallbackQuery(data=f"image_group_week_{_enc('ИКВ-11')}_1", user_id=1)

    asyncio.run(group.process_group_week_navigation(cb))

    assert len(cb.message.photos) == 1


# --- personal: пресеты дня и картинка ----------------------------------------

def test_process_image_week_renders_photo(reset_registries):
    lk_client.apis[1] = _FakeUserAPI()
    cb = FakeCallbackQuery(data="image_week_0", user_id=1)

    asyncio.run(personal.process_image_week(cb))

    assert len(cb.message.photos) == 1


def test_process_image_week_unauthorized(reset_registries):
    cb = FakeCallbackQuery(data="image_week_0", user_id=1)

    asyncio.run(personal.process_image_week(cb))

    assert any(a and "авторизуйтесь" in a for a in cb.answers)


def test_process_my_day_renders(reset_registries):
    lk_client.apis[1] = _FakeUserAPI()
    cb = FakeCallbackQuery(data="my_day_0", user_id=1)

    asyncio.run(personal.process_my_day(cb))

    assert len(cb.message.edits) == 1


# --- common: cb_sched_reload -------------------------------------------------

def test_cb_sched_reload_reports_count(monkeypatch):
    async def _fake_reload(**kwargs):
        return {'ИКВ-11': [], 'ИКВ-12': [], 'ИКВ-13': []}

    monkeypatch.setattr(sched_common, "get_all_groups_timetable", _fake_reload)
    cb = FakeCallbackQuery(data="m:sched:reload", user_id=1)

    asyncio.run(sched_common.cb_sched_reload(cb, FakeState()))

    status_msg = cb.message.replies[0]
    assert any("3 групп" in e['text'] for e in status_msg.edits)
