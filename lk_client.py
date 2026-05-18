"""Клиент личного кабинета lk.sut.ru (задача 4.1, шаг 10).

Извлечён из main.py: расширенный API-клиент ЛК (DebuggableBonchAPI),
debug-дампы HTML-страниц, реестр API-инстансов по user_id и LK-сервис-функции
(работа с расписанием без авторизации, поиск получателей, загрузка файлов и
отправка сообщений в ЛК).

Чистая декомпозиция — поведение не меняется.

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
import logging
import os
import re
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp
import aiofiles
import pytz
from bs4 import BeautifulSoup
from yarl import URL as YarlURL

from bonchapi import BonchAPI, parser

import parsers
import db
from config import get_lk_semaphore, LESSON_INTERVALS
from monitoring import _note_parser_failure
from security import decrypt_password

# `apis` и `timetable_api` намеренно НЕ входят в __all__: это разделяемое
# изменяемое состояние, доступ к нему — строго через lk_client.<имя> (модуль-
# квалифицированный), иначе `from lk_client import *` сделал бы снимок ссылки.
__all__ = [
    "DebuggableBonchAPI",
    "save_debug_dump",
    "get_timetable_api",
    "get_message_api",
    "lk_search_recipients",
    "lk_upload_file",
    "lk_send_message",
    "TimetableBonchAPI",
    "BROWSER_HEADERS",
]

# Импорт для работы с расписанием без авторизации
try:
    from TImetabels import BonchAPI as TimetableBonchAPI, BROWSER_HEADERS
except ImportError:
    # Если импорт не работает, используем альтернативный путь
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from TImetabels import BonchAPI as TimetableBonchAPI, BROWSER_HEADERS


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
        AUTH = f'https://lk.sut.ru/cabinet/lib/autentificationok.php?users={email}&parole={password}'
        CABINET = 'https://lk.sut.ru/cabinet/'

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
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
                    # Инициализируем сессию (получаем куки)
                    async with session.get(CABINET, proxy=None) as response:
                        if response.status == 403:
                            body = (await response.text())[:500]
                            logging.error("403 при открытии CABINET для %s. Тело: %s", email, body)
                            return False
                        response.raise_for_status()

                    # Некоторым конфигурациям lk нужен ?login=no, оставляем как доп. шаг
                    async with session.get(f"{CABINET}?login=no", proxy=None) as response:
                        if response.status == 403:
                            body = (await response.text())[:500]
                            logging.error("403 при открытии CABINET?login=no для %s. Тело: %s", email, body)
                            return False
                        response.raise_for_status()

                    async with session.post(AUTH, proxy=None) as response:
                        if response.status == 403:
                            body = (await response.text())[:500]
                            logging.error("403 при POST AUTH для %s. Тело: %s", email, body)
                            return False
                        response.raise_for_status()
                        text = await response.text()

                    # Обрезаем пробелы и переносы строк, так как сервер может возвращать '\n1' вместо '1'
                    text_clean = (text or "").strip()
                    if text_clean == "1":
                        async with session.get(f"{CABINET}?login=yes", proxy=None) as response:
                            if response.status == 403:
                                body = (await response.text())[:500]
                                logging.error("403 при открытии CABINET?login=yes для %s. Тело: %s", email, body)
                                return False
                            response.raise_for_status()
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
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
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
                async with session.get(URL, proxy=None) as response:
                    text = await response.text()
                if response.status == 403:
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
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
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
                    async with session.post(URL, data=data, proxy=None) as resp:
                        text = await resp.text()

                    # Проверяем ответ на ошибку авторизации
                    if text and ("login=no" in text or "index.php?login=no" in text):
                        raise ValueError(
                            "Session expired during lesson click - redirect to login=no. Need to re-authenticate."
                        )

                    if resp.status == 200:
                        clicked += 1

                    logging.debug(
                        "Ответ на клик урока %s: статус %s, первые 200 символов: %s",
                        lesson_id,
                        resp.status,
                        text[:200],
                    )

        self._refresh_cookies_view()
        return clicked


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


async def get_message_api(user_id: int) -> Optional[TimetableBonchAPI]:
    """
    Получает экземпляр TimetableBonchAPI для работы с сообщениями пользователя.
    Использует cookies из существующего авторизованного API пользователя.
    """
    # Проверяем, есть ли уже авторизованный API для пользователя
    if user_id not in apis:
        # Пытаемся автоматически авторизовать (auto_login_user остаётся в main).
        import main
        success = await main.auto_login_user(user_id)
        if not success or user_id not in apis:
            logging.warning(f"Не удалось получить API для пользователя {user_id}")
            return None

    # Используем cookies из существующего API
    existing_api = apis[user_id]
    if not hasattr(existing_api, 'cookies') or not existing_api.cookies:
        logging.warning(f"У пользователя {user_id} нет cookies в API")
        # Попробуем переавторизоваться
        db.cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
        result = db.cursor.fetchone()
        if result:
            email, password = result
            await existing_api.login(email, decrypt_password(password))
            if not hasattr(existing_api, 'cookies') or not existing_api.cookies:
                return None
        else:
            return None

    # Создаем временный экземпляр TimetableBonchAPI только для вызова методов
    # Используем cookies из существующего API
    first_day = os.getenv('FIRST_DAY', '2026-02-03')
    message_api = TimetableBonchAPI(first_day=first_day)
    message_api.cookies = existing_api.cookies  # Используем cookies из существующего API

    # Отладочная информация
    logging.debug(f"Создан message_api для пользователя {user_id}, cookies: {len(list(message_api.cookies)) if message_api.cookies else 0} cookies")

    return message_api


async def lk_search_recipients(message_api: TimetableBonchAPI, query: str):
    """
    Поиск получателей в ЛК по ФИО через страницу поиска subconto.
    Возвращает список словарей вида {'id': int, 'label': 'ФИО И.О. (id=...)'}.
    """
    URL = "https://lk.sut.ru/cabinet/subconto/search.php"

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
            async with session.get(URL, params={"value": query}, cookies=message_api.cookies, proxy=None) as response:
                status = response.status
                response.raise_for_status()
                html_text = await response.text()

        # Парсим строки вида "ФИО (id=12345)"
        results = parsers.parse_recipients(html_text)

        if results:
            logging.info("Найдено %s получателей по запросу %r", len(results), query)
        else:
            logging.warning(
                "Поиск получателей %r: 0 совпадений (HTTP %s, длина %s). Фрагмент ответа: %s",
                query, status, len(html_text or ""),
                (html_text or "")[:400].replace("\n", " "),
            )
        return results
    except Exception as e:
        logging.error(f"Ошибка при поиске получателей в ЛК: {type(e).__name__} {e}", exc_info=True)
        return []


async def lk_upload_file(message_api: TimetableBonchAPI, filename: str, id: int = 0) -> int:
    """
    Загрузка файла в ЛК с использованием cookies уже авторизованного API.
    Реализация основана на SendMsgAPI.upload_file.
    """
    URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message_create_stud.php'

    try:
        async with aiofiles.open(filename, 'rb') as f:
            file = await f.read()

        data = aiohttp.FormData()
        data.add_field("id", str(id))
        data.add_field("upload", "")
        data.add_field('userfile', file, filename=os.path.basename(filename))

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
            async with session.post(URL, cookies=message_api.cookies, data=data, proxy=None) as response:
                response.raise_for_status()
                text = await response.text()
                match = re.search(r'data\.idinfo = "(\d+)"', text)
                if not match:
                    logging.error("Не удалось извлечь idinfo из ответа при загрузке файла")
                    return 0
                idinfo = match.group(1)
                logging.info('Файл успешно загружен в ЛК, idinfo=%s', idinfo)
                return int(idinfo)
    except Exception as e:
        logging.error(f'Ошибка при загрузке файла в ЛК: {type(e).__name__} {e}', exc_info=True)
        return 0


async def lk_send_message(
    message_api: TimetableBonchAPI,
    recipient_id: int,
    title: str,
    message_text: str,
    idinfo: int = 0,
) -> bool:
    """
    Отправка сообщения в ЛК с использованием cookies уже авторизованного API.
    Реализация основана на SendMsgAPI.send_msg.
    """
    URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message.php'

    data = {
        "idinfo": str(idinfo),
        "item": '0',
        "title": title,
        "mes_otvet": message_text,
        "adresat": str(recipient_id),
        "saveotv": ''
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
            async with session.post(URL, cookies=message_api.cookies, data=data, proxy=None) as response:
                response.raise_for_status()
                text = await response.text()
                # Успех — пустой ответ; ЛК часто отдаёт его как пробелы/перевод строки.
                if text.strip() == '':
                    logging.info('Сообщение в ЛК успешно отправлено (adresat=%s)', recipient_id)
                    return True
                else:
                    # Сервер иногда возвращает ошибку про link_url, но сообщение всё равно отправляется
                    # Проверяем, является ли это только ошибкой про link_url
                    if 'link_url' in text.lower() and 'undefined index' in text.lower():
                        logging.warning('Сервер вернул предупреждение про link_url, но сообщение должно быть отправлено (adresat=%s)', recipient_id)
                        return True
                    logging.error('Ошибка при отправке сообщения в ЛК, ответ сервера: %r', text)
                    return False
    except Exception as e:
        logging.error(f'Ошибка при отправке сообщения в ЛК: {type(e).__name__} {e}', exc_info=True)
        return False
