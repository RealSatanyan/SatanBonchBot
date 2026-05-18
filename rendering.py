"""Генерация изображений расписания через PIL.

Задача 4.1, шаг 9 — чистая декомпозиция main.py без изменения поведения.

Лист графа зависимостей: только stdlib + PIL. Не импортирует проектные модули.
Шрифты (`G8.otf`, `Montserrat-SemiBold.ttf`, `seguiemj.ttf`, `OpenSansEmoji.ttf`)
грузятся по относительным путям из корня проекта — пути сохранены ровно как в
main.py, rendering.py лежит в том же корне, рабочая директория та же.
"""

import hashlib
import logging
import os
import time as time_module
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont


def generate_timetable_image(timetable) -> str:
    """
    Генерирует изображение с расписанием.
    :param timetable: Список занятий.
    :return: Путь к сохраненному изображению.
    """
    # Размеры изображения
    width, height = 1200, 1600
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Шрифты
    text_font_path = "G8.otf"  # Шрифт для текста
    emoji_font_path = "seguiemj.ttf"  # Шрифт для эмодзи (например, Segoe UI Emoji)

    try:
        text_font = ImageFont.truetype(text_font_path, size=20)
    except IOError:
        text_font = ImageFont.load_default()

    try:
        emoji_font = ImageFont.truetype(emoji_font_path, size=20)
    except IOError:
        emoji_font = ImageFont.load_default()

    # Начальные координаты
    x_left = 10  # Левый столбик
    x_right = width // 2 + 10  # Правый столбик
    y = 10

    # Группируем занятия по дням
    days = {}
    for lesson in timetable:
        date = lesson.date
        if date not in days:
            days[date] = []
        days[date].append(lesson)

    # Сортируем дни по дате
    sorted_days = sorted(days.items(), key=lambda x: datetime.strptime(x[0], "%Y-%m-%d"))

    # Распределяем дни по столбикам
    left_days = ["Понедельник", "Среда", "Пятница"]
    right_days = ["Вторник", "Четверг", "Суббота"]

    # Отрисовываем левый столбик
    y_left = y
    for day_name in left_days:
        # Проверяем, есть ли занятия для этого дня
        day_lessons = []
        for date, lessons in sorted_days:
            if lessons[0].day == day_name:
                day_lessons = lessons
                break

        if day_lessons:
            # Заголовок дня
            draw_text_with_emoji(draw, day_name, x_left, y_left, text_font, emoji_font)
            y_left += 30

            # Отображаем занятия
            for lesson in day_lessons:
                lesson_info = (
                    f"⏰ {lesson.time}\n"
                    f"📚 {lesson.subject}\n"
                    f"🎓 {lesson.teacher}\n"
                    f"🏫 {lesson.location}\n"
                    f"🔹 Тип: {lesson.lesson_type}\n"
                )
                y_left = draw_lesson(draw, lesson_info, x_left, y_left, text_font, emoji_font, width // 2 - 20)
                y_left += 10  # Отступ между занятиями

            y_left += 20  # Отступ между днями

    # Отрисовываем правый столбик
    y_right = y
    for day_name in right_days:
        # Проверяем, есть ли занятия для этого дня
        day_lessons = []
        for date, lessons in sorted_days:
            if lessons[0].day == day_name:
                day_lessons = lessons
                break

        if day_lessons:
            # Заголовок дня
            draw_text_with_emoji(draw, day_name, x_right, y_right, text_font, emoji_font)
            y_right += 30

            # Отображаем занятия
            for lesson in day_lessons:
                lesson_info = (
                    f"⏰ {lesson.time}\n"
                    f"📚 {lesson.subject}\n"
                    f"🎓 {lesson.teacher}\n"
                    f"🏫 {lesson.location}\n"
                    f"🔹 Тип: {lesson.lesson_type}\n"
                )
                y_right = draw_lesson(draw, lesson_info, x_right, y_right, text_font, emoji_font, width // 2 - 20)
                y_right += 10  # Отступ между занятиями

            y_right += 20  # Отступ между днями

    # Сохраняем изображение
    image_path = "timetable.png"
    logging.info(f"Изображение успешно сохранено по пути: {image_path}")
    image.save(image_path)
    return image_path

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

def generate_timetable_image_from_dict(timetable: list, title: str = "Расписание", week_number: int = None, group_name: str = "") -> str:
    """
    Генерирует красивое изображение с расписанием из словарей (формат TImetabels.py).
    :param timetable: Список словарей с занятиями.
    :param title: Заголовок расписания.
    :param week_number: Номер недели для фильтрации (None - все недели).
    :param group_name: Название группы для уникальности имени файла.
    :return: Путь к сохраненному изображению.
    """
    import time
    import hashlib

    # Создаем уникальное имя файла
    unique_suffix = hashlib.md5(f"{group_name}_{week_number}_{time_module.time()}".encode()).hexdigest()[:8]
    safe_group_name = "".join(c for c in group_name if c.isalnum() or c in ('-', '_'))[:20] if group_name else "group"
    image_filename = f"timetable_{safe_group_name}_week_{week_number if week_number is not None else 'all'}_{unique_suffix}.png"

    if isinstance(timetable, str) or not timetable:
        # Создаем пустое изображение с сообщением
        width, height = 800, 200
        image = Image.new('RGB', (width, height), color=(245, 247, 250))
        draw = ImageDraw.Draw(image)
        try:
            text_font = ImageFont.truetype("G8.otf", size=24)
        except IOError:
            text_font = ImageFont.load_default()
        message = "Расписание пусто" if not timetable else timetable
        draw.text((50, 100), message, fill=(100, 100, 100), font=text_font)
        image.save(image_filename)
        return image_filename

    # Фильтруем по неделе, если указана
    if week_number is not None:
        timetable = [lesson for lesson in timetable if lesson.get('Номер недели') == week_number]
        if not timetable:
            width, height = 800, 200
            image = Image.new('RGB', (width, height), color=(245, 247, 250))
            draw = ImageDraw.Draw(image)
            try:
                text_font = ImageFont.truetype("G8.otf", size=24)
            except IOError:
                text_font = ImageFont.load_default()
            draw.text((50, 100), f"Нет занятий на неделе №{week_number}", fill=(100, 100, 100), font=text_font)
            image.save(image_filename)
            return image_filename

    # Группируем занятия по дням
    days = {}
    for lesson in timetable:
        date = lesson.get('Число', '')
        if date not in days:
            days[date] = []
        days[date].append(lesson)

    # Сортируем дни по дате
    sorted_days = sorted(days.items(), key=lambda x: datetime.strptime(x[0], "%Y.%m.%d") if x[0] else datetime.min)

    # Пастельные розово-фиолетовые цвета для разных типов занятий
    lesson_type_colors = {
        'Лекция': (186, 104, 200),           # Пастельный фиолетовый
        'Практические занятия': (255, 182, 193),  # Пастельный розовый
        'Лабораторная работа': (221, 160, 221),  # Сливовый
        'Семинар': (230, 190, 255),          # Лавандовый
    }
    default_color = (200, 180, 220)  # Пастельный фиолетово-серый по умолчанию

    # Подсчитываем максимальную высоту для столбцов с блоками
    header_height = 120
    block_padding = 15
    block_spacing = 15
    lesson_entry_height = 80  # Примерная высота одной записи

    # Вычисляем высоту для каждого дня
    left_days_order = ["Понедельник", "Среда", "Пятница"]
    right_days_order = ["Вторник", "Четверг", "Суббота"]

    days_by_name_temp = {}
    for date, lessons in sorted_days:
        day_name = lessons[0].get('День недели', '')
        if day_name:
            days_by_name_temp[day_name] = lessons

    # Вычисляем высоту левого столбца
    left_height = 0
    for day_name in left_days_order:
        if day_name in days_by_name_temp:
            lessons = days_by_name_temp[day_name]
            day_height = max(150, len(lessons) * lesson_entry_height + block_padding * 2)
            left_height += day_height + block_spacing

    # Вычисляем высоту правого столбца
    right_height = 0
    for day_name in right_days_order:
        if day_name in days_by_name_temp:
            lessons = days_by_name_temp[day_name]
            day_height = max(150, len(lessons) * lesson_entry_height + block_padding * 2)
            right_height += day_height + block_spacing

    max_column_height = max(left_height, right_height)
    estimated_height = header_height + max_column_height + 100  # +100 для футера
    height = max(1200, estimated_height)
    width = 1400

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

    # Шрифты - используем Montserrat-SemiBold для текста, seguiemj для цветных эмодзи (COLR)
    text_font_path = "Montserrat-SemiBold.ttf"
    emoji_font_path = "seguiemj.ttf"

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
    import os
    try:
        # Загружаем seguiemj.ttf который использует COLR формат для цветных эмодзи
        # COLR шрифты поддерживают обычные размеры, не требуют фиксированного размера
        # Pillow 10.0.0+ поддерживает COLR через embedded_color=True
        emoji_font = ImageFont.truetype(emoji_font_path, size=18)
        emoji_font_small = ImageFont.truetype(emoji_font_path, size=14)
        emoji_font_tiny = ImageFont.truetype(emoji_font_path, size=12)
        logging.info(f"seguiemj.ttf (COLR) загружен для цветных эмодзи из {os.path.abspath(emoji_font_path)}")
    except IOError as e:
        logging.error(f"Ошибка при загрузке {emoji_font_path}: {e}")
        # Fallback на OpenSansEmoji если seguiemj не найден
        try:
            emoji_font_path_fallback = "OpenSansEmoji.ttf"
            emoji_font = ImageFont.truetype(emoji_font_path_fallback, size=18)
            emoji_font_small = ImageFont.truetype(emoji_font_path_fallback, size=14)
            emoji_font_tiny = ImageFont.truetype(emoji_font_path_fallback, size=12)
            logging.warning("seguiemj.ttf не найден, используем OpenSansEmoji.ttf")
        except IOError:
            emoji_font = ImageFont.load_default()
            emoji_font_small = ImageFont.load_default()
            emoji_font_tiny = ImageFont.load_default()
            logging.warning("Эмодзи шрифты не найдены, используем default")

    # Рисуем заголовок с названием группы
    if group_name:
        title_text = f"Расписание занятий - {group_name}"
    else:
        title_text = "Расписание занятий"

    # Центрируем заголовок
    try:
        title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
    except:
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

    # Распределяем дни по столбикам
    left_days_order = ["Понедельник", "Среда", "Пятница"]
    right_days_order = ["Вторник", "Четверг", "Суббота"]

    column_width = (width - 60) // 2
    x_left = 20
    x_right = column_width + 40

    # Создаем словарь для быстрого доступа к дням
    days_by_name = {}
    for date, lessons in sorted_days:
        day_name = lessons[0].get('День недели', '')
        if day_name:
            days_by_name[day_name] = (date, lessons)

    # Размеры блоков
    block_padding = 15
    block_spacing = 15

    # Функция для отрисовки занятия в новом стиле
    def draw_lesson_entry(draw, lesson, x, y, max_width, text_font, emoji_font):
        time_str = lesson.get('Время занятия', 'Не указано')
        subject = lesson.get('Предмет', 'Не указано')
        teacher = lesson.get('ФИО преподавателя', 'Не указано')
        room = lesson.get('Номер кабинета', 'Не указано')

        # Форматируем время: заменяем ":" на "."
        time_formatted = time_str.replace(':', '.')

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
        except:
            time_text_width = draw.textlength(time_formatted, font=lesson_text_font)
            time_text_height = 14

        time_text_x = time_box_x + (time_box_width - time_text_width) // 2
        time_text_y = time_box_y + (time_box_height - time_text_height) // 2
        draw_text_with_emoji(draw, time_formatted, time_text_x, time_text_y, lesson_text_font, emoji_font, fill=(255, 255, 255))

        # Предмет с эмодзи книги
        subject_x = x
        subject_y = current_y + time_box_height + 8

        # Эмодзи книги вместо квадрата
        book_emoji = "📚"
        draw_text_with_emoji(draw, book_emoji, subject_x, subject_y, lesson_title_font, emoji_font, fill=(0, 0, 0))

        # Текст предмета
        subject_display = subject[:43] + "..." if len(subject) > 43 else subject
        try:
            emoji_width = draw.textlength(book_emoji, font=emoji_font)
        except:
            emoji_width = 20
        draw_text_with_emoji(draw, subject_display, subject_x + int(emoji_width) + 6, subject_y, lesson_title_font, emoji_font, fill=(0, 0, 0))

        # Преподаватель, кабинет и тип предмета с эмодзи
        info_y = subject_y + 22
        info_parts = []
        if teacher and teacher != 'Не указано':
            teacher_display = teacher[:18] + "..." if len(teacher) > 18 else teacher
            info_parts.append(f"👤 {teacher_display}")  # Используем простой эмодзи вместо составного
        if room and room != 'Не указано':
            info_parts.append(f"🏫 {room}")

        # Добавляем тип предмета
        lesson_type = lesson.get('Тип занятия', '')
        if lesson_type:
            # Выбираем эмодзи в зависимости от типа занятия
            type_emoji = "📖"  # По умолчанию
            if "Лекция" in lesson_type:
                type_emoji = "📝"
            elif "Практические" in lesson_type or "Практика" in lesson_type:
                type_emoji = "✏️"
            elif "Лабораторная" in lesson_type or "Лаборатория" in lesson_type:
                type_emoji = "🔬"
            elif "Семинар" in lesson_type:
                type_emoji = "💬"

            type_display = lesson_type[:15] + "..." if len(lesson_type) > 15 else lesson_type
            info_parts.append(f"{type_emoji} {type_display}")

        if info_parts:
            info_line = " | ".join(info_parts)
            # Текст информации с эмодзи
            draw_text_with_emoji(draw, info_line, x, info_y, lesson_text_font, emoji_font, fill=(0, 0, 0))

        return current_y + time_box_height + 8 + 22 + (22 if info_parts else 0) + 12

    # Отрисовываем левый столбик
    y_left = y_start
    for day_name in left_days_order:
        if day_name in days_by_name:
            date, lessons = days_by_name[day_name]
            lessons_sorted = sorted(lessons, key=lambda x: x.get('Время занятия', '') or '')

            # Вычисляем высоту блока на основе количества занятий
            day_header_height = 40
            block_content_height = len(lessons_sorted) * 80 + block_padding * 2 + day_header_height
            block_height = max(150, block_content_height)

            # Рисуем белый блок с розовой рамкой для дня
            draw_rounded_rectangle(
                draw, [x_left, y_left, x_left + column_width - 20, y_left + block_height],
                radius=12, fill=(255, 255, 255), outline=(255, 182, 193), width=2
            )

            # Эмодзи для дней недели
            day_emojis = {
                "Понедельник": "💙",
                "Вторник": "💚",
                "Среда": "💛",
                "Четверг": "🧡",
                "Пятница": "❤️",
                "Суббота": "💜"
            }
            day_emoji = day_emojis.get(day_name, "📅")

            # Заголовок дня недели
            day_header_text = f"{day_emoji} {day_name}"
            if date:
                day_header_text += f" ({date})"

            # Рисуем заголовок дня в верхней части блока
            header_bg_y = y_left + 5
            header_bg_height = day_header_height - 10
            draw_rounded_rectangle(
                draw, [x_left + 5, header_bg_y, x_left + column_width - 25, header_bg_y + header_bg_height],
                radius=6, fill=(255, 240, 245), outline=(255, 182, 193), width=1
            )

            # Текст дня недели - разделяем эмодзи и текст для правильного выравнивания
            day_emoji = day_emojis.get(day_name, "📅")
            day_name_text = f"{day_name}"
            if date:
                day_name_text += f" ({date})"

            # Рисуем эмодзи
            draw_text_with_emoji(draw, day_emoji, x_left + 12, header_bg_y + 8, day_font, emoji_font, fill=(100, 50, 100))

            # Вычисляем позицию текста после эмодзи
            try:
                emoji_width = draw.textlength(day_emoji, font=emoji_font)
            except:
                emoji_width = 25

            # Рисуем текст дня недели, поднимая его выше для выравнивания с эмодзи
            draw_text_with_emoji(draw, day_name_text, x_left + 12 + int(emoji_width) + 6, header_bg_y + 2, day_font, emoji_font, fill=(100, 50, 100))

            # Отрисовываем занятия в блоке
            lesson_y = y_left + block_padding + day_header_height
            for lesson in lessons_sorted:
                lesson_y = draw_lesson_entry(draw, lesson, x_left + block_padding, lesson_y, column_width - block_padding * 2 - 20, lesson_text_font, emoji_font)

            y_left += block_height + block_spacing

    # Отрисовываем правый столбик
    y_right = y_start
    for day_name in right_days_order:
        if day_name in days_by_name:
            date, lessons = days_by_name[day_name]
            lessons_sorted = sorted(lessons, key=lambda x: x.get('Время занятия', '') or '')

            # Вычисляем высоту блока на основе количества занятий
            day_header_height = 40
            block_content_height = len(lessons_sorted) * 80 + block_padding * 2 + day_header_height
            block_height = max(150, block_content_height)

            # Рисуем белый блок с розовой рамкой для дня
            draw_rounded_rectangle(
                draw, [x_right, y_right, x_right + column_width - 20, y_right + block_height],
                radius=12, fill=(255, 255, 255), outline=(255, 182, 193), width=2
            )

            # Эмодзи для дней недели
            day_emojis = {
                "Понедельник": "💙",
                "Вторник": "💚",
                "Среда": "💛",
                "Четверг": "💗 ",
                "Пятница": "❤️",
                "Суббота": "💜"
            }
            day_emoji = day_emojis.get(day_name, "📅")

            # Заголовок дня недели
            day_header_text = f"{day_emoji} {day_name}"
            if date:
                day_header_text += f" ({date})"

            # Рисуем заголовок дня в верхней части блока
            header_bg_y = y_right + 5
            header_bg_height = day_header_height - 10
            draw_rounded_rectangle(
                draw, [x_right + 5, header_bg_y, x_right + column_width - 25, header_bg_y + header_bg_height],
                radius=6, fill=(255, 240, 245), outline=(255, 182, 193), width=1
            )

            # Текст дня недели - разделяем эмодзи и текст для правильного выравнивания
            day_emoji = day_emojis.get(day_name, "📅")
            day_name_text = f"{day_name}"
            if date:
                day_name_text += f" ({date})"

            # Рисуем эмодзи
            draw_text_with_emoji(draw, day_emoji, x_right + 12, header_bg_y + 8, day_font, emoji_font, fill=(100, 50, 100))

            # Вычисляем позицию текста после эмодзи
            try:
                emoji_width = draw.textlength(day_emoji, font=emoji_font)
            except:
                emoji_width = 25

            # Рисуем текст дня недели, поднимая его выше для выравнивания с эмодзи
            draw_text_with_emoji(draw, day_name_text, x_right + 12 + int(emoji_width) + 6, header_bg_y + 2, day_font, emoji_font, fill=(100, 50, 100))

            # Отрисовываем занятия в блоке
            lesson_y = y_right + block_padding + day_header_height
            for lesson in lessons_sorted:
                lesson_y = draw_lesson_entry(draw, lesson, x_right + block_padding, lesson_y, column_width - block_padding * 2 - 20, lesson_text_font, emoji_font)

            y_right += block_height + block_spacing

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
    except:
        footer_width = draw.textlength(footer_text, font=footer_font)

    footer_x = (width - footer_width) // 2
    # Светлый серый цвет для текста футера
    draw_text_with_emoji(draw, footer_text, footer_x, footer_y, footer_font, emoji_font, fill=(180, 180, 180))

    # Рисуем сердечки
    try:
        hearts_bbox = draw.textbbox((0, 0), hearts, font=emoji_font_small)
        hearts_width = hearts_bbox[2] - hearts_bbox[0]
    except:
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

def draw_lesson(draw, lesson_info, x, y, text_font, emoji_font, max_width):
    """
    Рисует информацию о занятии.
    :param draw: Объект ImageDraw.
    :param lesson_info: Текст с информацией о занятии.
    :param x: Начальная координата X.
    :param y: Начальная координата Y.
    :param text_font: Шрифт для текста.
    :param emoji_font: Шрифт для эмодзи.
    :param max_width: Максимальная ширина текста.
    :return: Новое значение Y после отрисовки.
    """
    # Адаптируем размер шрифта, если текст не помещается
    font_size = 20
    while True:
        try:
            text_font = ImageFont.truetype("G8.otf", size=font_size)
            emoji_font = ImageFont.truetype("seguiemj.ttf", size=font_size)
        except IOError:
            text_font = ImageFont.load_default()
            emoji_font = ImageFont.load_default()

        # Проверяем, помещается ли текст по ширине
        text_width = max(draw.textlength(line, font=text_font) for line in lesson_info.split("\n"))
        if text_width <= max_width or font_size <= 10:
            break
        font_size -= 1

    # Рисуем текст с эмодзи
    for line in lesson_info.split("\n"):
        draw_text_with_emoji(draw, line, x, y, text_font, emoji_font)
        y += 20  # Отступ между строками

    return y

def draw_text_with_emoji(draw, text, x, y, text_font, emoji_font, fill=(0, 0, 0), image=None):
    """
    Рисует текст с эмодзи, используя разные шрифты.
    Для цветных эмодзи использует seguiemj.ttf с поддержкой COLR (Color Outline).
    :param draw: Объект ImageDraw.
    :param text: Текст для отрисовки.
    :param x: Начальная координата X.
    :param y: Начальная координата Y.
    :param text_font: Шрифт для текста.
    :param emoji_font: Шрифт для эмодзи (NotoColorEmoji.ttf).
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
