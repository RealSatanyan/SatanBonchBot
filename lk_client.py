"""Клиент личного кабинета lk.sut.ru (задача 4.1, шаг 10).

Расширенный API-клиент ЛК (DebuggableBonchAPI), общий http-хелпер _lk_fetch
с ретраями, debug-дампы HTML-страниц, реестр API-инстансов по user_id и
аксессор публичного расписания (get_timetable_api).

Домен сообщений ЛК (get_message_api, поиск получателей, загрузка файла,
отправка сообщения) вынесен в lk_messages.py (задача B.2 фазы 5).

Разделяемое состояние:
- ``apis`` — изменяемый словарь ``{user_id: DebuggableBonchAPI}``. main.py и
  хэндлеры обращаются к нему как ``lk_client.apis`` (модуль-квалифицированный
  доступ), словарь мутируется на месте и не переприсваивается.
- ``timetable_api`` — синглтон ``TimetableBonchAPI``, переприсваивается в
  ``get_timetable_api``; внешний доступ строго через ``lk_client.timetable_api``.

Модуль НЕ импортирует main на уровне модуля (избегаем цикла с
lesson_controller). ``auto_login_user``/``perform_login``/``LessonController``
остаются в main.py.
"""

import asyncio
import html
import json
import logging
import os
import re
import time as time_module
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp
import pytz
from bs4 import BeautifulSoup
from yarl import URL as YarlURL

from bonchapi import BonchAPI, parser

import parsers
from config import get_lk_semaphore, LESSON_INTERVALS, BROWSER_HEADERS, USER_AGENT
from monitoring import _note_parser_failure

# `apis` и `timetable_api` намеренно НЕ входят в __all__: это разделяемое
# изменяемое состояние, доступ к нему — строго через lk_client.<имя> (модуль-
# квалифицированный), иначе `from lk_client import *` сделал бы снимок ссылки.
__all__ = [
    "DebuggableBonchAPI",
    "save_debug_dump",
    "get_timetable_api",
    "TimetableBonchAPI",
    "BROWSER_HEADERS",
]

# Импорт для работы с расписанием без авторизации
try:
    from public_timetable import BonchAPI as TimetableBonchAPI
except ImportError:
    # Если импорт не работает, используем альтернативный путь
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from public_timetable import BonchAPI as TimetableBonchAPI


# --- Общий http-хелпер для запросов в ЛК (задача A.1) ------------------------

# База экспоненциального бэкоффа: пауза перед повтором — _LK_RETRY_BACKOFF_SEC
# * 2**попытка (1.5с, 3с). Вынесена в модульную переменную — тесты её обнуляют.
_LK_RETRY_BACKOFF_SEC = 1.5
_LK_RETRIES = 3


async def _lk_fetch(
    session, method: str, url: str, *, idempotent: bool = True,
    read_body: bool = True, **kwargs
) -> tuple[int, str]:
    """Выполняет HTTP-запрос в ЛК через переданную session с ретраями.

    Возвращает (статус, тело). Тело декодируется через response.text(): по
    charset из Content-Type — страницы lk.sut.ru отдаются в cp1251, хардкод
    utf-8 ломал кириллицу в мойибейк. errors='replace' — устойчивость к
    усечённому/битому ответу (не падаем в UnicodeDecodeError). Сетевые сбои,
    таймауты и ответы 5xx повторяются с экспоненциальным бэкоффом: раньше
    первая же ошибка сети роняла вход / автоотметку / напоминание.

    ``idempotent`` — для запросов, которые безопасно повторять (GET, поиск,
    вход). Для НЕидемпотентных (отправка сообщения: повтор после успешного
    POST создаст дубль) передаётся ``idempotent=False`` — тогда повторяются
    только заведомо «доотправочные» сбои: соединение не установлено, запрос
    не ушёл (``aiohttp.ClientConnectorError``); 5xx и неоднозначные сбои
    после отправки не ретраятся, ошибка пробрасывается вызывающему.

    ``read_body=False`` — когда нужен только статус (этапы логина: открытие
    кабинета, ?login=no/yes). Тело тогда НЕ вычитывается: страница ЛК после
    входа большая/отдаётся медленно, и `response.read()` на ней может висеть
    до таймаута — а тело там не нужно. Возвращается пустая строка.

    proxy=None, заголовки, таймаут и cookie_jar берутся из переданной session;
    семафор ЛК (get_lk_semaphore) остаётся за вызывающим кодом.
    """
    request = session.post if method.upper() == "POST" else session.get
    status, text = 0, ""
    for attempt in range(_LK_RETRIES):
        try:
            async with request(url, proxy=None, **kwargs) as response:
                status = response.status
                if read_body:
                    # text() декодирует по charset ответа (lk.sut.ru — cp1251),
                    # errors='replace' — не падать на усечённом/битом теле.
                    text = await response.text(errors="replace")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            pre_send = isinstance(e, aiohttp.ClientConnectorError)
            if not idempotent and not pre_send:
                raise
            if attempt >= _LK_RETRIES - 1:
                raise
            logging.warning("Сбой запроса в ЛК (%s) — повтор (попытка %s): %s",
                             url, attempt + 1, e)
            await asyncio.sleep(_LK_RETRY_BACKOFF_SEC * (2 ** attempt))
            continue
        if status >= 500 and idempotent and attempt < _LK_RETRIES - 1:
            logging.warning("ЛК ответил %s на %s — повтор (попытка %s)",
                            status, url, attempt + 1)
            await asyncio.sleep(_LK_RETRY_BACKOFF_SEC * (2 ** attempt))
            continue
        return status, text
    return status, text


class DebuggableBonchAPI(BonchAPI):
    """
    Расширяет стандартный BonchAPI подробными логами при клике.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Постоянный CookieJar: куки должны жить между запросами, иначе ЛК отвечает ERR_MSG/403.
        self.cookie_jar = aiohttp.CookieJar(unsafe=True)
        # Оставляем атрибут для обратной совместимости (используется в других местах кода),
        # но наполняем его из cookie_jar после логина.
        self.cookies = None
        self._raw_timetable_cache_html: Optional[str] = None
        self._raw_timetable_cache_ts: Optional[float] = None

    def _refresh_cookies_view(self):
        """Обновляет self.cookies из текущего cookie_jar для совместимости с внешним кодом."""
        try:
            self.cookies = self.cookie_jar.filter_cookies(YarlURL("https://lk.sut.ru/"))
        except Exception:
            # В крайних случаях оставляем как есть
            pass

    def _get_week_safe(self, html: str) -> int:
        """Извлекает номер недели из HTML расписания (см. parsers.parse_week_number)."""
        return parsers.parse_week_number(html)

    def _get_week_param_safe(self, html: str) -> int:
        """Извлекает week_param для POST в raspisanie.php (см. parsers.parse_week_param)."""
        return parsers.parse_week_param(html)

    def _extract_start_lesson_ids(self, timetable_html: str) -> tuple[str, ...]:
        """Извлекает занятия с кнопкой «Начать занятие» (см. parsers.extract_start_lesson_ids)."""
        return parsers.extract_start_lesson_ids(timetable_html)

    async def login(self, email: str, password: str) -> bool:
        """
        Переопределяем метод login для использования HTTPS вместо HTTP.
        Исправляет проблему "The plain HTTP request was sent to HTTPS port".
        """
        # users/parole идут в query-строку — percent-кодируем, иначе спецсимвол
        # в пароле (`&`, `#`, `%`, пробел) молча исказит значение (A.2).
        email_q = urllib.parse.quote(email, safe='')
        password_q = urllib.parse.quote(password, safe='')
        AUTH = f'https://lk.sut.ru/cabinet/lib/autentificationok.php?users={email_q}&parole={password_q}'
        CABINET = 'https://lk.sut.ru/cabinet/'

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Referer": CABINET,
        }

        try:
            timeout_cfg = aiohttp.ClientTimeout(total=40)
            async with get_lk_semaphore():
                async with aiohttp.ClientSession(
                    timeout=timeout_cfg,
                    headers=headers,
                    trust_env=True,
                    cookie_jar=self.cookie_jar,
                    connector=aiohttp.TCPConnector(force_close=True),
                ) as session:
                    # Инициализируем сессию (получаем куки). Каждый запрос —
                    # через _lk_fetch: ретраи при сетевом сбое/5xx, устойчивое чтение.
                    # На этапах логина нужен только статус (куки берутся из
                    # заголовков ответа) — тело не вычитываем (read_body=False),
                    # иначе чтение большой страницы кабинета может висеть.
                    status, body = await _lk_fetch(session, "GET", CABINET, read_body=False)
                    if status >= 400:
                        logging.error("HTTP %s при открытии CABINET для %s. Тело: %s",
                                      status, email, body[:500])
                        return False

                    # Некоторым конфигурациям lk нужен ?login=no, оставляем как доп. шаг
                    status, body = await _lk_fetch(session, "GET", f"{CABINET}?login=no", read_body=False)
                    if status >= 400:
                        logging.error("HTTP %s при открытии CABINET?login=no для %s. Тело: %s",
                                      status, email, body[:500])
                        return False

                    status, text = await _lk_fetch(session, "POST", AUTH)
                    if status >= 400:
                        logging.error("HTTP %s при POST AUTH для %s. Тело: %s",
                                      status, email, text[:500])
                        return False

                    # Обрезаем пробелы и переносы строк, так как сервер может возвращать '\n1' вместо '1'
                    text_clean = (text or "").strip()
                    if text_clean == "1":
                        status, body = await _lk_fetch(session, "GET", f"{CABINET}?login=yes", read_body=False)
                        if status >= 400:
                            logging.error("HTTP %s при открытии CABINET?login=yes для %s. Тело: %s",
                                          status, email, body[:500])
                            return False
                        self._refresh_cookies_view()
                        logging.info("Успешная авторизация для %s", email)
                        return True

                    self._refresh_cookies_view()
                    logging.warning(
                        "Ошибка авторизации для %s: ответ сервера '%s' (очищенный: '%s')",
                        email,
                        text,
                        text_clean,
                    )
                    return False
        except Exception as e:
            logging.error("Ошибка при авторизации для %s: %s", email, e, exc_info=True)
            return False

    async def get_raw_timetable(self, week_number: int = False) -> str:
        """
        Получает HTML страницы raspisanie.php из lk.sut.ru.
        week_number — номер недели для навигации; без него берётся текущая.
        Запрос к ЛК идёт через прокси (trust_env=True подхватывает HTTP(S)_PROXY из env).
        """
        URL = "https://lk.sut.ru/cabinet/project/cabinet/forms/raspisanie.php"
        if week_number:
            URL += f"?week={week_number}"
        ERR_MSG = "У Вас нет прав доступа. Или необходимо перезагрузить приложение.."
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://lk.sut.ru/cabinet/",
            "Connection": "keep-alive",
        }

        timeout_cfg = aiohttp.ClientTimeout(total=40)

        # Небольшой кэш для текущей недели, чтобы напоминание "за 10 минут" и клик
        # не дёргали страницу слишком часто. Для конкретной недели кэш не используем.
        use_cache = not week_number
        if use_cache and self._raw_timetable_cache_html is not None and self._raw_timetable_cache_ts is not None:
            if (time_module.time() - self._raw_timetable_cache_ts) < 30:
                return self._raw_timetable_cache_html

        async with get_lk_semaphore():
            async with aiohttp.ClientSession(
                timeout=timeout_cfg,
                headers=headers,
                trust_env=True,
                cookie_jar=self.cookie_jar,
                connector=aiohttp.TCPConnector(force_close=True),
            ) as session:
                status, text = await _lk_fetch(session, "GET", URL)
                if status == 403:
                    # Оставляем текст как есть (он будет задемплен выше по стеку),
                    # но логируем маленький кусок для быстрого понимания.
                    logging.error("403 Forbidden при получении raspisanie.php. Первые 200 символов: %s", (text or "")[:200])
                # ЛК иногда возвращает короткое сообщение вместо HTML при протухшей сессии
                if (text or "").strip() == ERR_MSG:
                    logging.warning("ЛК вернул ERR_MSG вместо расписания — похоже, сессия истекла.")
                self._refresh_cookies_view()
                if use_cache:
                    self._raw_timetable_cache_html = text
                    self._raw_timetable_cache_ts = time_module.time()
                return text

    def _parse_today_start_lesson_details(
        self, timetable_html: str, today_date_str: str, target_pair_number: int
    ) -> Optional[dict]:
        """
        Парсит raspisanie.php и ищет "Начать занятие" для сегодняшнего дня и заданной пары (1..7).
        today_date_str формат: 'DD.MM.YYYY' из ЛК.
        """
        if not timetable_html:
            return None

        soup = BeautifulSoup(timetable_html, "html.parser")

        table = soup.find("table", class_="simple-little-table")
        if not table:
            return None

        current_day = None
        # Пробуем распарсить все tr из tbody
        tbody = table.find("tbody")
        if not tbody:
            return None

        for tr in tbody.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue

            # Заголовок дня: td[colspan=6] + <b>День</b> + <small><br/>DD.MM.YYYY</small>
            if len(tds) == 1 and tds[0].has_attr("colspan") and "6" in str(tds[0].get("colspan")):
                day_text = tds[0].get_text(" ", strip=True)
                m = re.search(r"(\d{2}\.\d{2}\.\d{4})", day_text)
                current_day = m.group(1) if m else None
                continue

            # Строка занятия обычно имеет несколько td
            if current_day != today_date_str:
                continue

            # В примере: [0]=пара, [1]=предмет, [2]=пусто/тип, [3]=кабинет, [4]=преподаватель, [5]=ссылки + кнопка
            if len(tds) < 6:
                continue

            pair_cell_text = tds[0].get_text(" ", strip=True)
            # Пример: "3 (13:00-14:35)"
            m_pair = re.search(r"(\d+)\s*\(", pair_cell_text)
            if not m_pair:
                continue
            pair_number = int(m_pair.group(1))
            if pair_number != target_pair_number:
                continue

            # Ищем "Начать занятие" (open_zan) внутри последней ячейки.
            # Для напоминаний нам кнопка может НЕ быть видна ещё (препод/время),
            # поэтому не возвращаем None, если ссылки нет.
            rasp = None
            week_param = None
            start_a = None
            last_td = tds[-1]
            for a in last_td.find_all("a"):
                a_text = a.get_text(" ", strip=True)
                onclick = a.get("onclick", "") or ""
                if "Начать занятие" in a_text and "open_zan" in onclick:
                    start_a = a
                    break
            if start_a:
                m_onclick = re.search(
                    r"open_zan\(\s*(\d+)\s*,\s*(\d+)\s*\)",
                    start_a.get("onclick", "") or "",
                )
                if m_onclick:
                    rasp = m_onclick.group(1)
                    week_param = m_onclick.group(2)

            # Предмет/кабинет
            subject = None
            b_tag = tds[1].find("b")
            if b_tag:
                subject = b_tag.get_text(" ", strip=True)
            else:
                subject = tds[1].get_text(" ", strip=True)

            room = tds[3].get_text(" ", strip=True) if len(tds) > 3 else None
            teacher = tds[4].get_text(" ", strip=True) if len(tds) > 4 else None

            return {
                "pair_number": pair_number,
                "subject": subject,
                "room": room,
                "teacher": teacher,
                "rasp": rasp,
                "week_param": week_param,
            }

        return None

    async def get_upcoming_start_lesson_details(
        self, now_dt: datetime, target_pair_index: int, window_minutes: int = 15
    ) -> Optional[dict]:
        """
        Возвращает детали пары (pair_number/room/subject) если:
        - до начала пары осталось <= window_minutes
        - и в raspisanie.php для сегодняшнего дня есть "Начать занятие" именно этой пары.
        target_pair_index: 0..6 (как в lesson_intervals)
        """
        start_time, end_time = LESSON_INTERVALS[target_pair_index]
        start_dt = datetime.combine(now_dt.date(), start_time, tzinfo=now_dt.tzinfo)
        delta_min = (start_dt - now_dt).total_seconds() / 60.0
        if delta_min < 0 or delta_min > window_minutes:
            return None

        today_date_str = now_dt.strftime("%d.%m.%Y")
        target_pair_number = target_pair_index + 1

        html_text = await self.get_raw_timetable()
        return self._parse_today_start_lesson_details(html_text, today_date_str, target_pair_number)

    async def get_current_lesson_details(
        self, now_dt: datetime, target_pair_index: int
    ) -> Optional[dict]:
        """
        Возвращает детали пары (pair_number/room/subject/teacher) для указанной пары (0..6)
        без проверок по времени (только парсинг строки в raspisanie.php).
        """
        today_date_str = now_dt.strftime("%d.%m.%Y")
        target_pair_number = target_pair_index + 1
        html_text = await self.get_raw_timetable()
        return self._parse_today_start_lesson_details(html_text, today_date_str, target_pair_number)

    def _extract_lesson_ids_fallback(self, timetable_html: str) -> tuple[str, ...]:
        """Запасной поиск lesson_id по id='knopXXXX' (см. parsers.extract_lesson_ids_fallback)."""
        return parsers.extract_lesson_ids_fallback(timetable_html)

    async def click_start_lesson(self, user_id=None) -> int:
        URL = "https://lk.sut.ru/cabinet/project/cabinet/forms/raspisanie.php"
        ERR_MSG = "У Вас нет прав доступа. Или необходимо перезагрузить приложение.."

        timetable = await self.get_raw_timetable()

        # Проверяем, не является ли ответ редиректом на login=no (истекшая сессия)
        if timetable and ("login=no" in timetable or "index.php?login=no" in timetable):
            raise ValueError("Session expired - redirect to login=no. Need to re-authenticate.")
        # Сессия может “протухнуть” и вернуться коротким текстом
        if (timetable or "").strip() == ERR_MSG:
            raise ValueError("Session expired - ERR_MSG from LK. Need to re-authenticate.")

        # Отдельный кейс: в ЛК нет назначенной группы -> расписания и кнопок не будет
        if "Ваша группа не определена" in (timetable or ""):
            raise ValueError("LK group not defined - cannot auto-click")

        # Номер недели — для логов; week_param — для POST (open=1&rasp=...&week=...)
        week_number = self._get_week_safe(timetable)
        week_param = self._get_week_param_safe(timetable)

        # Здоровье парсера: на валидной странице расписания week_param есть всегда
        # (сессионные/групповые кейсы отсеяны выше). Отсутствие = вёрстка ЛК
        # изменилась — фиксируем сбой; при всплеске придёт алерт админам.
        if not week_param:
            await _note_parser_failure(user_id)

        # Самый надежный набор кандидатов: только реальные кнопки "Начать занятие"
        lesson_ids = self._extract_start_lesson_ids(timetable)

        # Fallback: если на странице нет "Начать занятие" (например, рано), пробуем старые варианты
        if not lesson_ids:
            parsed_ids = await parser.get_lesson_id(timetable)
            lesson_ids = tuple(parsed_ids or ())
            if not lesson_ids:
                lesson_ids = self._extract_lesson_ids_fallback(timetable)

        logging.debug(
            "Неделя №%s (week_param=%s), найдено %s кандидат(ов) для клика: %s",
            week_number,
            week_param,
            len(lesson_ids),
            lesson_ids,
        )

        if not lesson_ids:
            # Сохраняем HTML для анализа: сайт мог поменять верстку.
            dump_path = save_debug_dump("no_candidates", timetable or "")
            if dump_path:
                logging.warning("Не найдено кандидатов для клика. HTML сохранен в %s", dump_path)
            else:
                logging.warning("Не найдено кандидатов для клика (debug-дамп не сохранён)")
            return 0

        if not week_param:
            # Без week_param клики не сработают (сервер ждёт внутренний индекс showweek(...))
            dump_path = save_debug_dump("no_week_param", timetable or "")
            if dump_path:
                logging.error("Не удалось извлечь week_param. HTML сохранен в %s", dump_path)
            else:
                logging.error("Не удалось извлечь week_param (debug-дамп не сохранён)")
            return 0

        clicked = 0
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": URL,
        }
        async with get_lk_semaphore():
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(40),
                headers=headers,
                trust_env=True,
                cookie_jar=self.cookie_jar,
                connector=aiohttp.TCPConnector(force_close=True),
            ) as session:
                for lesson_id in lesson_ids:
                    data = {"open": 1, "rasp": lesson_id, "week": week_param}
                    status, text = await _lk_fetch(session, "POST", URL, data=data)

                    # Проверяем ответ на ошибку авторизации
                    if text and ("login=no" in text or "index.php?login=no" in text):
                        raise ValueError(
                            "Session expired during lesson click - redirect to login=no. Need to re-authenticate."
                        )

                    if status == 200:
                        clicked += 1

                    logging.debug(
                        "Ответ на клик урока %s: статус %s, первые 200 символов: %s",
                        lesson_id,
                        status,
                        text[:200],
                    )

        self._refresh_cookies_view()
        return clicked

    async def get_messages_page(self, page: int = 1) -> dict:
        """
        Загружает ОДНУ страницу входящих сообщений ЛК (~20 шт).
        Возвращает {'messages': [...], 'total_pages': int}.

        Постраничная загрузка нужна для ленивой подгрузки в боте: страница 1
        отдаётся сразу, остальные — по мере листания. Вызывается на уже
        авторизованном клиенте (get_message_api это гарантирует).
        """
        BASE_URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message.php'
        empty = {'messages': [], 'total_pages': 1}

        headers = {
            **BROWSER_HEADERS,
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://lk.sut.ru/cabinet/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        page = max(1, page)
        page_url = f'{BASE_URL}?type=in' if page == 1 else f'{BASE_URL}?page={page}&type=in'

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
                # Прогрев сессии — только для первой страницы (необязательный шаг).
                if page == 1:
                    try:
                        async with session.get('https://lk.sut.ru/cabinet/', cookies=self.cookies, headers=headers) as cab_response:
                            cab_response.raise_for_status()
                    except Exception as e:
                        logging.debug("Инициализация кабинета пропущена: %s", e)

                # Запрос страницы — через _lk_fetch: разовый сетевой сбой
                # повторяется, а не превращается в ложное «нет сообщений».
                status, text = await _lk_fetch(
                    session, "GET", page_url, cookies=self.cookies, headers=headers)

            if status >= 400:
                logging.warning("HTTP %s на странице %s сообщений", status, page)
                return empty

            if 'ERRNO:' in text or 'Undefined index' in text:
                logging.warning("Ошибка PHP на странице %s сообщений", page)
                return empty

            messages = parsers.parse_message_rows(text)
            total_pages = parsers.parse_total_message_pages(text)
            logging.debug("Страница %s сообщений: %s шт (всего страниц: %s)", page, len(messages), total_pages)
            return {'messages': messages, 'total_pages': total_pages}
        except Exception as e:
            logging.error('Ошибка при получении страницы %s сообщений: %s', page, e, exc_info=True)
            return empty

    async def get_message(self, message_id: str) -> dict:
        """Получить конкретное сообщение ЛК по ID (клиент уже авторизован)."""
        URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/sendto2.php'

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
                data = {
                    'id': message_id,
                    'prosmotr': ''
                }
                # Просмотр сообщения идемпотентен — _lk_fetch повторит запрос
                # при разовом сетевом сбое.
                status, text = await _lk_fetch(
                    session, "POST", URL, cookies=self.cookies, data=data)

            if status >= 400:
                logging.warning("HTTP %s при получении сообщения ЛК %s", status, message_id)
                return {}

            # Парсим JSON ответ
            try:
                message_data = json.loads(text)
                # Декодируем HTML сущности в текстовых полях
                if 'annotation' in message_data:
                    message_data['annotation'] = html.unescape(message_data['annotation'])
                if 'name' in message_data:
                    message_data['name'] = html.unescape(message_data['name'])
                return message_data
            except json.JSONDecodeError:
                # Если это не JSON, пытаемся парсить HTML
                soup = BeautifulSoup(text, 'html.parser')
                message_data = {
                    'id': message_id,
                    'annotation': '',
                    'name': '',
                    'viddok': '',
                    'otvet': 0,
                    'idinfo': 0,
                    'files': '',
                    'sendto': message_id,
                    'otpr': 0,
                    'history': 0
                }

                # Пытаемся извлечь данные из HTML
                name_elem = soup.find('input', {'name': 'name'}) or soup.find('h2') or soup.find('h3')
                if name_elem:
                    message_data['name'] = name_elem.get('value', '') or name_elem.text.strip()

                annotation_elem = soup.find('textarea', {'name': 'annotation'}) or soup.find('div', class_='annotation')
                if annotation_elem:
                    message_data['annotation'] = annotation_elem.get('value', '') or annotation_elem.text.strip()

                return message_data
        except Exception as e:
            logging.error('Ошибка при получении сообщения ЛК %s: %s', message_id, e, exc_info=True)
            return {}


# Реестр API-инстансов по user_id. Изменяемый словарь — мутируется на месте,
# никогда не переприсваивается. Внешний доступ строго через lk_client.apis.
apis = {}  # Словарь для хранения экземпляров BonchAPI

# Экземпляр TimetableBonchAPI для работы с расписанием без авторизации.
# Переприсваивается лениво в get_timetable_api — внешний доступ строго
# через lk_client.timetable_api.
timetable_api = None  # Будет инициализирован при первом использовании


def _prune_debug_dumps(dump_dir: Path) -> None:
    """Оставляет в debug_dumps/ только N последних .html-файлов (DEBUG_DUMPS_KEEP)."""
    try:
        keep = max(1, int(os.getenv("DEBUG_DUMPS_KEEP", "30")))
    except ValueError:
        keep = 30
    try:
        dumps = sorted(
            dump_dir.glob("*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in dumps[keep:]:
            stale.unlink(missing_ok=True)
    except Exception:
        logging.warning("Не удалось почистить старые debug-дампы", exc_info=True)


def save_debug_dump(prefix: str, content: str) -> Optional[Path]:
    """
    Сохраняет HTML-дамп в debug_dumps/ для отладки парсеров.
    Управляется через .env (по умолчанию ВЫКЛ — opt-in):
      DEBUG_DUMPS=1        — включить дампы;
      DEBUG_DUMPS_KEEP=30  — сколько последних файлов хранить.
    Возвращает путь к файлу либо None (дампы отключены / ошибка записи).
    """
    if os.getenv("DEBUG_DUMPS", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    try:
        dump_dir = Path("debug_dumps")
        dump_dir.mkdir(exist_ok=True)
        ts = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%Y%m%d_%H%M%S_%f")
        dump_path = dump_dir / f"{prefix}_{ts}.html"
        dump_path.write_text(content or "", encoding="utf-8")
        _prune_debug_dumps(dump_dir)
        return dump_path
    except Exception:
        logging.warning("Не удалось сохранить debug-дамп '%s'", prefix, exc_info=True)
        return None


async def get_timetable_api():
    """
    Получает или создает экземпляр BonchAPI для работы с расписанием без авторизации.
    Инициализирует API и загружает список групп, как в CLI версии.
    """
    global timetable_api
    if timetable_api is None:
        # Используем дату начала семестра (можно вынести в конфигурацию)
        # По умолчанию используем текущую дату начала семестра
        # Можно получить из переменной окружения или использовать значение по умолчанию
        first_day = os.getenv('FIRST_DAY', '2026-02-03')  # Пример даты
        timetable_api = TimetableBonchAPI(first_day=first_day)
        # Как в CLI версии: сначала загружаем schet и группы
        await timetable_api.get_schet()
        await timetable_api.get_groups()
        logging.info(f"Инициализирован TimetableBonchAPI с {len(timetable_api.groups_id)} группами")
    return timetable_api
