"""Тесты merge_lessons_by_groups — объединение групп-потоков (задача B.3).

Лекция-поток для нескольких групп = почти одинаковые занятия, отличающиеся
только полем 'Группа'. merge_lessons_by_groups сливает их в одну запись
с полем 'Группы'.
"""
from satanbonchbot.formatting import  merge_lessons_by_groups, format_timetable_dict


def _lesson(group, *, week=1, day=0, time="09:00-10:35", subject="Физика",
            lesson_type="Лекция", teacher="Иванов И.И.", room="ауд. 401",
            date="2026.05.18"):
    return {
        'Группа': group,
        'Число': date,
        'День недели': 'Понедельник',
        'Номер недели': week,
        'Номер дня недели': day,
        'Номер занятия': 1,
        'Время занятия': time,
        'Предмет': subject,
        'Тип занятия': lesson_type,
        'ФИО преподавателя': teacher,
        'Номер кабинета': room,
    }


# --- слияние -----------------------------------------------------------------

def test_merges_lessons_differing_only_by_group():
    merged = merge_lessons_by_groups([_lesson("ИКВ-11"), _lesson("ИКВ-12")])

    assert len(merged) == 1
    assert merged[0]['Группы'] == ['ИКВ-11', 'ИКВ-12']


def test_merged_groups_are_sorted():
    merged = merge_lessons_by_groups([
        _lesson("ИКВ-13"), _lesson("ИКВ-11"), _lesson("ИКВ-12"),
    ])

    assert merged[0]['Группы'] == ['ИКВ-11', 'ИКВ-12', 'ИКВ-13']


def test_duplicate_group_not_repeated():
    merged = merge_lessons_by_groups([_lesson("ИКВ-11"), _lesson("ИКВ-11")])

    assert len(merged) == 1
    assert merged[0].get('Группы') is None  # одна группа — поле не появляется


# --- не сливает разное -------------------------------------------------------

def test_different_subject_not_merged():
    merged = merge_lessons_by_groups([
        _lesson("ИКВ-11", subject="Физика"),
        _lesson("ИКВ-12", subject="Химия"),
    ])

    assert len(merged) == 2
    assert all('Группы' not in lesson for lesson in merged)


def test_different_time_not_merged():
    merged = merge_lessons_by_groups([
        _lesson("ИКВ-11", time="09:00-10:35"),
        _lesson("ИКВ-12", time="10:45-12:20"),
    ])

    assert len(merged) == 2


def test_different_room_not_merged():
    merged = merge_lessons_by_groups([
        _lesson("ИКВ-11", room="ауд. 401"),
        _lesson("ИКВ-12", room="ауд. 512"),
    ])

    assert len(merged) == 2


# --- единичная группа и порядок ----------------------------------------------

def test_single_group_lesson_unchanged():
    merged = merge_lessons_by_groups([_lesson("ИКВ-11")])

    assert len(merged) == 1
    assert merged[0]['Группа'] == "ИКВ-11"
    assert 'Группы' not in merged[0]


def test_order_preserved_by_first_appearance():
    merged = merge_lessons_by_groups([
        _lesson("ИКВ-11", time="09:00-10:35", subject="Физика"),
        _lesson("ИКВ-11", time="10:45-12:20", subject="Химия"),
        _lesson("ИКВ-12", time="09:00-10:35", subject="Физика"),
    ])

    assert [l['Предмет'] for l in merged] == ["Физика", "Химия"]
    assert merged[0]['Группы'] == ['ИКВ-11', 'ИКВ-12']


def test_empty_list_returns_empty():
    assert merge_lessons_by_groups([]) == []


def test_non_dict_entries_skipped():
    merged = merge_lessons_by_groups([_lesson("ИКВ-11"), "мусор", None])

    assert len(merged) == 1


# --- отображение в format_timetable_dict -------------------------------------

def test_format_shows_merged_groups():
    merged = merge_lessons_by_groups([_lesson("ИКВ-11"), _lesson("ИКВ-12")])
    text = format_timetable_dict(merged, "Расписание преподавателя")

    assert "👥 Группы: ИКВ-11, ИКВ-12" in text


def test_format_shows_single_group_as_before():
    text = format_timetable_dict([_lesson("ИКВ-11")], "Расписание группы")

    assert "👥 Группа: ИКВ-11" in text
    assert "Группы:" not in text
