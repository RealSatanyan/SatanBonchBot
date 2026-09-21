"""Smoke-тесты rendering.py: картинка расписания генерируется, файл не пуст.

rendering.py рисует PNG через PIL. Полную вёрстку не проверяем — убеждаемся,
что функция отрабатывает без исключений и создаёт непустой файл. Сгенерированные
PNG удаляются фикстурой cleanup_images.
"""
import os
import glob
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from satanbonchbot.rendering import  (
    generate_timetable_image,
    generate_timetable_image_from_dict,
    _personal_lesson_to_dict,
    _wrap_text_to_width,
    _lesson_entry_layout,
    _render_placeholder_image,
    _DAY_ORDER,
    _DAY_EMOJIS,
    LESSON_LINE_HEIGHT,
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

def test_personal_lesson_to_dict_uses_full_name_from_group():
    """В картинке подставляется полное ФИО из расписания группы + корпус."""
    lesson = _personal_lesson(
        "Понедельник", time="13:00-14:35", subject="Физика",
        teacher="Иванов И.И.", location="214; Б22/1",
    )
    index = _build_full_name_index([{
        "День недели": "Понедельник", "Время занятия": "13:00-14:35",
        "Предмет": "Физика", "ФИО преподавателя (полное)": "Иванов Иван Иванович",
    }])

    info = _personal_lesson_to_dict(lesson, index)

    assert info['ФИО преподавателя (полное)'] == "Иванов Иван Иванович"
    assert info['Корпус'] == "Б22/1"


def test_personal_lesson_to_dict_falls_back_to_short_name():
    """Без совпадения в расписании группы остаётся краткое ФИО."""
    lesson = _personal_lesson("Понедельник", teacher="Петров П.П.")

    info = _personal_lesson_to_dict(lesson, {})

    assert info['ФИО преподавателя (полное)'] == "Петров П.П."


def test_generate_timetable_image_accepts_group_timetable(cleanup_personal_image):
    """generate_timetable_image принимает расписание группы и не падает."""
    timetable = [_personal_lesson("Понедельник")]
    group = [{
        "День недели": "Понедельник", "Время занятия": "09:00-10:35",
        "Предмет": "Базы данных", "ФИО преподавателя (полное)": "Иванов Иван Иванович",
    }]

    path = generate_timetable_image(timetable, group_timetable=group)

    _assert_nonempty_png(path)


# --- баг с "кривой" картинкой: сломанный путь к шрифтам ----------------------
# (satanbonchbot/assets/fonts/ не существовал — реальный assets/fonts/ лежит
# в корне репо; ImageFont.truetype тихо падал в except и подставлял
# ImageFont.load_default() — битмап-шрифт без кириллицы, весь русский текст
# на картинке рисовался пустыми прямоугольниками). Тест ловит регресс пути,
# а не рисует пиксели — сравнивает _FONTS_DIR с реальным assets/fonts/ на диске.

def test_fonts_dir_points_at_real_font_files_on_disk():
    from satanbonchbot.rendering import _FONTS_DIR
    assert os.path.isdir(_FONTS_DIR), f"{_FONTS_DIR} не существует"
    assert os.path.isfile(os.path.join(_FONTS_DIR, "Montserrat-SemiBold.ttf"))


# --- перенос текста по словам вместо обрезки по символам ---------------------

def _measure_draw():
    return ImageDraw.Draw(Image.new('RGB', (1, 1)))


def _real_font(size=18):
    return ImageFont.truetype(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts", "Montserrat-SemiBold.ttf"),
        size=size,
    )


def test_wrap_text_to_width_breaks_on_word_boundaries_within_max_width():
    draw = _measure_draw()
    font = _real_font()
    long_text = "Теоретические основы конвергенции логических и интеллектуальных сетей"

    lines = _wrap_text_to_width(draw, long_text, font, max_width=200)

    assert len(lines) > 1
    for line in lines:
        assert draw.textlength(line, font=font) <= 200
    # Слова не разорваны посередине — каждое слово в исходном тексте целиком.
    for line in lines:
        for word in line.split():
            assert word in long_text.split()


def test_wrap_text_to_width_respects_max_lines_and_ellipsizes_last_line():
    draw = _measure_draw()
    font = _real_font()
    long_text = "слово " * 30

    lines = _wrap_text_to_width(draw, long_text, font, max_width=150, max_lines=2)

    assert len(lines) == 2
    assert lines[-1].endswith("…")
    assert draw.textlength(lines[-1], font=font) <= 150


def test_wrap_text_to_width_empty_text_returns_no_lines():
    draw = _measure_draw()
    font = _real_font()
    assert _wrap_text_to_width(draw, "", font, max_width=200) == []
    assert _wrap_text_to_width(draw, None, font, max_width=200) == []


def test_wrap_text_to_width_hard_breaks_word_wider_than_max_width():
    """Слово без пробелов, которое само по себе шире колонки — режем по буквам,
    а не вылезаем за рамку карточки."""
    draw = _measure_draw()
    font = _real_font()
    word = "оченьдлинноесловобезпробеловкотороеневлезаетниводнустроку" * 2

    lines = _wrap_text_to_width(draw, word, font, max_width=100)

    assert len(lines) > 1
    for line in lines:
        assert draw.textlength(line, font=font) <= 100


# --- реальная высота записи занятия вместо fixed lesson_entry_height = 80 ----

def test_lesson_entry_layout_height_grows_with_wrapped_subject():
    draw = _measure_draw()
    title_font = _real_font(18)
    text_font = _real_font(14)

    short_lesson = _lesson(Предмет="Физика")
    long_lesson = _lesson(
        Предмет="Очень длинное название предмета которое совершенно точно "
                "не поместится в одну строку карточки расписания"
    )

    _, short_lines, _, short_height = _lesson_entry_layout(draw, short_lesson, 300, text_font, title_font)
    _, long_lines, _, long_height = _lesson_entry_layout(draw, long_lesson, 300, text_font, title_font)

    assert len(short_lines) == 1
    assert len(long_lines) > 1
    assert long_height > short_height


def test_lesson_entry_layout_keeps_full_teacher_name_instead_of_char_truncation():
    """Раньше teacher[:24] + '...' резал ФИО посреди слова — теперь текст либо
    влезает целиком, либо переносится (без потери информации до 2 строк)."""
    draw = _measure_draw()
    title_font = _real_font(18)
    text_font = _real_font(14)
    lesson = _lesson(**{'ФИО преподавателя (полное)': 'Старостин Владимир Сергеевич'})

    _, _, info_lines, _ = _lesson_entry_layout(draw, lesson, 400, text_font, title_font)

    assert "Старостин Владимир Сергеевич" in " ".join(info_lines)


def test_lesson_entry_layout_puts_each_info_field_on_its_own_line():
    """Раньше ФИО/аудитория/тип склеивались в одну строку через " | " и
    вместе укладывались в общий лимит в 2 строки — при длинном ФИО это
    выталкивало часть текста (например тип занятия) за пределы отведённого
    места, и запись вылезала за рамку карточки (высота карточки считалась
    по тем же строкам, но перенос мог обрубить "|"+хвост посреди слова).
    Теперь у каждого поля своя строка(и) — без разделителя "|"."""
    draw = _measure_draw()
    title_font = _real_font(18)
    text_font = _real_font(14)
    narrow_width = 200  # узкая колонка — как в двухколоночной картинке под телефон
    lesson = _lesson(**{
        'ФИО преподавателя (полное)': 'Штеренберг Станислав Игоревич',
        'Номер кабинета': '509', 'Корпус': 'Б22/2', 'Тип занятия': 'Лабораторная работа',
    })

    _, _, info_lines, height = _lesson_entry_layout(draw, lesson, narrow_width, text_font, title_font)

    joined = " ".join(info_lines)
    assert "|" not in joined
    assert "Штеренберг Станислав Игоревич" in joined
    assert "509" in joined and "Б22/2" in joined
    assert "Лабораторная работа" in joined
    # Высота обязана учитывать все строки инфо-блока — иначе карточка
    # окажется короче фактически нарисованного контента (сам баг).
    assert height >= (28 + 8 + 1 * LESSON_LINE_HEIGHT + len(info_lines) * LESSON_LINE_HEIGHT + 12)


# --- эмодзи с вариационным селектором рвут отступ без libraqm в проде -------

def test_no_emoji_uses_variation_selector_16():
    """Прод (Docker, python:3.12-slim) не ставит libraqm — без него Pillow
    не шейпит последовательность «эмодзи + U+FE0F» как одну лигатуру, а
    считает вариационный селектор отдельным «символом» со своей шириной:
    "✏️" рисовалась вдвое шире нормы (33px вместо 19px при 14px), "❤️" —
    43px вместо 25px при 18px, оставляя заметную дыру перед следующим
    текстом. Симптом виден только там, где libraqm нет (прод), не локально,
    где он обычно есть, — поэтому здесь статическая проверка данных, а не
    пиксельный тест. Фикс — не использовать эмодзи, которым для
    emoji-варианта нужен VS16 (✏, ❤ и т.п.); из диапазона 0x1F300+ (📝🏫👤🔬💬)
    он не нужен вообще, они emoji-only по умолчанию."""
    for day_name, emoji in _DAY_EMOJIS.items():
        assert "️" not in emoji, f"{day_name}: {emoji!r} содержит VS16 — сломает отступ без libraqm"

    draw = _measure_draw()
    title_font = _real_font(18)
    text_font = _real_font(14)
    for lesson_type in ["Лекция", "Практические занятия", "Практика", "Лабораторная работа", "Семинар", "Коллоквиум"]:
        lesson = _lesson(**{'Тип занятия': lesson_type})
        _, _, info_lines, _ = _lesson_entry_layout(draw, lesson, 300, text_font, title_font)
        joined = "".join(info_lines)
        assert "️" not in joined, f"{lesson_type!r} -> {info_lines!r} содержит VS16"


# --- воскресенье больше не пропадает с картинки ------------------------------

def test_day_order_includes_sunday():
    """Раньше занятия по воскресеньям (сессия/пересдачи) группировались в
    данных, но ни в один из двух списков колонок не попадали — молча
    пропадали с картинки без единого сообщения об ошибке."""
    assert "Воскресенье" in _DAY_ORDER
    assert len(_DAY_ORDER) == 7


def test_generate_image_from_dict_renders_sunday_lessons(cleanup_images):
    lessons = [_lesson(**{'День недели': 'Воскресенье', 'Число': '2026.05.24'})]

    path = generate_timetable_image_from_dict(lessons, week_number=1, group_name="ИКВ-11")

    _assert_nonempty_png(path)
    # Карточка воскресенья реально дорисовалась — картинка не осталась
    # пустым фоном без единого блока (что и происходило до фикса).
    img = Image.open(path)
    assert img.getcolors(maxcolors=4) is None, "картинка должна содержать карточку дня, не только фон"


# --- личная картинка: пустая заглушка и рост холста под занятые недели -------

def test_generate_timetable_image_empty_shows_placeholder_not_blank(cleanup_images):
    """Раньше пустое расписание рисовало чисто белый холст 1200x1600 без
    единого пояснения пользователю."""
    path = generate_timetable_image([])

    _assert_nonempty_png(path)
    img = Image.open(path)
    assert img.getcolors(maxcolors=2) is None, "заглушка не должна быть сплошным одноцветным полотном"


def test_generate_timetable_image_grows_past_old_fixed_height(cleanup_images):
    """Раньше холст был фиксированным 1200x1600 — занятия, не поместившиеся
    ниже y=1600, PIL молча обрезал (без исключения и без предупреждения)."""
    busy_day = [
        _personal_lesson("Понедельник", date="2026-05-18", time=f"{9 + i}:00-{10 + i}:35", subject=f"Предмет {i}")
        for i in range(16)
    ]

    path = generate_timetable_image(busy_day)

    img = Image.open(path)
    assert img.height > 1600


# --- заглушка-с-текстом: перенос длинных сообщений об ошибке ----------------

def test_render_placeholder_image_wraps_long_message(tmp_path):
    """Раньше draw.text рисовал одну строку без переноса на фиксированном
    холсте 800px — длинное сообщение обрезалось за правым краем и было
    невидимо пользователю (именно там, где картинка должна объяснить проблему)."""
    long_message = "Ошибка сервера: " + "не удалось получить расписание группы " * 8
    image_path = str(tmp_path / "placeholder.png")

    result_path = _render_placeholder_image(image_path, long_message)

    img = Image.open(result_path)
    assert img.width == 800
    assert img.height > 200  # выросло под многострочный текст
