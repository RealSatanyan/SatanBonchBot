"""Генерация изображений расписания через PIL.

Задача 4.1, шаг 9 — чистая декомпозиция main.py без изменения поведения.

Зависит из проектных модулей только от чистого L2 (formatting, parsers) —
для обогащения личной картинки полным ФИО преподавателя (задача B.2).
Шрифты (`G8.otf`, `Montserrat-SemiBold.ttf`, `seguiemj.ttf`, `OpenSansEmoji.ttf`)
лежат в `assets/fonts/` В КОРНЕ РЕПОЗИТОРИЯ (не внутри пакета satanbonchbot/)
и грузятся через _font_path() — путь строится от расположения этого модуля,
не зависит от рабочей директории.
"""

import hashlib
import logging
import os
import time as time_module
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from satanbonchbot.formatting import  _build_full_name_index, resolve_teacher_full_name
from satanbonchbot.parsers import  split_room_building

# ВАЖНО: assets/fonts/ лежит в КОРНЕ репозитория, а не satanbonchbot/assets/fonts/
# (см. Dockerfile: `COPY assets/ ./assets/` копирует именно корневую assets/).
# rendering.py сам лежит в пакете satanbonchbot/, поэтому до корня — на один
# уровень выше dirname(__file__). Раньше здесь не хватало этого шага: путь
# указывал на несуществующую satanbonchbot/assets/fonts/, ImageFont.truetype
# тихо падал в except и подставлял ImageFont.load_default() — битмап-шрифт
# без кириллицы, из-за чего весь русский текст на картинке рисовался
# пустыми прямоугольниками (regression: рефакторинг раскладки на пакет).
_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")


def _font_path(name: str) -> str:
    """Абсолютный путь к шрифту в assets/fonts/ — не зависит от рабочей директории."""
    return os.path.join(_FONTS_DIR, name)


# Порядок дней — общий для обеих картинок (личной и групповой). Воскресенье
# раньше не входило ни в один список: занятия по воскресеньям (сессия/
# пересдачи) группировались в data, но никуда не попадали — молча пропадали
# с картинки без всякого сообщения об ошибке.
_DAY_ORDER = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

# Два столбика внутри узкого (под телефон) холста — компромисс между старой
# альбомной раскладкой 1400px (в ширину, неудобно с телефона) и одной длинной
# колонкой (в высоту, неудобно долго скроллить): картинка остаётся вытянутой
# по вертикали, но за счёт двух колонок примерно вдвое короче.
_LEFT_DAY_ORDER = ["Понедельник", "Среда", "Пятница"]
_RIGHT_DAY_ORDER = ["Вторник", "Четверг", "Суббота", "Воскресенье"]


def _personal_lesson_to_dict(lesson, full_name_index: dict) -> dict:
    """Приводит занятие личного расписания (атрибуты ЛК) к формату словаря
    групповых занятий (public_timetable.py) — обе картинки (личная и
    групповая) рисуются одним и тем же движком (_render_day_columns_image).
    Полное ФИО берётся из расписания группы (задача B.2; фолбэк — краткое),
    корпус разбивается из location — как в текстовом format_timetable.
    """
    teacher = resolve_teacher_full_name(lesson, full_name_index)
    room, building = split_room_building(lesson.location)
    return {
        'Время занятия': lesson.time,
        'Предмет': lesson.subject,
        'ФИО преподавателя (полное)': teacher,
        'Номер кабинета': room,
        'Корпус': building,
        'Тип занятия': lesson.lesson_type,
    }


def generate_timetable_image(timetable, group_timetable=None) -> str:
    """
    Генерирует изображение с расписанием.
    :param timetable: Список занятий (объекты личного расписания ЛК).
    :param group_timetable: Расписание группы пользователя для обогащения
        полным ФИО преподавателя (задача B.2); None — без обогащения.
    :return: Путь к сохраненному изображению.
    """
    unique_suffix = hashlib.md5(str(time_module.time()).encode()).hexdigest()[:8]
    image_path = f"timetable_personal_{unique_suffix}.png"

    if not timetable:
        return _render_placeholder_image(image_path, "Расписание пусто")

    full_name_index = _build_full_name_index(group_timetable)

    # Группируем занятия по дням недели и приводим к общему словарному формату.
    days = {}
    for lesson in timetable:
        days.setdefault(lesson.date, []).append(lesson)
    sorted_days = sorted(days.items(), key=lambda x: datetime.strptime(x[0], "%Y-%m-%d"))

    days_by_name = {}
    for date, lessons in sorted_days:
        day_name = lessons[0].day
        if not day_name:
            continue
        lesson_dicts = [_personal_lesson_to_dict(lesson, full_name_index) for lesson in lessons]
        date_display = date.replace('-', '.')
        if day_name in days_by_name:
            days_by_name[day_name][1].extend(lesson_dicts)
        else:
            days_by_name[day_name] = (date_display, lesson_dicts)

    return _render_day_columns_image(days_by_name, "Моё расписание", image_path)

def draw_rounded_rectangle(draw, xy, radius, fill=None, outline=None, width=1):
    """
    Рисует скругленный прямоугольник.
    :param draw: ImageDraw объект
    :param xy: Координаты (x1, y1, x2, y2)
    :param radius: Радиус скругления
    :param fill: Цвет заливки
    :param outline: Цвет контура
    :param width: Толщина контура
    """
    x1, y1, x2, y2 = xy

    # Рисуем основной прямоугольник
    if fill:
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)

    # Рисуем скругленные углы
    if fill:
        # Верхний левый
        draw.ellipse([x1, y1, x1 + radius * 2, y1 + radius * 2], fill=fill)
        # Верхний правый
        draw.ellipse([x2 - radius * 2, y1, x2, y1 + radius * 2], fill=fill)
        # Нижний левый
        draw.ellipse([x1, y2 - radius * 2, x1 + radius * 2, y2], fill=fill)
        # Нижний правый
        draw.ellipse([x2 - radius * 2, y2 - radius * 2, x2, y2], fill=fill)

    if outline:
        # Контур для прямых сторон
        draw.rectangle([x1 + radius, y1, x2 - radius, y1 + width], fill=outline)  # Верх
        draw.rectangle([x1 + radius, y2 - width, x2 - radius, y2], fill=outline)  # Низ
        draw.rectangle([x1, y1 + radius, x1 + width, y2 - radius], fill=outline)  # Лево
        draw.rectangle([x2 - width, y1 + radius, x2, y2 - radius], fill=outline)  # Право

        # Контур для углов (дуги)
        try:
            draw.arc([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=outline, width=width)
            draw.arc([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=outline, width=width)
            draw.arc([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=outline, width=width)
            draw.arc([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=outline, width=width)
        except:
            # Если arc не поддерживает width, рисуем без него
            draw.arc([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=outline)
            draw.arc([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=outline)
            draw.arc([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=outline)
            draw.arc([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=outline)

def _load_timetable_fonts():
    """
    Загружает шрифты для картинки расписания. Возвращает кортеж:
    (title_font, day_font, lesson_title_font, lesson_text_font, footer_font,
     emoji_font, emoji_font_small). Фолбэк на default при отсутствии файлов.
    """
    text_font_path = _font_path("Montserrat-SemiBold.ttf")
    emoji_font_path = _font_path("seguiemj.ttf")

    # Шрифты для текста (Montserrat-SemiBold)
    try:
        title_font = ImageFont.truetype(text_font_path, size=36)
        day_font = ImageFont.truetype(text_font_path, size=24)
        lesson_title_font = ImageFont.truetype(text_font_path, size=18)
        lesson_text_font = ImageFont.truetype(text_font_path, size=14)
        footer_font = ImageFont.truetype(text_font_path, size=14)
    except IOError:
        # Fallback на default, если шрифт не найден
        title_font = ImageFont.load_default()
        day_font = ImageFont.load_default()
        lesson_title_font = ImageFont.load_default()
        lesson_text_font = ImageFont.load_default()
        footer_font = ImageFont.load_default()

    # Шрифт для цветных эмодзи (seguiemj.ttf с поддержкой COLR - Color Outline)
    try:
        # Загружаем seguiemj.ttf который использует COLR формат для цветных эмодзи
        # COLR шрифты поддерживают обычные размеры, не требуют фиксированного размера
        # Pillow 10.0.0+ поддерживает COLR через embedded_color=True
        emoji_font = ImageFont.truetype(emoji_font_path, size=18)
        emoji_font_small = ImageFont.truetype(emoji_font_path, size=14)
        logging.info(f"seguiemj.ttf (COLR) загружен для цветных эмодзи из {os.path.abspath(emoji_font_path)}")
    except IOError as e:
        logging.error(f"Ошибка при загрузке {emoji_font_path}: {e}")
        # Fallback на OpenSansEmoji если seguiemj не найден
        try:
            emoji_font_path_fallback = _font_path("OpenSansEmoji.ttf")
            emoji_font = ImageFont.truetype(emoji_font_path_fallback, size=18)
            emoji_font_small = ImageFont.truetype(emoji_font_path_fallback, size=14)
            logging.warning("seguiemj.ttf не найден, используем OpenSansEmoji.ttf")
        except IOError:
            emoji_font = ImageFont.load_default()
            emoji_font_small = ImageFont.load_default()
            logging.warning("Эмодзи шрифты не найдены, используем default")

    return (title_font, day_font, lesson_title_font, lesson_text_font,
            footer_font, emoji_font, emoji_font_small)


# Эмодзи дней недели — одна колонка, один набор (раньше было два почти
# одинаковых словаря для левой/правой колонки).
# "❤" — БЕЗ вариационного селектора U+FE0F: в проде (Docker) не установлен
# libraqm, поэтому Pillow не шейпит "❤️" как одну лигатуру, а считает
# вариационный селектор отдельным "символом" со своей шириной — эмодзи
# рисуется вдвое шире, чем остальные (43px вместо 25px при 18px), и после
# него остаётся заметная дыра перед текстом дня. Без селектора та же
# картинка эмодзи (это выделенный emoji-шрифт), но с нормальной шириной.
_DAY_EMOJIS = {
    "Понедельник": "💙", "Вторник": "💚", "Среда": "💛",
    "Четверг": "💗", "Пятница": "❤", "Суббота": "💜", "Воскресенье": "🖤",
}


def _draw_day_block(draw, day_name, date, lessons, x, y, column_width,
                    day_font, emoji_font, lesson_text_font, lesson_title_font,
                    day_emojis, block_padding, block_spacing, emoji_font_small):
    """Рисует блок одного дня (рамка, заголовок, занятия). Возвращает y следующего блока."""
    lessons_sorted = sorted(lessons, key=lambda les: les.get('Время занятия', '') or '')

    # Высота блока — сумма РЕАЛЬНЫХ высот записей (см. _lesson_entry_layout),
    # а не len(lessons) * 80: фиксированная высота на запись предполагала
    # ровно одну строку темы и одну строку инфо, из-за чего длинный текст
    # вылезал за рамку карточки (визуальная "кривизна", которую чиним).
    day_header_height = 40
    max_entry_width = column_width - block_padding * 2 - 20
    entries_height = sum(
        _lesson_entry_layout(draw, lesson, max_entry_width, lesson_text_font, lesson_title_font)[-1]
        for lesson in lessons_sorted
    )
    block_height = max(150, entries_height + block_padding * 2 + day_header_height)

    # Рисуем белый блок с розовой рамкой для дня
    draw_rounded_rectangle(
        draw, [x, y, x + column_width - 20, y + block_height],
        radius=12, fill=(255, 255, 255), outline=(255, 182, 193), width=2
    )

    # Рисуем заголовок дня в верхней части блока
    header_bg_y = y + 5
    header_bg_height = day_header_height - 10
    draw_rounded_rectangle(
        draw, [x + 5, header_bg_y, x + column_width - 25, header_bg_y + header_bg_height],
        radius=6, fill=(255, 240, 245), outline=(255, 182, 193), width=1
    )

    # Текст дня недели — эмодзи и текст рисуем раздельно для выравнивания
    day_emoji = day_emojis.get(day_name, "📅")
    day_name_text = f"{day_name}"
    if date:
        day_name_text += f" ({date})"

    draw_text_with_emoji(draw, day_emoji, x + 12, header_bg_y + 8, day_font, emoji_font, fill=(100, 50, 100))

    # Вычисляем позицию текста после эмодзи
    try:
        emoji_width = draw.textlength(day_emoji, font=emoji_font)
    except:
        emoji_width = 25

    draw_text_with_emoji(draw, day_name_text, x + 12 + int(emoji_width) + 6, header_bg_y + 2, day_font, emoji_font, fill=(100, 50, 100))

    # Отрисовываем занятия в блоке
    lesson_y = y + block_padding + day_header_height
    for lesson in lessons_sorted:
        lesson_y = _draw_lesson_entry(draw, lesson, x + block_padding, lesson_y, max_entry_width, emoji_font, lesson_text_font, lesson_title_font, emoji_font_small)

    return y + block_height + block_spacing


# Шаг строки для темы/инфо занятия — используется и при расчёте высоты
# (_lesson_entry_layout), и при самой отрисовке (_draw_lesson_entry), так что
# посчитанная высота карточки не может разойтись с тем, что реально нарисуется.
LESSON_LINE_HEIGHT = 22
SUBJECT_MAX_LINES = 3
# Лимит строк НА КАЖДОЕ поле инфо-блока (ФИО/аудитория/тип) по отдельности —
# не общий лимit на все поля сразу (см. _lesson_entry_layout).
INFO_FIELD_MAX_LINES = 2


def _wrap_text_to_width(draw, text, font, max_width, max_lines=None):
    """Переносит text по словам так, чтобы каждая строка укладывалась в max_width px.

    textwrap.wrap здесь не подходит — его width считает СИМВОЛЫ, а не пиксели,
    и для пропорционального шрифта с кириллицей это либо вылезает за рамку,
    либо жмётся с запасом. Меряем реальную ширину рендера (draw.textlength).
    Слово шире max_width само по себе режется по буквам. При max_lines лишний
    текст отбрасывается, последняя строка помечается «…».
    """
    text = (text or "").strip()
    if not text:
        return []

    def fits(s):
        return draw.textlength(s, font=font) <= max_width

    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if fits(candidate):
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if fits(word):
            current = word
        else:
            piece = ""
            for ch in word:
                if piece and not fits(piece + ch):
                    lines.append(piece)
                    piece = ch
                else:
                    piece += ch
            current = piece
    if current:
        lines.append(current)

    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and not fits(last + "…"):
            last = last[:-1]
        lines[-1] = (last + "…") if last else "…"

    return lines


def _lesson_entry_layout(draw, lesson, max_width, lesson_text_font, lesson_title_font):
    """Готовит перенесённый по словам текст и итоговую высоту записи занятия
    — без отрисовки. Вызывается дважды на одну и ту же запись: сначала для
    расчёта высоты карточки дня (_draw_day_block), потом при самой отрисовке
    (_draw_lesson_entry) — чистая функция от одних и тех же входных данных,
    поэтому цифры физически не могут разойтись (в отличие от прежних двух
    независимых формул — `len(lessons) * 80` в одном месте и фактическая
    высота отрисовки в другом).
    """
    time_str = lesson.get('Время занятия', 'Не указано')
    subject = lesson.get('Предмет', 'Не указано')
    # Полное ФИО, если доступно (задача B.1), иначе — краткое.
    teacher = lesson.get('ФИО преподавателя (полное)') or lesson.get('ФИО преподавателя', 'Не указано')
    room = lesson.get('Номер кабинета', 'Не указано')
    building = lesson.get('Корпус')

    time_formatted = time_str.replace(':', '.')

    # Тема рисуется рядом с эмодзи-иконкой книги (см. _draw_lesson_entry) —
    # под неё резервируем место в доступной ширине.
    subject_max_width = max(20, max_width - 26)
    subject_lines = _wrap_text_to_width(draw, subject, lesson_title_font, subject_max_width, SUBJECT_MAX_LINES)

    # Группы/ФИО/аудитория/тип — каждый признак на своей строке (а не одной
    # общей строкой через " | "), с переносом внутри строки при надобности.
    # Раньше все признаки склеивались в одну строку и вместе укладывались в
    # общий лимит INFO_MAX_LINES=2 — длинное ФИО плюс тип съедали этот лимит
    # целиком, и часть текста («Лекция» и т.п.) обрезалась или вылезала за
    # рамку карточки, потому что перенос резал прямо по разделителю "|".
    info_lines = []

    # Слитые группы-потоки (расписание преподавателя/аудитории) — см. B.3.
    groups = lesson.get('Группы')
    if groups:
        info_lines.extend(_wrap_text_to_width(draw, f"👥 {', '.join(groups)}", lesson_text_font, max_width, INFO_FIELD_MAX_LINES))
    if teacher and teacher != 'Не указано':
        info_lines.extend(_wrap_text_to_width(draw, f"👤 {teacher}", lesson_text_font, max_width, INFO_FIELD_MAX_LINES))
    if room and room != 'Не указано':
        room_label = f"{room} · {building}" if building else room
        info_lines.extend(_wrap_text_to_width(draw, f"🏫 {room_label}", lesson_text_font, max_width, INFO_FIELD_MAX_LINES))

    lesson_type = lesson.get('Тип занятия', '')
    if lesson_type:
        type_emoji = "📖"  # По умолчанию
        if "Лекция" in lesson_type:
            type_emoji = "📝"
        elif "Практические" in lesson_type or "Практика" in lesson_type:
            # "✏" — БЕЗ вариационного селектора U+FE0F, см. комментарий у
            # _DAY_EMOJIS: в проде без libraqm "✏️" рисуется вдвое шире
            # нормы, отсюда была видимая дыра перед "Практические занятия".
            type_emoji = "✏"
        elif "Лабораторная" in lesson_type or "Лаборатория" in lesson_type:
            type_emoji = "🔬"
        elif "Семинар" in lesson_type:
            type_emoji = "💬"
        info_lines.extend(_wrap_text_to_width(draw, f"{type_emoji} {lesson_type}", lesson_text_font, max_width, INFO_FIELD_MAX_LINES))

    height = (
        28 + 8                                                   # блок времени + отступ
        + len(subject_lines) * LESSON_LINE_HEIGHT
        + (len(info_lines) * LESSON_LINE_HEIGHT if info_lines else 0)
        + 12                                                     # нижний отступ
    )

    return time_formatted, subject_lines, info_lines, height


def _draw_lesson_entry(draw, lesson, x, y, max_width, emoji_font,
                       lesson_text_font, lesson_title_font, emoji_font_small):
    """Рисует одну запись занятия (время, предмет, инфо). Возвращает нижний y."""
    time_formatted, subject_lines, info_lines, height = _lesson_entry_layout(
        draw, lesson, max_width, lesson_text_font, lesson_title_font)

    current_y = y

    # Более контрастный прямоугольник для времени с белым текстом
    time_box_height = 28
    time_box_width = 100
    time_box_x = x
    time_box_y = current_y

    # Более темный и контрастный розовый цвет для времени
    time_color = (219, 112, 147)  # Более насыщенный розовый для лучшего контраста
    draw_rounded_rectangle(
        draw, [time_box_x, time_box_y, time_box_x + time_box_width, time_box_y + time_box_height],
        radius=5, fill=time_color
    )

    # Белый текст времени
    try:
        time_bbox = draw.textbbox((0, 0), time_formatted, font=lesson_text_font)
        time_text_width = time_bbox[2] - time_bbox[0]
        time_text_height = time_bbox[3] - time_bbox[1]
    except Exception:
        time_text_width = draw.textlength(time_formatted, font=lesson_text_font)
        time_text_height = 14

    time_text_x = time_box_x + (time_box_width - time_text_width) // 2
    time_text_y = time_box_y + (time_box_height - time_text_height) // 2
    draw_text_with_emoji(draw, time_formatted, time_text_x, time_text_y, lesson_text_font, emoji_font, fill=(255, 255, 255))

    # Предмет с эмодзи книги — первая строка рядом с эмодзи, перенесённые
    # продолжения выровнены под текст первой строки (без эмодзи).
    subject_x = x
    subject_y = current_y + time_box_height + 8

    book_emoji = "📚"
    draw_text_with_emoji(draw, book_emoji, subject_x, subject_y, lesson_title_font, emoji_font, fill=(0, 0, 0))
    try:
        emoji_width = draw.textlength(book_emoji, font=emoji_font)
    except Exception:
        emoji_width = 20
    subject_text_x = subject_x + int(emoji_width) + 6

    line_y = subject_y
    for line in subject_lines:
        draw_text_with_emoji(draw, line, subject_text_x, line_y, lesson_title_font, emoji_font, fill=(0, 0, 0))
        line_y += LESSON_LINE_HEIGHT

    # Преподаватель, кабинет и тип предмета с эмодзи. Важно: эмодзи-шрифт
    # ЗДЕСЬ — emoji_font_small (14px, как и lesson_text_font), а не emoji_font
    # (18px, для заголовка предмета). Раньше иконка бралась 18px рядом с
    # 14px текстом — для "толстых" иконок (👤/🏫) разница малозаметна, а
    # тонкая ✏️ в увеличенном на четверть боксе давала заметный пробел перед
    # текстом ("слишком большой пробел после смайлика").
    if info_lines:
        info_y = line_y
        for line in info_lines:
            draw_text_with_emoji(draw, line, x, info_y, lesson_text_font, emoji_font_small, fill=(0, 0, 0))
            info_y += LESSON_LINE_HEIGHT

    return current_y + height


def _render_placeholder_image(image_filename: str, message: str) -> str:
    """Маленькая картинка-заглушка с текстом — для пустого расписания / ошибки.

    message может быть произвольной строкой ошибки апстрима (см. вызов из
    generate_timetable_image_from_dict) — раньше рисовалась одной строкой
    без переноса на фиксированном холсте 800px и обрезалась по правому краю,
    если не помещалась (то есть ровно там, где картинка должна объяснить
    пользователю проблему, текст мог быть невидимым).
    """
    width = 800
    margin_x = 50
    max_text_width = width - margin_x * 2
    try:
        text_font = ImageFont.truetype(_font_path("G8.otf"), size=24)
    except IOError:
        text_font = ImageFont.load_default()

    measure_draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    lines = _wrap_text_to_width(measure_draw, message, text_font, max_text_width) or [""]

    line_height = 32
    height = max(200, 100 + len(lines) * line_height)
    image = Image.new('RGB', (width, height), color=(245, 247, 250))
    draw = ImageDraw.Draw(image)
    y = (height - len(lines) * line_height) // 2
    for line in lines:
        draw.text((margin_x, y), line, fill=(100, 100, 100), font=text_font)
        y += line_height

    image.save(image_filename)
    return image_filename


def _render_day_columns_image(days_by_name: dict, title_text: str, image_filename: str) -> str:
    """Общий движок обеих картинок расписания (личной и групповой): розовый
    градиентный хедер, две колонки карточек-дней (_draw_day_block), футер.
    Вынесен из generate_timetable_image_from_dict (B.1) — раньше личная
    картинка (generate_timetable_image) рисовалась отдельным, гораздо более
    простым кодом (белый фон без карточек), а её собственный расчёт высоты
    молча расходился с фактической отрисовкой (обреза́л последнюю запись).
    Теперь обе картинки используют один и тот же, уже проверенный движок —
    расхождению просто неоткуда взяться.

    :param days_by_name: {день_недели: (дата_для_заголовка, [занятия])};
        занятия — словари в формате public_timetable.py (см. _lesson_entry_layout).
    :param title_text: Текст в шапке картинки.
    :param image_filename: Имя файла для сохранения.
    :return: Путь к сохранённому изображению.
    """
    header_height = 120
    block_padding = 15
    block_spacing = 15
    day_header_height = 40
    # Два узких столбика вместо одного во всю ширину — картинка компактнее
    # (короче в 2 раза), но холст всё ещё уже, чем высокий: под телефон, а не
    # альбомная 1400px-широкая раскладка, которая была изначально.
    width = 900
    x_content = 20
    column_width = (width - x_content * 3) // 2
    x_left = x_content
    x_right = x_content * 2 + column_width
    max_entry_width = column_width - block_padding * 2 - 20

    # Шрифты нужны уже здесь — для измерения реальной высоты записей ниже.
    (title_font, day_font, lesson_title_font, lesson_text_font, footer_font,
     emoji_font, emoji_font_small) = _load_timetable_fonts()

    # Высота колонки — сумма РЕАЛЬНЫХ высот записей (_lesson_entry_layout),
    # а не фиксированная оценка на запись. textlength/textbbox зависят только
    # от шрифта, не от готового холста, поэтому меряем через одноразовый draw
    # на холсте 1x1 — это позволяет узнать точную высоту ДО того, как холст
    # нужного размера вообще создан (высота холста как раз и есть то,
    # что мы сейчас считаем).
    _measure_draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))

    def _column_height(day_names):
        total = 0
        for day_name in day_names:
            if day_name not in days_by_name:
                continue
            _, lessons = days_by_name[day_name]
            entries_height = sum(
                _lesson_entry_layout(_measure_draw, lesson, max_entry_width, lesson_text_font, lesson_title_font)[-1]
                for lesson in lessons
            )
            day_height = max(150, entries_height + block_padding * 2 + day_header_height)
            total += day_height + block_spacing
        return total

    max_column_height = max(_column_height(_LEFT_DAY_ORDER), _column_height(_RIGHT_DAY_ORDER))
    estimated_height = header_height + max_column_height + 100  # +100 для футера
    height = max(600, estimated_height)

    # Создаем изображение с пастельным фоном
    image = Image.new('RGB', (width, height), color=(255, 250, 252))  # Почти белый с розовым оттенком
    draw = ImageDraw.Draw(image)

    # Рисуем градиентный фон для заголовка (розово-фиолетовый)
    header_color_start = (255, 182, 193)  # Пастельный розовый
    header_color_end = (186, 104, 200)   # Пастельный фиолетовый
    for i in range(header_height):
        ratio = i / header_height
        r = int(header_color_start[0] * (1 - ratio) + header_color_end[0] * ratio)
        g = int(header_color_start[1] * (1 - ratio) + header_color_end[1] * ratio)
        b = int(header_color_start[2] * (1 - ratio) + header_color_end[2] * ratio)
        draw.rectangle([(0, i), (width, i + 1)], fill=(r, g, b))

    # Центрируем заголовок
    try:
        title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
    except Exception:
        # Fallback для старых версий PIL
        title_width = draw.textlength(title_text, font=title_font)
    title_x = (width - title_width) // 2
    title_y = 40

    # Белый текст для заголовка на розово-фиолетовом фоне
    draw_text_with_emoji(draw, title_text, title_x, title_y, title_font, emoji_font, fill=(255, 255, 255))

    # Рисуем декоративное подчеркивание под заголовком
    underline_y = title_y + 50
    underline_width = title_width + 40
    underline_x = (width - underline_width) // 2
    draw.rectangle([underline_x, underline_y, underline_x + underline_width, underline_y + 3], fill=(255, 255, 255))

    # Начальная позиция для контента
    y_start = header_height + 50

    # Отрисовываем левый и правый столбики (задача B.1 — извлечён _draw_day_block).
    y_left = y_start
    for day_name in _LEFT_DAY_ORDER:
        if day_name in days_by_name:
            date, lessons = days_by_name[day_name]
            y_left = _draw_day_block(
                draw, day_name, date, lessons, x_left, y_left, column_width,
                day_font, emoji_font, lesson_text_font, lesson_title_font,
                _DAY_EMOJIS, block_padding, block_spacing, emoji_font_small)

    y_right = y_start
    for day_name in _RIGHT_DAY_ORDER:
        if day_name in days_by_name:
            date, lessons = days_by_name[day_name]
            y_right = _draw_day_block(
                draw, day_name, date, lessons, x_right, y_right, column_width,
                day_font, emoji_font, lesson_text_font, lesson_title_font,
                _DAY_EMOJIS, block_padding, block_spacing, emoji_font_small)

    max_y = max(y_left, y_right) + 20

    # Добавляем футер
    footer_height = 50
    footer_y = max_y + 20

    # Убеждаемся, что у нас достаточно места для футера
    if footer_y + footer_height > height:
        # Расширяем изображение
        new_image = Image.new('RGB', (width, footer_y + footer_height), color=(255, 250, 252))
        new_image.paste(image, (0, 0))
        image = new_image
        draw = ImageDraw.Draw(image)

    # Рисуем футер (светлый серый текст) с нормальными сердечками
    footer_text = "SatanBonchBot"
    hearts = "💗 💗 💗"

    try:
        footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
        footer_width = footer_bbox[2] - footer_bbox[0]
    except Exception:
        footer_width = draw.textlength(footer_text, font=footer_font)

    footer_x = (width - footer_width) // 2
    # Светлый серый цвет для текста футера
    draw_text_with_emoji(draw, footer_text, footer_x, footer_y, footer_font, emoji_font, fill=(180, 180, 180))

    # Рисуем сердечки
    try:
        hearts_bbox = draw.textbbox((0, 0), hearts, font=emoji_font_small)
        hearts_width = hearts_bbox[2] - hearts_bbox[0]
    except Exception:
        hearts_width = draw.textlength(hearts, font=emoji_font_small)

    hearts_x = (width - hearts_width) // 2
    draw_text_with_emoji(draw, hearts, hearts_x, footer_y + 22, footer_font, emoji_font_small, fill=(255, 182, 193))

    # Обрезаем до финальной высоты
    final_height = footer_y + footer_height
    if final_height < image.height:
        image = image.crop((0, 0, width, final_height))

    # Сохраняем изображение
    logging.info(f"Изображение успешно сохранено по пути: {image_filename}")
    image.save(image_filename)
    return image_filename


def generate_timetable_image_from_dict(timetable: list, title: str = "Расписание", week_number: int = None, group_name: str = "") -> str:
    """
    Генерирует красивое изображение с расписанием из словарей (формат public_timetable.py).
    :param timetable: Список словарей с занятиями.
    :param title: Заголовок расписания.
    :param week_number: Номер недели для фильтрации (None - все недели).
    :param group_name: Название группы для уникальности имени файла.
    :return: Путь к сохраненному изображению.
    """

    # Создаем уникальное имя файла
    unique_suffix = hashlib.md5(f"{group_name}_{week_number}_{time_module.time()}".encode()).hexdigest()[:8]
    safe_group_name = "".join(c for c in group_name if c.isalnum() or c in ('-', '_'))[:20] if group_name else "group"
    image_filename = f"timetable_{safe_group_name}_week_{week_number if week_number is not None else 'all'}_{unique_suffix}.png"

    if isinstance(timetable, str) or not timetable:
        message = "Расписание пусто" if not timetable else timetable
        return _render_placeholder_image(image_filename, message)

    # Фильтруем по неделе, если указана
    if week_number is not None:
        timetable = [lesson for lesson in timetable if lesson.get('Номер недели') == week_number]
        if not timetable:
            return _render_placeholder_image(
                image_filename, f"Нет занятий на неделе №{week_number}")

    # Группируем занятия по дням
    days = {}
    for lesson in timetable:
        date = lesson.get('Число', '')
        if date not in days:
            days[date] = []
        days[date].append(lesson)

    # Сортируем дни по дате
    sorted_days = sorted(days.items(), key=lambda x: datetime.strptime(x[0], "%Y.%m.%d") if x[0] else datetime.min)

    days_by_name = {}
    for date, lessons in sorted_days:
        day_name = lessons[0].get('День недели', '')
        if day_name:
            days_by_name[day_name] = (date, lessons)

    title_text = f"Расписание занятий - {group_name}" if group_name else "Расписание занятий"
    return _render_day_columns_image(days_by_name, title_text, image_filename)

def draw_text_with_emoji(draw, text, x, y, text_font, emoji_font, fill=(0, 0, 0), image=None):
    """
    Рисует текст с эмодзи, используя разные шрифты.
    Для цветных эмодзи использует seguiemj.ttf с поддержкой COLR (Color Outline).
    :param draw: Объект ImageDraw.
    :param text: Текст для отрисовки.
    :param x: Начальная координата X.
    :param y: Начальная координата Y.
    :param text_font: Шрифт для текста.
    :param emoji_font: Шрифт для эмодзи (seguiemj.ttf, фолбэк OpenSansEmoji.ttf).
    :param fill: Цвет текста (по умолчанию черный, для эмодзи игнорируется если шрифт поддерживает CBDT).
    """
    current_x = x
    i = 0
    while i < len(text):
        char = text[i]
        char_code = ord(char)

        # Проверяем, является ли символ эмодзи
        # Эмодзи могут быть составными (например, 👨‍🏫 состоит из нескольких символов)
        is_emoji = False

        # Базовые диапазоны эмодзи
        if (0x1F300 <= char_code <= 0x1F9FF) or \
           (0x2600 <= char_code <= 0x26FF) or \
           (0x2700 <= char_code <= 0x27BF) or \
           (char_code > 0xFFFF):
            is_emoji = True

        # Проверяем составные эмодзи (например, 👨‍🏫)
        emoji_text = char
        if i + 1 < len(text):
            next_char = text[i + 1]
            next_char_code = ord(next_char)
            # Если следующий символ - Zero Width Joiner или Variation Selector, это составной эмодзи
            if next_char_code == 0x200D or next_char_code == 0xFE0F:
                is_emoji = True
                # Собираем весь составной эмодзи
                j = i + 1
                while j < len(text):
                    char_j = text[j]
                    char_j_code = ord(char_j)
                    # Продолжаем собирать составной эмодзи, пока встречаем:
                    # - Zero Width Joiner (0x200D)
                    # - Эмодзи символы (0x1F300-0x1F9FF)
                    # - Вариационные селекторы (0xFE00-0xFE0F)
                    # - Combining Enclosing Keycap (0x20E3)
                    if char_j_code == 0x200D or \
                       (0x1F300 <= char_j_code <= 0x1F9FF) or \
                       (0xFE00 <= char_j_code <= 0xFE0F) or \
                       (0x20E3 <= char_j_code <= 0x20E3):
                        emoji_text += char_j
                        j += 1
                    else:
                        # Если следующий символ не является частью эмодзи (пробел, буква и т.д.), останавливаемся
                        break
                i = j - 1  # Устанавливаем индекс на последний символ эмодзи

        if is_emoji:
            # Для COLR шрифтов (seguiemj.ttf) используем обычную отрисовку с embedded_color=True
            # COLR шрифты поддерживают обычные размеры, не требуют сложного масштабирования
            try:
                # Пробуем использовать embedded_color для COLR цветных эмодзи
                draw.text((current_x, y), emoji_text, font=emoji_font, embedded_color=True)
            except (TypeError, AttributeError):
                # Если embedded_color не поддерживается, используем обычный метод
                try:
                    draw.text((current_x, y), emoji_text, fill=fill, font=emoji_font)
                except Exception:
                    draw.text((current_x, y), emoji_text, font=emoji_font)

            # Обновляем позицию X
            try:
                current_x += emoji_font.getlength(emoji_text)
            except:
                try:
                    bbox = draw.textbbox((0, 0), emoji_text, font=emoji_font)
                    current_x += bbox[2] - bbox[0]
                except:
                    current_x += 20  # Примерная ширина
        else:
            font = text_font
            draw.text((current_x, y), char, fill=fill, font=font)

        # Позиция X уже обновлена для эмодзи в блоке выше
        if not is_emoji:
            try:
                current_x += font.getlength(char)  # Обновляем позицию X
            except:
                # Fallback для старых версий PIL
                try:
                    bbox = draw.textbbox((0, 0), char, font=font)
                    current_x += bbox[2] - bbox[0]
                except:
                    current_x += 10  # Примерная ширина

        i += 1
