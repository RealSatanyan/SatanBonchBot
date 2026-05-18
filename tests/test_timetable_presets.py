"""Тесты пресетов расписания «Сегодня» / «Завтра»: фильтр по дате, week_offset."""
from datetime import date
from types import SimpleNamespace

import main


# --- filter_group_lessons_by_date (дикт-формат, поле 'Число') ----------------

def _group_lesson(date_str: str, subject: str = "Матан") -> dict:
    return {"Число": date_str, "Предмет": subject, "День недели": "Понедельник"}


def test_filter_group_keeps_only_matching_date():
    timetable = [
        _group_lesson("2026.05.18", "Матан"),
        _group_lesson("2026.05.19", "Физика"),
        _group_lesson("2026.05.18", "История"),
    ]
    result = main.filter_group_lessons_by_date(timetable, "2026.05.18")
    assert [l["Предмет"] for l in result] == ["Матан", "История"]


def test_filter_group_empty_when_no_match():
    timetable = [_group_lesson("2026.05.19")]
    assert main.filter_group_lessons_by_date(timetable, "2026.05.18") == []


def test_filter_group_handles_non_list_input():
    assert main.filter_group_lessons_by_date("ошибка парсера", "2026.05.18") == []
    assert main.filter_group_lessons_by_date(None, "2026.05.18") == []


# --- filter_personal_lessons_by_date (объекты ЛК, атрибут .date) -------------

def test_filter_personal_keeps_only_matching_date():
    timetable = [
        SimpleNamespace(date="2026-05-18", subject="Матан"),
        SimpleNamespace(date="2026-05-19", subject="Физика"),
        SimpleNamespace(date="2026-05-18", subject="История"),
    ]
    result = main.filter_personal_lessons_by_date(timetable, "2026-05-18")
    assert [l.subject for l in result] == ["Матан", "История"]


def test_filter_personal_empty_when_no_match_or_empty():
    timetable = [SimpleNamespace(date="2026-05-19", subject="Физика")]
    assert main.filter_personal_lessons_by_date(timetable, "2026-05-18") == []
    assert main.filter_personal_lessons_by_date([], "2026-05-18") == []
    assert main.filter_personal_lessons_by_date(None, "2026-05-18") == []


# --- _week_offset_for_date ---------------------------------------------------

def test_week_offset_today_is_zero():
    today = date(2026, 5, 20)  # среда
    assert main._week_offset_for_date(today, today) == 0


def test_week_offset_tomorrow_same_week():
    today = date(2026, 5, 20)  # среда
    assert main._week_offset_for_date(date(2026, 5, 21), today) == 0


def test_week_offset_tomorrow_crosses_into_next_week():
    today = date(2026, 5, 24)  # воскресенье
    tomorrow = date(2026, 5, 25)  # понедельник следующей недели
    assert main._week_offset_for_date(tomorrow, today) == 1


def test_week_offset_previous_week_is_negative():
    today = date(2026, 5, 20)  # среда
    assert main._week_offset_for_date(date(2026, 5, 11), today) == -1
