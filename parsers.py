"""Чистые парсеры HTML/текста с сайтов lk.sut.ru и cabinet.sut.ru.

Сюда вынесена логика разбора ответов сайта, отделённая от сетевого кода.
Модуль НЕ делает сетевых запросов и не имеет импорт-тайм side-effects,
поэтому его легко покрыть юнит-тестами (см. tests/). Зависит только от
bs4 и стандартной библиотеки.

Польза: при изменении вёрстки сайта тесты падают явно, а не «0 групп без
ошибки».
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

# Дни недели в порядке колонок таблицы расписания cabinet.sut.ru.
DAYS_OF_WEEK = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
DAYS_OF_WEEK_STR_TO_INT = {day: index for index, day in enumerate(DAYS_OF_WEEK)}


# --- cabinet.sut.ru: списки факультетов / групп ------------------------------

def parse_id_name_pairs(text: str) -> dict:
    """Разбирает ответ cabinet.sut.ru вида 'id1,name1;id2,name2;...' в {id: name}."""
    result = {}
    for chunk in (text or '').split(';'):
        chunk = chunk.strip()
        if not chunk or ',' not in chunk:
            continue
        key, _, value = chunk.partition(',')
        key, value = key.strip(), value.strip()
        if key and value:
            result[key] = value
    return result


# --- lk.sut.ru raspisanie.php: номер недели и week_param ---------------------

def parse_week_number(html: str) -> int:
    """
    Безопасно извлекает номер недели из HTML расписания.
    Если заголовок недели отсутствует или формат изменился, возвращает 0.
    """
    if not html:
        logging.warning("Пустой HTML расписания, используем неделю 0")
        return 0

    try:
        soup = BeautifulSoup(html, "html.parser")

        # 1) Быстрый путь: h3/h2, как было раньше.
        header = soup.find(["h3", "h2"])
        if header:
            header_text = header.get_text(" ", strip=True)
            m = re.search(r"№\s*(\d+)", header_text)
            if m:
                return int(m.group(1))

        # 2) Fallback: ищем "Неделя №X" по всему тексту страницы.
        page_text = soup.get_text(" ", strip=True)
        m = re.search(r"(Недел[яи]|Week)\s*№?\s*(\d+)", page_text, flags=re.IGNORECASE)
        if m:
            return int(m.group(2))

        # 3) Не нашли — оставляем 0, но логируем контекст.
        logging.warning("Не найден номер недели в расписании (нет h3/h2/паттерна 'Неделя №'), используем неделю 0")
        return 0
    except Exception as e:
        logging.error("Ошибка при разборе номера недели: %s", e, exc_info=True)
        return 0


def parse_week_param(html: str) -> int:
    """
    Извлекает параметр week для POST в raspisanie.php (это НЕ номер недели из заголовка).
    На странице он передается как showweek(<week_param>) и open_zan(<rasp>, <week_param>).
    """
    if not html:
        return 0

    try:
        soup = BeautifulSoup(html, "html.parser")

        # 1) Самый надежный способ: ссылка showweek(...) текущей недели обычно выделена <b>...</b>
        for a in soup.find_all("a"):
            onclick = a.get("onclick", "") or ""
            if not isinstance(onclick, str):
                continue
            m = re.search(r"showweek\(\s*(\d+)\s*\)", onclick)
            if m and a.find("b"):
                return int(m.group(1))

        # 2) Fallback: берем week_param из любой кнопки "Начать занятие" open_zan(rasp, week)
        for a in soup.find_all("a"):
            onclick = a.get("onclick", "") or ""
            if not isinstance(onclick, str):
                continue
            m = re.search(r"open_zan\(\s*\d+\s*,\s*(\d+)\s*\)", onclick)
            if m:
                return int(m.group(1))

        # 3) Fallback regex по сырому html
        m = re.search(r"showweek\(\s*(\d+)\s*\)[^<]*&nbsp;?<b>", html)
        if m:
            return int(m.group(1))

        m = re.search(r"open_zan\(\s*\d+\s*,\s*(\d+)\s*\)", html)
        if m:
            return int(m.group(1))

        return 0
    except Exception:
        logging.warning("Не удалось извлечь week_param из HTML, используем 0", exc_info=True)
        return 0


def extract_start_lesson_ids(html: str) -> tuple:
    """
    Извлекает только те занятия, где реально есть кнопка "Начать занятие".
    Это самый устойчивый критерий: onclick="open_zan(<rasp>, <week_param>)".
    """
    if not html:
        return tuple()

    try:
        soup = BeautifulSoup(html, "html.parser")
        ids: list = []
        for a in soup.find_all("a"):
            onclick = a.get("onclick", "") or ""
            if not isinstance(onclick, str):
                continue
            # На странице встречаются и "Кнопка появится... Обновить." (update_zan), и нужная нам open_zan
            m = re.search(r"open_zan\(\s*(\d+)\s*,\s*\d+\s*\)", onclick)
            if not m:
                continue
            # Дополнительно фильтруем по тексту, чтобы не схватить что-то случайное
            text = a.get_text(" ", strip=True)
            if text and "Начать занятие" in text:
                ids.append(m.group(1))

        return _dedupe_preserving_order(ids)
    except Exception:
        return tuple()


def extract_lesson_ids_fallback(html: str) -> tuple:
    """
    Запасной вариант извлечения lesson_id, если open_zan-кандидатов не нашлось.
    На lk.sut.ru часто используются элементы с id вида 'knopXXXX'.
    """
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        ids = []
        for tag in soup.find_all(True):
            _id = tag.get("id", "")
            if isinstance(_id, str) and _id.startswith("knop") and len(_id) > 4:
                ids.append(_id[4:])
        return _dedupe_preserving_order(ids)
    except Exception:
        return tuple()


def _dedupe_preserving_order(items) -> tuple:
    """Убирает дубликаты, сохраняя порядок первого вхождения."""
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


# --- cabinet.sut.ru raspisanie_all_new: таблица расписания группы ------------

def parse_timetable_table(html: str, group_name: str, first_day: datetime):
    """
    Разбирает HTML расписания группы (таблица class="simple-little-table").

    Возвращает отсортированный список занятий (dict) либо строку
    'Расписание не найдено', если таблицы на странице нет.

    first_day — дата начала первой недели (datetime); нужна для вычисления
    календарной даты каждого занятия.
    """
    soup = BeautifulSoup(html or "", 'html.parser')
    table = soup.find('table', class_='simple-little-table')
    if not table:
        return 'Расписание не найдено'

    timetable_data = []
    tbody = table.find('tbody')
    rows = tbody.find_all('tr') if tbody else []

    for row in rows:
        cells = row.find_all('td')
        if not cells:
            continue

        lesson_number_text = cells[0].text.strip()
        lesson_number_parts = lesson_number_text.split()
        lesson_number = lesson_number_parts[0] if lesson_number_parts else None

        lesson_time = None
        if lesson_number == '7':
            lesson_time = '20:00-21:35'
        elif len(lesson_number_parts) > 1:
            lesson_time = lesson_number_parts[1][1:-1]

        for day_index, cell in enumerate(cells[1:]):
            pair_divs = cell.find_all('div', class_='pair')
            if not pair_divs:
                continue
            if day_index >= len(DAYS_OF_WEEK):
                continue

            day_name = DAYS_OF_WEEK[day_index]
            day_of_week_int = DAYS_OF_WEEK_STR_TO_INT[day_name]

            for pair_div in pair_divs:
                subject_element = pair_div.find('span', class_='subect')
                subject = subject_element.strong.text.strip() if subject_element and subject_element.strong else None

                type_element = pair_div.find('span', class_='type')
                lesson_type = type_element.text.strip('()') if type_element else None

                teacher_element = pair_div.find('span', class_='teacher')
                teacher = teacher_element.text.strip() if teacher_element else None

                room_element = pair_div.find('span', class_='aud')
                room = room_element.get_text(' ', strip=True) if room_element else None

                weeks_element = pair_div.find('span', class_='weeks')
                week_number_str = (
                    weeks_element.text.strip('()').replace('н', '').replace('*', '')
                    if weeks_element else None
                )

                weeks_list = []
                if week_number_str:
                    weeks_list = [w.strip() for w in week_number_str.split(',') if w.strip()]

                for week_str in weeks_list:
                    try:
                        week_number = int(week_str)
                    except (ValueError, TypeError):
                        continue
                    lesson_date = first_day + timedelta(days=week_number * 7 + day_of_week_int)
                    timetable_data.append({
                        'Группа': group_name,
                        'Число': lesson_date.strftime('%Y.%m.%d'),
                        'День недели': day_name,
                        'Номер недели': week_number,
                        'Номер дня недели': day_of_week_int,
                        'Номер занятия': lesson_number,
                        'Время занятия': lesson_time,
                        'Предмет': subject,
                        'Тип занятия': lesson_type,
                        'ФИО преподавателя': teacher,
                        'Номер кабинета': room,
                    })

    return sorted(timetable_data, key=lambda x: (x['Номер недели'], x['Номер дня недели']))


# --- lk.sut.ru message.php: список входящих сообщений ------------------------

def parse_message_rows(page_html: str) -> list:
    """
    Разбирает одну страницу входящих сообщений (table id="mytable").
    Возвращает список словарей сообщений; при отсутствии таблицы — [].
    """
    soup = BeautifulSoup(page_html or "", 'html.parser')
    table = soup.find('table', id='mytable')
    if not table:
        return []

    messages = []
    rows = table.find_all('tr', id=lambda x: x and x.startswith('tr_'))
    for row in rows:
        try:
            row_id = row.get('id', '')
            if not row_id.startswith('tr_'):
                continue
            message_id = row_id.replace('tr_', '')

            cells = row.find_all('td')
            if len(cells) < 4:
                continue

            date_text = cells[0].get_text(strip=True)

            # stripped_strings даёт все текстовые узлы, игнорируя теги (в т.ч. img).
            filtered_parts = [
                part.strip()
                for part in cells[1].stripped_strings
                if part.strip() and len(part.strip()) > 1
            ]
            title = ' '.join(filtered_parts).strip() or 'Без названия'

            files = []
            for file_link in cells[2].find_all('a', href=True):
                file_name = file_link.get_text(strip=True)
                if file_name:
                    files.append({'name': file_name, 'url': file_link.get('href', '')})

            sender = cells[3].get_text(strip=True) or 'Неизвестно'

            row_style = row.get('style', '')
            is_unread = (
                'font-weight: bold' in row_style
                or 'font-weight:bold' in row_style.replace(' ', '')
            )

            messages.append({
                'id': message_id,
                'title': title,
                'date': date_text,
                'sender': sender,
                'files': files,
                'has_files': len(files) > 0,
                'is_unread': is_unread,
            })
        except Exception as e:
            logging.warning("Ошибка при парсинге строки сообщения: %s", e)
            continue

    return messages


# --- lk.sut.ru subconto/search.php: получатели по ФИО ------------------------

def parse_recipients(html_text: str) -> list:
    """
    Разбирает страницу поиска получателей, извлекая строки вида 'ФИО (id=12345)'.
    Возвращает список словарей {'id': int, 'label': 'ФИО (id=...)'}.
    """
    pattern = r">([^<]+?) \(id=(\d+)\)</td>"
    results = []
    for match in re.finditer(pattern, html_text or ""):
        name = match.group(1).strip()
        rid = int(match.group(2))
        results.append({"id": rid, "label": f"{name} (id={rid})"})
    return results
