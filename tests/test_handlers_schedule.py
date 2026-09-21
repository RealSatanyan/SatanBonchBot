"""Точечные тесты обработчиков расписания (handlers/schedule/).

Хэндлеры aiogram вызываются напрямую с фейковыми Message / CallbackQuery /
FSMContext. Сеть и реестры замоканы; кэш расписания и lk_client.apis
изолируются фикстурами reset_timetable_service / reset_registries.
"""
import asyncio
import base64
import os
from types import SimpleNamespace

from satanbonchbot import lk_client
from satanbonchbot.handlers.schedule import  personal, group, teacher, room
from satanbonchbot.handlers.schedule import  common as sched_common


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


class _ExpiredSessionUserAPI:
    """Фейк DebuggableBonchAPI: сессия истекла (get_raw_timetable уже бросил бы это)."""

    async def get_timetable(self, week_offset=0):
        raise ValueError("Session expired - ERR_MSG from LK. Need to re-authenticate.")


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


def test_cmd_timetable_session_expired_prompts_relogin(reset_registries):
    """До фикса get_raw_timetable отдавал ERR_MSG как обычный HTML, парсер
    падал с AttributeError на 'NoneType' и юзер видел бесполезное 'попробуй
    позже' (или краш) вместо явного 'выполни /login заново'."""
    lk_client.apis[1] = _ExpiredSessionUserAPI()
    msg = FakeMessage(user_id=1)

    asyncio.run(personal.cmd_timetable(msg, uid=1))

    assert len(msg.answers) == 1
    assert "/login" in msg.answers[0]['text']


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


def test_cmd_teacher_timetable_shows_real_current_week(monkeypatch, reset_timetable_service):
    """Регрессия, продублированная и здесь (тот же баг, что и в group.py):
    раньше показывалась sorted(weeks)[0] вместо реальной текущей недели."""
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {
        'ИКВ-11': [_lesson(teacher="Петров П.П.", week=1), _lesson(teacher="Петров П.П.", week=5)],
    }
    monkeypatch.setattr(sched_common, "_current_semester_week", lambda: 5)
    msg = FakeMessage(user_id=1)

    asyncio.run(teacher.cmd_teacher_timetable(msg, override="Петров"))

    assert "Неделя №5" in msg.answers[0]['text']


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


def test_cmd_classroom_timetable_shows_real_current_week(monkeypatch, reset_timetable_service):
    """Тот же баг, что и в group.py/teacher.py: раньше показывалась
    sorted(weeks)[0] вместо реальной текущей недели."""
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {
        'ИКВ-11': [_lesson(room_no="ауд. 512", week=1), _lesson(room_no="ауд. 512", week=5)],
    }
    monkeypatch.setattr(sched_common, "_current_semester_week", lambda: 5)
    msg = FakeMessage(user_id=1)

    asyncio.run(room.cmd_classroom_timetable(msg, override="512"))

    assert "Неделя №5" in msg.answers[0]['text']


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


def test_cmd_group_timetable_shows_real_current_week(monkeypatch, reset_timetable_service):
    """Регрессия: раньше показывалась sorted(weeks)[0] — начало семестра —
    вместо реальной текущей недели (см. test_pick_current_week_* выше)."""
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {
        'ИКВ-11': [_lesson(week=1), _lesson(week=5), _lesson(week=10)],
    }

    async def _fake_api():
        return SimpleNamespace(groups_id={'1': 'ИКВ-11'})

    monkeypatch.setattr(group, "get_timetable_api", _fake_api)
    monkeypatch.setattr(sched_common, "_current_semester_week", lambda: 5)
    msg = FakeMessage(user_id=1)

    asyncio.run(group.cmd_group_timetable(msg, override="ИКВ-11"))

    assert "Неделя №5" in msg.answers[0]['text']


def test_cmd_group_timetable_splits_long_schedule_across_messages(monkeypatch, reset_timetable_service):
    """Длинное расписание группы разбивается на несколько сообщений под лимит
    Telegram; клавиатура — только на первом."""
    long_lessons = []
    for day in range(10):
        for i in range(4):
            lesson = dict(_lesson(
                teacher=f"Преподаватель № {i} с очень длинным ФИО для растягивания текста",
                week=1,
            ))
            lesson['Число'] = f"2026.05.{18 + day}"
            long_lessons.append(lesson)
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': long_lessons}

    async def _fake_api():
        return SimpleNamespace(groups_id={'1': 'ИКВ-11'})

    monkeypatch.setattr(group, "get_timetable_api", _fake_api)
    monkeypatch.setattr(sched_common, "_current_semester_week", lambda: 1)
    msg = FakeMessage(user_id=1)

    asyncio.run(group.cmd_group_timetable(msg, override="ИКВ-11"))

    assert len(msg.answers) > 1
    assert msg.answers[0].get('reply_markup') is not None
    assert all(a.get('reply_markup') is None for a in msg.answers[1:])
    assert all(len(a['text']) <= 4000 for a in msg.answers)


# --- common: pure хелперы (текущая неделя / обрезка длинных сообщений) ------

def test_pick_current_week_prefers_real_week_over_earliest(monkeypatch):
    """Регрессия: раньше "текущая неделя" по умолчанию бралась как
    sorted(weeks)[0] — буквально самая ранняя неделя в данных (начало
    семестра), а НЕ реальная календарная неделя. Студент, открывший
    /group_timetable (или /classroom_timetable, /teacher_timetable) в
    середине семестра, видел расписание недели №1 почти весь семестр,
    пока вручную не пролистает вперёд."""
    monkeypatch.setattr(sched_common, "_current_semester_week", lambda: 10)
    lessons = [_lesson(week=1), _lesson(week=10), _lesson(week=15)]

    assert sched_common.pick_current_week(lessons) == 10


def test_pick_current_week_falls_back_to_earliest_when_real_week_has_no_lessons(monkeypatch):
    """Реальная текущая неделя есть, но занятий на неё в данных нет (сессия/
    каникулы) — используем самую раннюю доступную, как и раньше."""
    monkeypatch.setattr(sched_common, "_current_semester_week", lambda: 20)
    lessons = [_lesson(week=1), _lesson(week=2)]

    assert sched_common.pick_current_week(lessons) == 1


def test_pick_current_week_empty_lessons_returns_none():
    assert sched_common.pick_current_week([]) is None


def test_truncate_for_telegram_leaves_short_text_untouched():
    assert sched_common.truncate_for_telegram("коротко", max_length=4000) == "коротко"


def test_truncate_for_telegram_cuts_long_text():
    text = "x" * 5000
    result = sched_common.truncate_for_telegram(text, max_length=4000)
    assert len(result) <= 4000 + len("\n\n... (сообщение обрезано, используйте навигацию по неделям)")
    assert result.startswith("x" * 4000)
    assert "обрезано" in result


def test_split_for_telegram_leaves_short_text_as_single_part():
    assert sched_common.split_for_telegram("коротко", max_length=4000) == ["коротко"]


def test_split_for_telegram_splits_on_day_boundaries():
    day = "----------------------\n📌 день\n" + ("x" * 3000) + "\n"
    text = day * 3  # ~9000+ символов, три «дня»

    parts = sched_common.split_for_telegram(text, max_length=4000)

    assert len(parts) > 1
    assert all(len(p) <= 4000 for p in parts)
    assert "".join(parts) == text


def test_split_for_telegram_truncates_when_no_day_boundary_to_split_on():
    text = "x" * 5000  # длинный текст без разделителя дня — резать некуда

    parts = sched_common.split_for_telegram(text, max_length=4000)

    assert len(parts) == 1
    assert "обрезано" in parts[0]


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


def test_process_group_week_navigation_cleans_temp_file_on_send_failure(reset_timetable_service, monkeypatch):
    """os.remove временного PNG раньше выполнялся только на успешном пути:
    сбой answer_photo (сеть/лимиты Telegram) оставлял файл на диске навсегда."""
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = {'ИКВ-11': [_lesson()]}

    generated_paths = []
    original_generate = group.generate_timetable_image_from_dict

    def _tracking_generate(*args, **kwargs):
        path = original_generate(*args, **kwargs)
        generated_paths.append(path)
        return path

    monkeypatch.setattr(group, "generate_timetable_image_from_dict", _tracking_generate)

    class _FailingMessage(FakeMessage):
        async def answer_photo(self, photo, **kwargs):
            raise RuntimeError("Telegram недоступен")

    cb = FakeCallbackQuery(data=f"image_group_week_{_enc('ИКВ-11')}_1", user_id=1)
    cb.message = _FailingMessage(user_id=1)

    asyncio.run(group.process_group_week_navigation(cb))

    assert generated_paths, "изображение должно было сгенерироваться"
    assert not os.path.exists(generated_paths[0]), "временный файл должен удаляться даже при сбое отправки"


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
