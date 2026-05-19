"""Smoke-тесты rendering.py: картинка расписания генерируется, файл не пуст.

rendering.py рисует PNG через PIL. Полную вёрстку не проверяем — убеждаемся,
что функция отрабатывает без исключений и создаёт непустой файл. Сгенерированные
PNG удаляются фикстурой cleanup_images.
"""
import os
import glob
from types import SimpleNamespace

import pytest

from satanbonchbot.rendering import  (
    generate_timetable_image,
    generate_timetable_image_from_dict,
    _format_personal_lesson_info,
)
from satanbonchbot.formatting import  _build_full_name_index


def _lesson(**overrides):
    """Занятие в формате parsers.parse_timetable_table."""
    base = {
        'Группа': 'ИКВ-11',
        'Число': '2026.05.18',
        'День недели': 'Понедельник',
        'Номер недели': 1,
        'Номер дня недели': 0,
        'Номер занятия': 1,
        'Время занятия': '09:00-10:35',
        'Предмет': 'Базы данных',
        'Тип занятия': 'Лекция',
        'ФИО преподавателя': 'Иванов И.И.',
        'Номер кабудитории': '',
        'Номер кабинета': 'ауд. 401',
    }
    base.update(overrides)
    return base


@pytest.fixture
def cleanup_images():
    """Удаляет timetable_*.png, созданные тестом."""
    before = set(glob.glob("timetable_*.png"))
    yield
    for path in set(glob.glob("timetable_*.png")) - before:
        try:
            os.remove(path)
        except OSError:
            pass


def _assert_nonempty_png(path):
    assert os.path.exists(path), f"файл {path} не создан"
    assert os.path.getsize(path) > 0, f"файл {path} пустой"
    with open(path, 'rb') as f:
        assert f.read(8) == b'\x89PNG\r\n\x1a\n', "файл не является PNG"


def test_generate_image_from_dict_with_lessons(cleanup_images):
    path = generate_timetable_image_from_dict(
        [_lesson(), _lesson(День='Понедельник', Предмет='Сети')],
        title="Расписание группы ИКВ-11",
        week_number=1,
        group_name="ИКВ-11",
    )
    _assert_nonempty_png(path)


def test_generate_image_from_dict_empty_timetable(cleanup_images):
    path = generate_timetable_image_from_dict([], title="Пусто", group_name="ИКВ-11")
    _assert_nonempty_png(path)


def test_generate_image_from_dict_no_lessons_for_week(cleanup_images):
    path = generate_timetable_image_from_dict(
        [_lesson(**{'Номер недели': 1})],
        week_number=99,
        group_name="ИКВ-11",
    )
    _assert_nonempty_png(path)


def test_generate_image_from_dict_filename_includes_week(cleanup_images):
    path = generate_timetable_image_from_dict([_lesson()], week_number=1, group_name="ИКВ-11")
    assert "week_1" in path


def _personal_lesson(day, **overrides):
    """Занятие личного расписания — объект с атрибутами (формат ЛК)."""
    base = dict(
        date="2026-05-18",
        day=day,
        time="09:00-10:35",
        subject="Базы данных",
        teacher="Иванов И.И.",
        location="ауд. 401",
        lesson_type="Лекция",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def cleanup_personal_image():
    """Удаляет timetable.png, созданный generate_timetable_image."""
    yield
    if os.path.exists("timetable.png"):
        try:
            os.remove("timetable.png")
        except OSError:
            pass


def test_generate_timetable_image_personal_format(cleanup_personal_image):
    timetable = [
        _personal_lesson("Понедельник"),
        _personal_lesson("Вторник", date="2026-05-19", subject="Сети"),
    ]
    path = generate_timetable_image(timetable)
    _assert_nonempty_png(path)


# --- B.2: обогащение картинки личного расписания полным ФИО ------------------

def test_personal_lesson_info_uses_full_name_from_group():
    """В картинке подставляется полное ФИО из расписания группы + корпус."""
    lesson = _personal_lesson(
        "Понедельник", time="13:00-14:35", subject="Физика",
        teacher="Иванов И.И.", location="214; Б22/1",
    )
    index = _build_full_name_index([{
        "День недели": "Понедельник", "Время занятия": "13:00-14:35",
        "Предмет": "Физика", "ФИО преподавателя (полное)": "Иванов Иван Иванович",
    }])

    info = _format_personal_lesson_info(lesson, index)

    assert "Иванов Иван Иванович" in info
    assert "корпус Б22/1" in info


def test_personal_lesson_info_falls_back_to_short_name():
    """Без совпадения в расписании группы остаётся краткое ФИО."""
    lesson = _personal_lesson("Понедельник", teacher="Петров П.П.")

    info = _format_personal_lesson_info(lesson, {})

    assert "Петров П.П." in info


def test_generate_timetable_image_accepts_group_timetable(cleanup_personal_image):
    """generate_timetable_image принимает расписание группы и не падает."""
    timetable = [_personal_lesson("Понедельник")]
    group = [{
        "День недели": "Понедельник", "Время занятия": "09:00-10:35",
        "Предмет": "Базы данных", "ФИО преподавателя (полное)": "Иванов Иван Иванович",
    }]

    path = generate_timetable_image(timetable, group_timetable=group)

    _assert_nonempty_png(path)
