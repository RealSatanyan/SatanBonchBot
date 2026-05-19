"""Текстовое форматирование расписания и фильтры по дате.

Лист графа зависимостей: только stdlib + pytz. Не импортирует проектные модули.
"""

from datetime import datetime, timedelta

import pytz


def filter_group_lessons_by_date(timetable, date_str: str) -> list:
    """Занятия группы (дикт-формат) на дату вида '2026.05.18' (поле 'Число')."""
    if not isinstance(timetable, list):
        return []
    return [l for l in timetable if isinstance(l, dict) and l.get("Число") == date_str]


def filter_personal_lessons_by_date(timetable, date_str: str) -> list:
    """Занятия личного расписания (объекты ЛК) на дату вида '2026-05-18'."""
    if not timetable:
        return []
    return [l for l in timetable if getattr(l, "date", None) == date_str]


def _week_offset_for_date(target, today) -> int:
    """week_offset недели target относительно недели today (оба — date)."""
    today_monday = today - timedelta(days=today.weekday())
    target_monday = target - timedelta(days=target.weekday())
    return (target_monday - today_monday).days // 7


def _moscow_today():
    """Текущая дата по московскому времени."""
    return datetime.now(pytz.timezone("Europe/Moscow")).date()


def format_timetable(timetable, title: str = "Ваше расписание") -> str:
    """
    Форматирует список занятий в читаемый текст.
    :param timetable: Список занятий.
    :param title: Заголовок расписания.
    :return: Отформатированная строка с расписанием.
    """
    if not timetable:
        return f"📅 {title}\n\nЗанятий не найдено 🎉"

    formatted_timetable = f"📅 {title}:\n\n"

    # Группируем занятия по дням
    days = {}
    for lesson in timetable:
        date = lesson.date
        if date not in days:
            days[date] = []
        days[date].append(lesson)

    # Сортируем дни по дате
    sorted_days = sorted(days.items(), key=lambda x: datetime.strptime(x[0], "%Y-%m-%d"))

    for date, lessons in sorted_days:
        formatted_timetable += f"----------------------\n📌 *{date} ({lessons[0].day})*\n"
        for lesson in lessons:
            formatted_timetable += (
                f"⏰ *{lesson.time}* \n"
                f"📚 {lesson.subject} \n"
                f"🎓 {lesson.teacher} \n"
                f"🏫 {lesson.location} \n"
                f"🔹 Тип: {lesson.lesson_type}\n\n"
            )

    return formatted_timetable

def merge_lessons_by_groups(lessons: list) -> list:
    """
    Сливает занятия-потоки в расписании преподавателя/аудитории.

    Занятия, совпадающие по (неделя, день, дата, время, предмет, тип,
    преподаватель, аудитория) и отличающиеся только группой, объединяются
    в одну запись с полем 'Группы' — отсортированным списком названий групп.
    Так лекция-поток для пяти групп даёт одну строку вместо пяти.

    Занятие одной группы остаётся без изменений (поле 'Группа', без 'Группы').
    Порядок занятий сохраняется по первому появлению.
    """
    merged = {}
    order = []
    for lesson in lessons:
        if not isinstance(lesson, dict):
            continue
        key = (
            lesson.get('Номер недели'),
            lesson.get('Номер дня недели'),
            lesson.get('Число'),
            lesson.get('Время занятия'),
            lesson.get('Предмет'),
            lesson.get('Тип занятия'),
            lesson.get('ФИО преподавателя'),
            lesson.get('Номер кабинета'),
        )
        if key not in merged:
            entry = dict(lesson)
            entry['_groups'] = []
            merged[key] = entry
            order.append(key)
        entry = merged[key]
        group = lesson.get('Группа', '')
        if group and group not in entry['_groups']:
            entry['_groups'].append(group)

    result = []
    for key in order:
        entry = merged[key]
        groups = entry.pop('_groups')
        if len(groups) > 1:
            entry['Группы'] = sorted(groups)
        result.append(entry)
    return result


def format_timetable_dict(timetable: list, title: str = "Расписание", week_number: int = None) -> str:
    """
    Форматирует список занятий из словарей (формат TImetabels.py) в читаемый текст.
    :param timetable: Список словарей с занятиями.
    :param title: Заголовок расписания.
    :param week_number: Номер недели для фильтрации (None - все недели).
    :return: Отформатированная строка с расписанием.
    """
    if isinstance(timetable, str):
        return f"❌ {timetable}"

    if not timetable:
        return "📅 Расписание пусто"

    # Фильтруем по неделе, если указана
    if week_number is not None:
        timetable = [lesson for lesson in timetable if lesson.get('Номер недели') == week_number]
        if not timetable:
            return f"📅 Нет занятий на неделе №{week_number}"

    formatted_timetable = f"📅 {title}"
    if week_number is not None:
        formatted_timetable += f" (Неделя №{week_number})"
    formatted_timetable += ":\n\n"

    # Группируем занятия по дням
    days = {}
    for lesson in timetable:
        date = lesson.get('Число', '')
        if date not in days:
            days[date] = []
        days[date].append(lesson)

    # Сортируем дни по дате
    sorted_days = sorted(days.items(), key=lambda x: datetime.strptime(x[0], "%Y.%m.%d") if x[0] else datetime.min)

    for date, lessons in sorted_days:
        day_name = lessons[0].get('День недели', '')
        formatted_timetable += f"----------------------\n📌 *{date} ({day_name})*\n"

        # Сортируем занятия по времени
        lessons_sorted = sorted(lessons, key=lambda x: x.get('Время занятия', '') or '')

        for lesson in lessons_sorted:
            time_str = lesson.get('Время занятия', 'Не указано')
            subject = lesson.get('Предмет', 'Не указано')
            teacher = lesson.get('ФИО преподавателя', 'Не указано')
            room = lesson.get('Номер кабинета', 'Не указано')
            lesson_type = lesson.get('Тип занятия', '')
            group = lesson.get('Группа', '')
            groups = lesson.get('Группы')

            formatted_timetable += f"⏰ *{time_str}*\n"
            formatted_timetable += f"📚 {subject}\n"
            if groups:
                formatted_timetable += f"👥 Группы: {', '.join(groups)}\n"
            elif group:
                formatted_timetable += f"👥 Группа: {group}\n"
            if teacher and teacher != 'Не указано':
                formatted_timetable += f"🎓 {teacher}\n"
            if room and room != 'Не указано':
                formatted_timetable += f"🏫 {room}\n"
            if lesson_type:
                formatted_timetable += f"🔹 Тип: {lesson_type}\n"
            formatted_timetable += "\n"

    return formatted_timetable
