"""Тесты уведомлений об изменении расписания (задача C.1).

diff_group_timetable — чистая логика (дифф двух снимков расписания группы).
notify_schedule_changes — рассылка; Telegram замокан фейковым bot.
"""
import asyncio

import db
import timetable_service
from timetable_service import diff_group_timetable, notify_schedule_changes


def _lesson(week=1, day=0, num="1", time="09:00-10:35", subject="Физика",
            ltype="Лекция", teacher="Иванов И.И.", room="401", building="Б22/1"):
    return {
        'Группа': 'ИКВ-11',
        'Номер недели': week,
        'Номер дня недели': day,
        'Номер занятия': num,
        'Время занятия': time,
        'Предмет': subject,
        'Тип занятия': ltype,
        'ФИО преподавателя': teacher,
        'Номер кабинета': room,
        'Корпус': building,
    }


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({'chat_id': chat_id, 'text': text})


# --- diff_group_timetable ----------------------------------------------------

def test_diff_no_changes():
    lessons = [_lesson(subject="Физика"), _lesson(subject="Химия")]
    diff = diff_group_timetable(lessons, list(lessons))
    assert diff['added'] == []
    assert diff['removed'] == []


def test_diff_ignores_lesson_order():
    a, b = _lesson(subject="Физика"), _lesson(subject="Химия")
    diff = diff_group_timetable([a, b], [b, a])
    assert diff['added'] == []
    assert diff['removed'] == []


def test_diff_detects_added_lesson():
    old = [_lesson(subject="Физика")]
    new = [_lesson(subject="Физика"), _lesson(subject="Химия")]
    diff = diff_group_timetable(old, new)
    assert len(diff['added']) == 1
    assert diff['added'][0]['Предмет'] == "Химия"
    assert diff['removed'] == []


def test_diff_detects_removed_lesson():
    old = [_lesson(subject="Физика"), _lesson(subject="Химия")]
    new = [_lesson(subject="Физика")]
    diff = diff_group_timetable(old, new)
    assert len(diff['removed']) == 1
    assert diff['removed'][0]['Предмет'] == "Химия"


def test_diff_teacher_change_is_add_plus_remove():
    old = [_lesson(teacher="Иванов И.И.")]
    new = [_lesson(teacher="Петров П.П.")]
    diff = diff_group_timetable(old, new)
    assert len(diff['added']) == 1 and diff['added'][0]['ФИО преподавателя'] == "Петров П.П."
    assert len(diff['removed']) == 1 and diff['removed'][0]['ФИО преподавателя'] == "Иванов И.И."


def test_diff_handles_non_list_inputs():
    assert diff_group_timetable(None, [_lesson()])['added']
    assert diff_group_timetable("Ошибка сервера", [_lesson()])['added']
    assert diff_group_timetable([_lesson()], None)['removed']


# --- notify_schedule_changes -------------------------------------------------

def _add_user(temp_db, user_id, group):
    temp_db.execute(
        "INSERT INTO users (user_id, email, password, group_name) VALUES (?, 'e', 'p', ?)",
        (user_id, group),
    )
    temp_db.commit()


def test_notify_sends_only_to_affected_group(monkeypatch, temp_db):
    fake_bot = _FakeBot()
    monkeypatch.setattr(timetable_service, "bot", fake_bot)
    _add_user(temp_db, 1, "ИКВ-11")
    _add_user(temp_db, 2, "ИКВ-11")
    _add_user(temp_db, 3, "ИКВ-12")

    old = {"ИКВ-11": [_lesson(subject="Физика")], "ИКВ-12": [_lesson(subject="Химия")]}
    new = {"ИКВ-11": [_lesson(subject="Физика"), _lesson(subject="Сети")],
           "ИКВ-12": [_lesson(subject="Химия")]}

    asyncio.run(notify_schedule_changes(old, new))

    notified = {s['chat_id'] for s in fake_bot.sent}
    assert notified == {1, 2}  # пользователи ИКВ-11; пользователь ИКВ-12 — нет
    assert "ИКВ-11" in fake_bot.sent[0]['text']


def test_notify_silent_when_no_changes(monkeypatch, temp_db):
    fake_bot = _FakeBot()
    monkeypatch.setattr(timetable_service, "bot", fake_bot)
    _add_user(temp_db, 1, "ИКВ-11")

    cache = {"ИКВ-11": [_lesson()]}
    asyncio.run(notify_schedule_changes(cache, {"ИКВ-11": [_lesson()]}))

    assert fake_bot.sent == []


def test_notify_skips_group_absent_in_old(monkeypatch, temp_db):
    fake_bot = _FakeBot()
    monkeypatch.setattr(timetable_service, "bot", fake_bot)
    _add_user(temp_db, 1, "ИКВ-11")

    # ИКВ-11 только в новом снимке (раньше не загружалась) — не уведомляем.
    asyncio.run(notify_schedule_changes({}, {"ИКВ-11": [_lesson()]}))

    assert fake_bot.sent == []


def test_notify_skips_mass_change(monkeypatch, temp_db):
    fake_bot = _FakeBot()
    monkeypatch.setattr(timetable_service, "bot", fake_bot)
    _add_user(temp_db, 1, "ИКВ-11")

    old = {"ИКВ-11": []}
    # Изменений больше порога — похоже на массовую перезагрузку, не уведомляем.
    new = {"ИКВ-11": [_lesson(num=str(i), subject=f"Предмет {i}") for i in range(40)]}

    asyncio.run(notify_schedule_changes(old, new))

    assert fake_bot.sent == []
