"""Тесты обогащения личного расписания (B.1+).

Личная страница ЛК (raspisanie.php) не содержит полных ФИО — их подтягивают
из расписания группы по (дата, время, предмет). Корпус разбивается из поля
location, которое уже содержит «аудитория; корпус».
"""
from types import SimpleNamespace

from satanbonchbot.formatting import  format_timetable


def _personal(date="2026-05-18", day="Понедельник", time="13:00-14:35",
              subject="Физика", teacher="Иванов И.И.",
              location="214; Б22/1", lesson_type="Лекция"):
    return SimpleNamespace(
        date=date, day=day, time=time, subject=subject,
        teacher=teacher, location=location, lesson_type=lesson_type,
    )


def _group(day="Понедельник", time="13:00-14:35", subject="Физика",
           full="Иванов Иван Иванович", date="2026.02.10"):
    # Сопоставление идёт по дню недели: расписание группы хранит даты от
    # начала семестра (февраль), а не текущей недели.
    return {
        'Число': date,
        'День недели': day,
        'Время занятия': time,
        'Предмет': subject,
        'ФИО преподавателя': 'Иванов И.И.',
        'ФИО преподавателя (полное)': full,
    }


# --- разбивка корпуса (без расписания группы) --------------------------------

def test_personal_location_split_into_room_and_building():
    text = format_timetable([_personal(location="214; Б22/1")])
    assert "🏫 214 · корпус Б22/1" in text


def test_personal_location_without_building():
    text = format_timetable([_personal(location="ДОТ")])
    assert "🏫 ДОТ" in text
    assert "корпус" not in text


# --- обогащение полным ФИО ---------------------------------------------------

def test_personal_enriched_with_full_name():
    text = format_timetable([_personal()], group_timetable=[_group()])
    assert "Иванов Иван Иванович" in text


def test_personal_match_by_weekday_not_date():
    """Сопоставление идёт по дню недели: даты личного (май) и группового
    (февраль, от начала семестра) расписаний не совпадают — это нормально."""
    personal = _personal(date="2026-05-18", day="Понедельник")
    group = _group(date="2026.02.10", day="Понедельник")
    text = format_timetable([personal], group_timetable=[group])
    assert "Иванов Иван Иванович" in text


def test_personal_no_match_on_different_weekday():
    personal = _personal(day="Понедельник")
    group = _group(day="Вторник")
    text = format_timetable([personal], group_timetable=[group])
    assert "Иванов И.И." in text
    assert "Иванович" not in text


def test_personal_falls_back_to_short_name_without_group():
    text = format_timetable([_personal()])
    assert "Иванов И.И." in text
    assert "Иванович" not in text


def test_personal_no_match_keeps_short_name():
    # В расписании группы другой предмет — сопоставления нет.
    text = format_timetable([_personal(subject="Физика")],
                            group_timetable=[_group(subject="Химия")])
    assert "Иванов И.И." in text
    assert "Иванович" not in text


def test_personal_match_tolerates_separator_differences():
    # Время в личном расписании с точками, в расписании группы — с двоеточиями.
    personal = _personal(time="13.30-15.00")
    group = _group(time="13:30-15:00")
    text = format_timetable([personal], group_timetable=group_timetable_list(group))
    assert "Иванов Иван Иванович" in text


def group_timetable_list(group):
    return [group]


def test_personal_enrichment_handles_empty_group_timetable():
    # Пустой/некорректный group_timetable не должен ломать форматирование.
    assert "Физика" in format_timetable([_personal()], group_timetable=[])
    assert "Физика" in format_timetable([_personal()], group_timetable=None)
    assert "Физика" in format_timetable([_personal()], group_timetable="мусор")
