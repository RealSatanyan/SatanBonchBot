"""Тесты форматирования расписания и клавиатур навигации по неделям."""
from satanbonchbot import formatting, keyboards


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
    assert formatting.format_timetable_dict("Ошибка сервера") == "❌ Ошибка сервера"


def test_format_timetable_dict_empty_list():
    assert formatting.format_timetable_dict([]) == "📅 Расписание пусто"


def test_format_timetable_dict_week_filter_no_matches():
    result = formatting.format_timetable_dict([LESSON], week_number=99)
    assert "Нет занятий на неделе №99" in result


def test_format_timetable_dict_renders_lesson():
    result = formatting.format_timetable_dict([LESSON], title="Моё расписание")
    assert "Моё расписание" in result
    assert "2026.02.10" in result
    assert "Математический анализ" in result
    assert "Иванов И.И." in result
    assert "ауд. 401" in result


def test_format_timetable_dict_week_number_in_header():
    result = formatting.format_timetable_dict([LESSON], week_number=1)
    assert "Неделя №1" in result


def test_format_timetable_dict_shows_full_name_and_building():
    """B.1: при наличии полного ФИО и корпуса показываем их."""
    lesson = {
        **LESSON,
        "ФИО преподавателя (полное)": "Иванов Иван Иванович",
        "Номер кабинета": "401",
        "Корпус": "Б22/1",
    }
    result = formatting.format_timetable_dict([lesson])
    assert "Иванов Иван Иванович" in result
    assert "корпус Б22/1" in result


def test_format_timetable_dict_falls_back_to_short_name():
    """B.1: без полного ФИО показываем краткое; без корпуса — только аудиторию."""
    result = formatting.format_timetable_dict([LESSON])
    assert "Иванов И.И." in result
    assert "корпус" not in result


def test_format_timetable_dict_escapes_markdown_special_chars():
    """Регрессия: '_'/'*'/'`'/'[' в скрейпленном предмете/преподавателе/
    аудитории роняли ВСЁ сообщение (parse_mode='Markdown', 'can't parse
    entities') вместо показа одного, но валидного, расписания группы/
    аудитории/преподавателя."""
    lesson = {
        **LESSON,
        "Предмет": "Мат_анализ",
        "ФИО преподавателя": "Иванов_И.И.",
        "Номер кабинета": "[401]",
    }
    result = formatting.format_timetable_dict([lesson])
    assert "Мат\\_анализ" in result
    assert "Иванов\\_И.И." in result
    # Экранируем именно '[' (спецсимвол legacy Markdown, начало ссылки);
    # закрывающая ']' сама по себе entity не запускает и экранирования не требует.
    assert "\\[401]" in result


# --- get_week_navigation_buttons ---------------------------------------------

def test_week_navigation_buttons_offsets():
    callbacks = _callbacks(keyboards.get_week_navigation_buttons(week_offset=2))
    assert "prev_week_1" in callbacks
    assert "next_week_3" in callbacks
    assert "current_week_0" in callbacks
    assert "image_week_2" in callbacks


# --- get_teacher_week_navigation_buttons -------------------------------------

def test_teacher_navigation_buttons_encode_name():
    kb = keyboards.get_teacher_week_navigation_buttons("Иванов И.И.", week_number=3)
    callbacks = _callbacks(kb)
    # Имя кодируется в base64 — в callback_data не должно быть кириллицы.
    assert any(cb.startswith("prev_teacher_week_") for cb in callbacks)
    assert any(cb.startswith("all_teacher_weeks_") for cb in callbacks)
    assert all(cb.isascii() for cb in callbacks)


def test_teacher_navigation_buttons_default_week():
    # week_number=None трактуется как 0.
    callbacks = _callbacks(keyboards.get_teacher_week_navigation_buttons("Петров"))
    assert any(cb.endswith("_-1") for cb in callbacks)
    assert any(cb.endswith("_1") for cb in callbacks)


def test_teacher_navigation_buttons_long_name_stays_within_callback_limit():
    """Точный поиск по полному ФИО (чтобы отсеять однофамильцев) — обычный
    сценарий в вузе. base64 полного ФИО + префикс кнопки + номер недели мог
    превысить лимит Telegram в 64 байта на callback_data, и вся клавиатура
    (а с ней и всё сообщение с уже готовым расписанием) падала с
    BUTTON_DATA_INVALID."""
    kb = keyboards.get_teacher_week_navigation_buttons("Иванов Иван Иванович", week_number=3)
    callbacks = _callbacks(kb)
    assert callbacks
    assert all(len(cb.encode("utf-8")) <= 64 for cb in callbacks)


def test_classroom_navigation_buttons_long_name_stays_within_callback_limit():
    kb = keyboards.get_classroom_week_navigation_buttons("Лабораторный корпус, ауд. 401-Б", week_number=3)
    callbacks = _callbacks(kb)
    assert callbacks
    assert all(len(cb.encode("utf-8")) <= 64 for cb in callbacks)


def test_group_navigation_buttons_long_name_stays_within_callback_limit():
    kb = keyboards.get_group_week_navigation_buttons("Очень-длинное-название-группы-ИКВ-11-доп", week_number=3)
    callbacks = _callbacks(kb)
    assert callbacks
    assert all(len(cb.encode("utf-8")) <= 64 for cb in callbacks)
