"""Тесты BonchAPI из TImetabels.py: разбор времени, фильтры, JSON, неделя."""
import json

import pytest

from TImetabels import BonchAPI


SAMPLE_TIMETABLE = {
    "ИКВ-11": [
        {
            "ФИО преподавателя": "Иванов И.И.", "Номер кабинета": "ауд. 401",
            "Номер недели": 1, "Номер дня недели": 1, "Время занятия": "13:00-14:35",
            "Предмет": "Физика",
        },
        {
            "ФИО преподавателя": "Иванов И.И.", "Номер кабинета": "ауд. 401",
            "Номер недели": 1, "Номер дня недели": 0, "Время занятия": "09:00-10:35",
            "Предмет": "Матанализ",
        },
    ],
    "ИКВ-12": [
        {
            "ФИО преподавателя": "Петров П.П.", "Номер кабинета": "ауд. 512",
            "Номер недели": 2, "Номер дня недели": 0, "Время занятия": "10:45-12:20",
            "Предмет": "Химия",
        },
    ],
}


# --- parse_lesson_time -------------------------------------------------------

def test_parse_lesson_time_extracts_start_time():
    assert BonchAPI.parse_lesson_time("13:00-14:35").strftime("%H:%M") == "13:00"


def test_parse_lesson_time_empty_returns_midnight():
    assert BonchAPI.parse_lesson_time("").strftime("%H:%M") == "00:00"
    assert BonchAPI.parse_lesson_time(None).strftime("%H:%M") == "00:00"


def test_parse_lesson_time_garbage_returns_midnight():
    assert BonchAPI.parse_lesson_time("не время").strftime("%H:%M") == "00:00"


# --- teacher_timetable -------------------------------------------------------

def test_teacher_timetable_collects_lessons_across_groups():
    lessons = BonchAPI.teacher_timetable(SAMPLE_TIMETABLE, "Иванов")
    assert len(lessons) == 2
    assert {l["Предмет"] for l in lessons} == {"Физика", "Матанализ"}


def test_teacher_timetable_sorted_by_week_day_time():
    lessons = BonchAPI.teacher_timetable(SAMPLE_TIMETABLE, "Иванов")
    keys = [(l["Номер недели"], l["Номер дня недели"]) for l in lessons]
    assert keys == sorted(keys)


def test_teacher_timetable_unknown_teacher_returns_empty():
    assert BonchAPI.teacher_timetable(SAMPLE_TIMETABLE, "Сидоров") == []


# --- classroom_timetable -----------------------------------------------------

def test_classroom_timetable_filters_by_room_substring():
    lessons = BonchAPI.classroom_timetable(SAMPLE_TIMETABLE, "401")
    assert len(lessons) == 2
    assert all("401" in l["Номер кабинета"] for l in lessons)


def test_classroom_timetable_unknown_room_returns_empty():
    assert BonchAPI.classroom_timetable(SAMPLE_TIMETABLE, "999") == []


# --- _parse_id_name_pairs (делегат в parsers) --------------------------------

def test_parse_id_name_pairs_delegates():
    assert BonchAPI._parse_id_name_pairs("1,Физика;2,Химия") == {"1": "Физика", "2": "Химия"}


# --- format_output -----------------------------------------------------------

def test_format_output_empty_timetable():
    assert "Нет занятий" in BonchAPI.format_output([])


def test_format_output_no_lessons_for_requested_week():
    lesson = {"Номер недели": 1}
    assert "Нет занятий для недели 5" in BonchAPI.format_output([lesson], week_number=5)


# --- save_to_json / load_from_json -------------------------------------------

def test_save_and_load_json_roundtrip(tmp_path):
    path = str(tmp_path / "timetable.json")
    BonchAPI.save_to_json(SAMPLE_TIMETABLE, path)
    assert BonchAPI.load_from_json(path) == SAMPLE_TIMETABLE


def test_save_to_json_writes_valid_utf8(tmp_path):
    path = tmp_path / "tt.json"
    BonchAPI.save_to_json({"ИКВ-11": []}, str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "ИКВ-11" in data


def test_load_from_json_missing_file_returns_none(tmp_path):
    assert BonchAPI.load_from_json(str(tmp_path / "missing.json")) is None


# --- set_current_week --------------------------------------------------------

def test_current_week_zero_when_first_day_in_future():
    api = BonchAPI("2099-01-01")
    assert api.cur_week == 0


def test_constructor_parses_first_day():
    api = BonchAPI("2026-02-03")
    assert api.first_day.year == 2026
    assert api.first_day.month == 2
    assert api.first_day.day == 3
