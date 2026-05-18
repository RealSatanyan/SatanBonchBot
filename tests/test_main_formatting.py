"""Тесты форматирования расписания и клавиатур навигации по неделям."""
import main


LESSON = {
    "Число": "2026.02.10",
    "День недели": "Понедельник",
    "Номер недели": 1,
    "Номер занятия": "1",
    "Время занятия": "09:00-10:35",
    "Предмет": "Математический анализ",
    "ФИО преподавателя": "Иванов И.И.",
    "Номер кабинета": "ауд. 401",
    "Тип занятия": "лекция",
    "Группа": "ИКВ-11",
}


def _callbacks(kb):
    return [btn.callback_data for row in kb.inline_keyboard for btn in row]


# --- format_timetable_dict ---------------------------------------------------

def test_format_timetable_dict_string_input_is_error():
    assert main.format_timetable_dict("Ошибка сервера") == "❌ Ошибка сервера"


def test_format_timetable_dict_empty_list():
    assert main.format_timetable_dict([]) == "📅 Расписание пусто"


def test_format_timetable_dict_week_filter_no_matches():
    result = main.format_timetable_dict([LESSON], week_number=99)
    assert "Нет занятий на неделе №99" in result


def test_format_timetable_dict_renders_lesson():
    result = main.format_timetable_dict([LESSON], title="Моё расписание")
    assert "Моё расписание" in result
    assert "2026.02.10" in result
    assert "Математический анализ" in result
    assert "Иванов И.И." in result
    assert "ауд. 401" in result


def test_format_timetable_dict_week_number_in_header():
    result = main.format_timetable_dict([LESSON], week_number=1)
    assert "Неделя №1" in result


# --- get_week_navigation_buttons ---------------------------------------------

def test_week_navigation_buttons_offsets():
    callbacks = _callbacks(main.get_week_navigation_buttons(week_offset=2))
    assert "prev_week_1" in callbacks
    assert "next_week_3" in callbacks
    assert "current_week_0" in callbacks
    assert "image_week_2" in callbacks


# --- get_teacher_week_navigation_buttons -------------------------------------

def test_teacher_navigation_buttons_encode_name():
    kb = main.get_teacher_week_navigation_buttons("Иванов И.И.", week_number=3)
    callbacks = _callbacks(kb)
    # Имя кодируется в base64 — в callback_data не должно быть кириллицы.
    assert any(cb.startswith("prev_teacher_week_") for cb in callbacks)
    assert any(cb.startswith("all_teacher_weeks_") for cb in callbacks)
    assert all(cb.isascii() for cb in callbacks)


def test_teacher_navigation_buttons_default_week():
    # week_number=None трактуется как 0.
    callbacks = _callbacks(main.get_teacher_week_navigation_buttons("Петров"))
    assert any(cb.endswith("_-1") for cb in callbacks)
    assert any(cb.endswith("_1") for cb in callbacks)
