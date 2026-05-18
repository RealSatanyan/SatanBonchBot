"""Тесты чистых парсеров (parsers.py).

Парсинг lk.sut.ru / cabinet.sut.ru хрупкий — при изменении вёрстки сайта
парсер ломается молча. Эти тесты фиксируют ожидаемое поведение: если разбор
сломается, тест упадёт явно, а не вернёт «0 групп без ошибки».
"""
from datetime import datetime

import parsers


# --- parse_id_name_pairs -----------------------------------------------------

def test_parse_id_name_pairs_basic():
    result = parsers.parse_id_name_pairs("1,Физика;2,Химия")
    assert result == {"1": "Физика", "2": "Химия"}


def test_parse_id_name_pairs_empty_input_returns_empty_dict():
    assert parsers.parse_id_name_pairs("") == {}
    assert parsers.parse_id_name_pairs(None) == {}


def test_parse_id_name_pairs_skips_malformed_chunks():
    # Чанк без запятой, пустое значение и лишние пробелы — отбрасываются.
    result = parsers.parse_id_name_pairs("  1 , Физика ;  плохо  ; 2, ;3,Химия")
    assert result == {"1": "Физика", "3": "Химия"}


def test_parse_id_name_pairs_keeps_comma_inside_name():
    # partition по первой запятой: запятая внутри названия остаётся в значении.
    result = parsers.parse_id_name_pairs("7,Группа, спецкурс")
    assert result == {"7": "Группа, спецкурс"}


# --- parse_week_number -------------------------------------------------------

def test_parse_week_number_from_h3_header(load_fixture):
    html = load_fixture("raspisanie_with_lessons.html")
    assert parsers.parse_week_number(html) == 15


def test_parse_week_number_empty_html_returns_zero():
    assert parsers.parse_week_number("") == 0


def test_parse_week_number_missing_header_returns_zero():
    assert parsers.parse_week_number("<html><body><p>нет недели</p></body></html>") == 0


def test_parse_week_number_fallback_to_page_text():
    # Нет h3/h2, но в тексте страницы есть "Неделя №7".
    html = "<html><body><div>Расписание. Неделя №7. Дальше...</div></body></html>"
    assert parsers.parse_week_number(html) == 7


# --- parse_week_param --------------------------------------------------------

def test_parse_week_param_from_bold_showweek(load_fixture):
    # Текущая неделя помечена <b> внутри ссылки showweek(38).
    html = load_fixture("raspisanie_with_lessons.html")
    assert parsers.parse_week_param(html) == 38


def test_parse_week_param_empty_html_returns_zero():
    assert parsers.parse_week_param("") == 0


def test_parse_week_param_fallback_to_open_zan():
    # Нет выделенной showweek-ссылки — берём week из open_zan(rasp, week).
    html = '<a onclick="open_zan(500, 42)">Начать занятие</a>'
    assert parsers.parse_week_param(html) == 42


def test_parse_week_param_fallback_to_raw_open_zan_regex():
    # Нет тегов <a> вообще — week_param берётся regex'ом по сырому HTML.
    html = '<script>var s = "open_zan(1, 77)";</script>'
    assert parsers.parse_week_param(html) == 77


def test_parse_week_param_no_data_returns_zero():
    assert parsers.parse_week_param("<html><body>нет недели</body></html>") == 0


# --- extract_start_lesson_ids ------------------------------------------------

def test_extract_start_lesson_ids_dedupes_and_filters(load_fixture):
    html = load_fixture("raspisanie_with_lessons.html")
    # 1001 встречается дважды -> схлопывается; update_zan(9999) не попадает.
    assert parsers.extract_start_lesson_ids(html) == ("1001", "1002")


def test_extract_start_lesson_ids_no_candidates(load_fixture):
    html = load_fixture("raspisanie_no_candidates.html")
    assert parsers.extract_start_lesson_ids(html) == ()


def test_extract_start_lesson_ids_empty_html():
    assert parsers.extract_start_lesson_ids("") == ()


def test_extract_start_lesson_ids_requires_button_text():
    # open_zan есть, но текст ссылки не «Начать занятие» — не кандидат.
    html = '<a onclick="open_zan(7, 1)">Что-то другое</a>'
    assert parsers.extract_start_lesson_ids(html) == ()


# --- extract_lesson_ids_fallback ---------------------------------------------

def test_extract_lesson_ids_fallback_from_knop_ids(load_fixture):
    html = load_fixture("raspisanie_with_lessons.html")
    assert parsers.extract_lesson_ids_fallback(html) == ("1001", "1002")


def test_extract_lesson_ids_fallback_no_knop_returns_empty(load_fixture):
    html = load_fixture("raspisanie_no_candidates.html")
    assert parsers.extract_lesson_ids_fallback(html) == ()


# --- parse_timetable_table ---------------------------------------------------

FIRST_DAY = datetime(2026, 2, 3)


def test_parse_timetable_table_missing_table_returns_message():
    assert parsers.parse_timetable_table("<html></html>", "ИКВ-11", FIRST_DAY) == "Расписание не найдено"


def test_parse_timetable_table_empty_html_returns_message():
    assert parsers.parse_timetable_table("", "ИКВ-11", FIRST_DAY) == "Расписание не найдено"


def test_parse_timetable_table_extracts_all_lessons(load_fixture):
    html = load_fixture("group_timetable.html")
    lessons = parsers.parse_timetable_table(html, "ИКВ-11", FIRST_DAY)
    # МатАнализ недели 1 и 2 (пн), Физика неделя 2 (ср), Информатика неделя 1 (пн).
    assert len(lessons) == 4


def test_parse_timetable_table_sorted_by_week_then_day(load_fixture):
    html = load_fixture("group_timetable.html")
    lessons = parsers.parse_timetable_table(html, "ИКВ-11", FIRST_DAY)
    keys = [(x["Номер недели"], x["Номер дня недели"]) for x in lessons]
    assert keys == sorted(keys)


def test_parse_timetable_table_lesson_fields(load_fixture):
    html = load_fixture("group_timetable.html")
    lessons = parsers.parse_timetable_table(html, "ИКВ-11", FIRST_DAY)
    matan = next(x for x in lessons if x["Предмет"] == "Математический анализ" and x["Номер недели"] == 1)
    assert matan["Группа"] == "ИКВ-11"
    assert matan["День недели"] == "Понедельник"
    assert matan["Номер занятия"] == "1"
    assert matan["Время занятия"] == "09:00-10:35"
    assert matan["Тип занятия"] == "лекция"
    assert matan["ФИО преподавателя"] == "Иванов И.И."
    assert matan["Номер кабинета"] == "ауд. 401"
    # first_day + 1 неделя * 7 + день 0 = 2026-02-10
    assert matan["Число"] == "2026.02.10"


def test_parse_timetable_table_seventh_lesson_gets_fixed_time(load_fixture):
    html = load_fixture("group_timetable.html")
    lessons = parsers.parse_timetable_table(html, "ИКВ-11", FIRST_DAY)
    informatika = next(x for x in lessons if x["Предмет"] == "Информатика")
    assert informatika["Номер занятия"] == "7"
    assert informatika["Время занятия"] == "20:00-21:35"


def test_parse_timetable_table_expands_multiple_weeks(load_fixture):
    html = load_fixture("group_timetable.html")
    lessons = parsers.parse_timetable_table(html, "ИКВ-11", FIRST_DAY)
    matan_weeks = sorted(x["Номер недели"] for x in lessons if x["Предмет"] == "Математический анализ")
    assert matan_weeks == [1, 2]


def test_parse_timetable_table_skips_non_numeric_weeks():
    # Некорректный номер недели не должен ронять разбор — занятие пропускается.
    html = """
    <table class="simple-little-table"><tbody>
      <tr>
        <td>1 (09:00-10:35)</td>
        <td>
          <div class="pair">
            <span class="subect"><strong>Физика</strong></span>
            <span class="weeks">(битоен)</span>
          </div>
        </td>
        <td></td><td></td><td></td><td></td><td></td>
      </tr>
    </tbody></table>
    """
    assert parsers.parse_timetable_table(html, "ИКВ-11", FIRST_DAY) == []


# --- parse_message_rows ------------------------------------------------------

def test_parse_message_rows_no_table_returns_empty():
    assert parsers.parse_message_rows("<html><body>пусто</body></html>") == []


def test_parse_message_rows_extracts_all_messages(load_fixture):
    messages = parsers.parse_message_rows(load_fixture("messages.html"))
    # Строка id="header" не начинается с tr_ и не попадает в результат.
    assert [m["id"] for m in messages] == ["3737509", "3737510", "3737511"]


def test_parse_message_rows_unread_message_fields(load_fixture):
    messages = parsers.parse_message_rows(load_fixture("messages.html"))
    unread = next(m for m in messages if m["id"] == "3737509")
    assert unread["title"] == "Важное объявление о сессии"
    assert unread["date"] == "18.05.2026"
    assert unread["sender"] == "Деканат ИКСС"
    assert unread["is_unread"] is True
    assert unread["has_files"] is True
    assert unread["files"] == [{"name": "Расписание.pdf", "url": "/files/doc1.pdf"}]


def test_parse_message_rows_read_message_without_files(load_fixture):
    messages = parsers.parse_message_rows(load_fixture("messages.html"))
    read = next(m for m in messages if m["id"] == "3737510")
    assert read["is_unread"] is False
    assert read["has_files"] is False
    assert read["files"] == []


def test_parse_message_rows_applies_defaults_for_blank_cells(load_fixture):
    messages = parsers.parse_message_rows(load_fixture("messages.html"))
    blank = next(m for m in messages if m["id"] == "3737511")
    assert blank["title"] == "Без названия"
    assert blank["sender"] == "Неизвестно"


# --- parse_recipients --------------------------------------------------------

def test_parse_recipients_extracts_id_name_pairs(load_fixture):
    recipients = parsers.parse_recipients(load_fixture("recipients.html"))
    assert recipients == [
        {"id": 101, "label": "Иванов Иван Иванович (id=101)"},
        {"id": 202, "label": "Петрова Анна Сергеевна (id=202)"},
    ]


def test_parse_recipients_empty_input_returns_empty_list():
    assert parsers.parse_recipients("") == []
    assert parsers.parse_recipients(None) == []


def test_parse_recipients_ignores_rows_without_id():
    html = "<td>Просто текст без идентификатора</td>"
    assert parsers.parse_recipients(html) == []
