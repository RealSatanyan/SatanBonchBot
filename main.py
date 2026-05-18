import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiohttp
import aiofiles
from bonchapi import BonchAPI, parser  # Импортируем ваш API
import sqlite3
from contextlib import closing
from dotenv import load_dotenv
from datetime import datetime, time, timedelta
from pathlib import Path
from aiogram.types import InputFile
from aiogram.types import FSInputFile
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.session.aiohttp import AiohttpSession
from PIL import Image, ImageDraw, ImageFont
import pytz
import os
import sys
from aiogram.types import BotCommand
from typing import Optional
import html
import re
import time as time_module
from bs4 import BeautifulSoup
import random
from yarl import URL as YarlURL
from cryptography.fernet import Fernet, InvalidToken
import parsers

# Импорт для работы с расписанием без авторизации
try:
    from TImetabels import BonchAPI as TimetableBonchAPI, BROWSER_HEADERS
except ImportError:
    # Если импорт не работает, используем альтернативный путь
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from TImetabels import BonchAPI as TimetableBonchAPI, BROWSER_HEADERS

# Ограничиваем частоту/параллелизм запросов к lk.sut.ru, чтобы не ловить антибот/ERR_MSG/403
LK_CONCURRENCY = max(1, int(os.getenv("LK_CONCURRENCY", "1")))
LK_LOGIN_DELAY_SEC = float(os.getenv("LK_LOGIN_DELAY_SEC", "1.5"))
LK_LOGIN_JITTER_SEC = float(os.getenv("LK_LOGIN_JITTER_SEC", "1.0"))
_LK_SEMAPHORE_BY_LOOP = {}

# Единые интервалы пар для напоминаний/автоотметки.
LESSON_INTERVALS = [
    (time(9, 0), time(10, 35)),    # 1 пара
    (time(10, 45), time(12, 20)),  # 2 пара
    (time(13, 0), time(14, 35)),   # 3 пара
    (time(14, 45), time(16, 20)),  # 4 пара
    (time(16, 30), time(18, 5)),   # 5 пара
    (time(18, 15), time(19, 50)),  # 6 пара
    (time(20, 0), time(21, 35)),   # 7 пара
]


def get_lk_semaphore() -> asyncio.Semaphore:
    """
    В Python 3.9 asyncio.Semaphore привязывается к event loop при создании.
    Поэтому создаем семафор лениво для текущего loop (иначе получаем
    'Future attached to a different loop').
    """
    loop = asyncio.get_running_loop()
    sem = _LK_SEMAPHORE_BY_LOOP.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(LK_CONCURRENCY)
        _LK_SEMAPHORE_BY_LOOP[loop] = sem
    return sem


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

    async def click_start_lesson(self) -> int:
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

logging.getLogger('aiogram').setLevel(logging.DEBUG)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Прокси нужен ТОЛЬКО для запросов в ЛК (lk.sut.ru).
# Напрямую, без прокси, ходят: Telegram (api.telegram.org) и публичное расписание
# (cabinet.sut.ru, www.sut.ru) — последнее через прокси отвечает таймаутом.
#
# Прокси прокидывается через стандартные переменные HTTP(S)_PROXY: aiohttp-сессии для
# sut.ru создаются с trust_env=True и подхватывают их автоматически. Хосты из NO_PROXY
# при этом исключаются и идут напрямую. Telegram-сессия (aiogram AiohttpSession) env не
# читает вовсе, так что для неё прокси не применяется в любом случае.
NO_PROXY_HOSTS = "api.telegram.org,cabinet.sut.ru,www.sut.ru"
LK_PROXY = os.getenv("ALL_PROXY")
if LK_PROXY:
    for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[_proxy_var] = LK_PROXY
    for _no_proxy_var in ("NO_PROXY", "no_proxy"):
        os.environ[_no_proxy_var] = NO_PROXY_HOSTS
    logging.info("Прокси для ЛК (lk.sut.ru) включён: %s", YarlURL(LK_PROXY).with_user(None))
    logging.info("Напрямую, без прокси: %s", NO_PROXY_HOSTS)
else:
    logging.info("ALL_PROXY не задан — запросы в ЛК идут напрямую")

# --- Шифрование паролей в users.db -------------------------------------------
# Пароли от ЛК хранятся в БД зашифрованными (Fernet, симметричный AES).
# Ключ ENCRYPTION_KEY лежит в .env. Потеря ключа = пароли не восстановить
# (пользователям придётся войти заново). Храни копию ключа отдельно и надёжно.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
try:
    _fernet = Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None
except (ValueError, TypeError):
    logging.error("ENCRYPTION_KEY задан, но невалиден — шифрование паролей ОТКЛЮЧЕНО!")
    _fernet = None
if _fernet is None:
    logging.warning(
        "ENCRYPTION_KEY не задан в .env — пароли в users.db хранятся БЕЗ шифрования. "
        "Сгенерируйте ключ: "
        "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )


def encrypt_password(password: str) -> str:
    """Шифрует пароль для хранения в БД. Без ключа возвращает значение как есть."""
    if _fernet is None:
        return password
    return _fernet.encrypt(password.encode()).decode()


def decrypt_password(stored: str) -> str:
    """
    Расшифровывает пароль из БД. Обратно совместимо со старыми plaintext-записями:
    если значение не является валидным Fernet-токеном, возвращает его как есть.
    """
    if _fernet is None or not stored:
        return stored
    try:
        return _fernet.decrypt(stored.encode()).decode()
    except InvalidToken:
        return stored


# Telegram-сессия БЕЗ прокси.
tg_session = AiohttpSession()
conn = sqlite3.connect('users.db', check_same_thread=False)
cursor = conn.cursor()

with closing(sqlite3.connect('users.db')) as db:
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            notify_enabled INTEGER NOT NULL DEFAULT 1,
            notify_minutes INTEGER NOT NULL DEFAULT 10,
            autoclick_enabled INTEGER NOT NULL DEFAULT 1
        )
    ''')
    # Миграция уже существующих БД: добавляем недостающие колонки настроек
    # (ALTER TABLE ADD COLUMN идемпотентным не является).
    _user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if 'notify_enabled' not in _user_columns:
        db.execute('ALTER TABLE users ADD COLUMN notify_enabled INTEGER NOT NULL DEFAULT 1')
    if 'notify_minutes' not in _user_columns:
        db.execute('ALTER TABLE users ADD COLUMN notify_minutes INTEGER NOT NULL DEFAULT 10')
    if 'autoclick_enabled' not in _user_columns:
        db.execute('ALTER TABLE users ADD COLUMN autoclick_enabled INTEGER NOT NULL DEFAULT 1')
    db.commit()

bot = Bot(token=BOT_TOKEN, session=tg_session)
dp = Dispatcher()

controllers = {}  # Словарь для хранения контроллеров
apis = {}  # Словарь для хранения экземпляров BonchAPI
# Экземпляр BonchAPI для работы с расписанием без авторизации (преподаватели, кабинеты)
timetable_api = None  # Будет инициализирован при первом использовании
all_groups_timetable_cache = None  # Кэш расписания всех групп
timetable_loading = False  # Флаг загрузки расписания
timetable_progress_users = {}  # Словарь {user_id: message} для отправки прогресса
timetable_progress = {'current': 0, 'total': 0, 'start_time': None}  # Прогресс загрузки
# Состояния навигации по сообщениям
message_states = {}  # Словарь {user_id: {'messages': [], 'current_index': 0}} для навигации по сообщениям
pending_lk_messages = {}  # Словарь {(user_id, recipient_id): {'text': str, 'title': str, 'label': str}}
LOGIN_CMD_RE = re.compile(r"^/login(?:@\w+)?\s+(\S+)\s+(\S+)\s*$")
MAX_EMAIL_LEN = 254
MAX_PASSWORD_LEN = 256
# Email должен быть похож на email: это отсекает мусор и попытки инъекций
# (email подставляется в URL запроса авторизации в ЛК).
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def parse_login_credentials(message_text: str) -> tuple[str, str] | None:
    """
    Извлекает email и password только из ожидаемого формата /login.
    Возвращает None при невалидном формате.
    """
    if not message_text:
        return None

    match = LOGIN_CMD_RE.match(message_text.strip())
    if not match:
        return None

    email, password = match.group(1), match.group(2)
    if len(email) > MAX_EMAIL_LEN or len(password) > MAX_PASSWORD_LEN:
        return None

    if not EMAIL_RE.match(email):
        return None

    return email, password


# --- Rate-limit на попытки входа (защита от перебора паролей) -----------------
# In-memory троттл: не более LOGIN_RATE_LIMIT попыток входа на user_id
# за окно LOGIN_RATE_WINDOW_SEC секунд. Настраивается через .env.
LOGIN_RATE_LIMIT = max(1, int(os.getenv("LOGIN_RATE_LIMIT", "5")))
LOGIN_RATE_WINDOW_SEC = max(1, int(os.getenv("LOGIN_RATE_WINDOW_SEC", "300")))
_login_attempts: dict[int, list[float]] = {}


def check_login_rate_limit(user_id: int) -> int:
    """
    Регистрирует попытку входа и проверяет лимит.
    Возвращает 0, если вход разрешён, либо число секунд до следующей попытки.
    """
    now = time_module.monotonic()
    window_start = now - LOGIN_RATE_WINDOW_SEC
    attempts = [t for t in _login_attempts.get(user_id, ()) if t > window_start]
    if len(attempts) >= LOGIN_RATE_LIMIT:
        _login_attempts[user_id] = attempts
        return int(attempts[0] + LOGIN_RATE_WINDOW_SEC - now) + 1
    attempts.append(now)
    _login_attempts[user_id] = attempts
    return 0


def format_retry_after(seconds: int) -> str:
    """Человекочитаемое время ожидания для сообщений пользователю."""
    if seconds >= 60:
        minutes = (seconds + 59) // 60
        return f"{minutes} мин"
    return f"{seconds} сек"


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
    Управляется через .env:
      DEBUG_DUMPS=0        — полностью отключить дампы;
      DEBUG_DUMPS_KEEP=30  — сколько последних файлов хранить.
    Возвращает путь к файлу либо None (дампы отключены / ошибка записи).
    """
    if os.getenv("DEBUG_DUMPS", "1").strip().lower() in ("0", "false", "no", "off"):
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


class LessonController:
    def __init__(self, api, bot, user_id):
        self.api = api
        self.bot = bot
        self.user_id = user_id
        self.is_running = False
        self.task = None
        self.notified = False  # Флаг для отслеживания отправки уведомления
        self._last_success_lesson_key: Optional[str] = None
        self._last_upcoming_lesson_key: Optional[str] = None

        # Интервалы пар (начало и конец)
        self.lesson_intervals = LESSON_INTERVALS

    def _current_lesson_interval_index(self, now_time: time) -> Optional[int]:
        """
        Возвращает индекс интервала пары (0..6), если сейчас идёт пара, иначе None.
        """
        for i, (start_time, end_time) in enumerate(self.lesson_intervals):
            if self.is_time_between(start_time, end_time, now_time):
                return i
        return None

    def _upcoming_lesson_interval_index(
        self,
        now_dt: datetime,
        min_minutes_before_start: int = 9,
        max_minutes_before_start: int = 10,
    ) -> Optional[int]:
        """
        Возвращает индекс пары, если до её начала осталось от min до max минут.
        Нужен для стабильного напоминания "за 10 минут" с учётом минутного тика.
        """
        for i, (start_time, _end_time) in enumerate(self.lesson_intervals):
            # Используем дату now_dt и тот же TZ
            start_dt = datetime.combine(now_dt.date(), start_time, tzinfo=now_dt.tzinfo)
            delta_min = (start_dt - now_dt).total_seconds() / 60.0
            if min_minutes_before_start <= delta_min <= max_minutes_before_start:
                return i
        return None

    def is_time_between(self, start_time, end_time, now_time):
        """Проверка, находится ли текущее время в заданном интервале."""
        if start_time <= end_time:
            return start_time <= now_time <= end_time
        else:  # Интервал переходит через полночь
            return start_time <= now_time or now_time <= end_time

    def is_lesson_time(self, now_time):
        """Проверка, находится ли текущее время в интервале любой из пар."""
        for start_time, end_time in self.lesson_intervals:
            if self.is_time_between(start_time, end_time, now_time):
                return True
        return False

    async def start_lesson(self):
        if self.is_running:
            return "Автокликалка уже запущена."

        self.is_running = True
        moscow_tz = pytz.timezone('Europe/Moscow')

        while self.is_running:
            try:
                now_dt = datetime.now(moscow_tz)
                now = now_dt.time()

                # Напоминание о начале пары (один раз на пару). Включение и
                # «за сколько минут» настраиваются пользователем в разделе «Профиль».
                # Диапазон (N-1)..N нужен из-за периодической проверки раз в минуту.
                notify_enabled, notify_minutes = get_notify_settings(self.user_id)
                upcoming_idx = self._upcoming_lesson_interval_index(
                    now_dt,
                    min_minutes_before_start=max(1, notify_minutes - 1),
                    max_minutes_before_start=notify_minutes,
                ) if notify_enabled else None
                if upcoming_idx is not None:
                    lesson_key = f"{now_dt.strftime('%Y-%m-%d')}_upcoming_{upcoming_idx}"
                    if self._last_upcoming_lesson_key != lesson_key:
                        try:
                            start_time, _end_time = self.lesson_intervals[upcoming_idx]
                            start_dt = datetime.combine(now_dt.date(), start_time, tzinfo=now_dt.tzinfo)
                            minutes_left = max(0, int((start_dt - now_dt).total_seconds() // 60))
                            human_idx = upcoming_idx + 1

                            details = await self.api.get_upcoming_start_lesson_details(
                                now_dt=now_dt,
                                target_pair_index=upcoming_idx,
                                window_minutes=notify_minutes,
                            )
                            # Интервалы пар (LESSON_INTERVALS) — это просто сетка времени.
                            # Уведомляем ТОЛЬКО если эта пара реально есть в расписании
                            # на сегодня (details найдены). Нет пары -> молчим, ключ не
                            # фиксируем, чтобы при сбое загрузки расписания был ретрай.
                            if not details:
                                logging.info(
                                    "Пара %s в %s не отправлена: нет в расписании на сегодня (user_id=%s)",
                                    human_idx,
                                    now_dt.strftime("%H:%M"),
                                    self.user_id,
                                )
                            else:
                                room = details.get("room") or "—"
                                subject = details.get("subject") or ""
                                teacher = details.get("teacher") or ""
                                subj_part = f"\n📚 {subject}" if subject else ""
                                room_part = f"\n🚪 Аудитория: {room}" if room and room != "—" else f"\n🚪 Аудитория: —"
                                teacher_part = f"\n👨‍🏫 {teacher}" if teacher else ""
                                msg = (
                                    f"🔔 Через {minutes_left} мин начнётся {human_idx}-я пара."
                                    f"{subj_part}{room_part}{teacher_part}"
                                )

                                await self.bot.send_message(self.user_id, msg)
                                self._last_upcoming_lesson_key = lesson_key
                                logging.info(
                                    "Отправлено напоминание о паре: user_id=%s, pair=%s, minutes_left=%s",
                                    self.user_id,
                                    human_idx,
                                    minutes_left,
                                )
                        except Exception as notify_error:
                            logging.warning(
                                "Не удалось отправить напоминание о паре для user_id=%s: %s",
                                self.user_id,
                                notify_error,
                                exc_info=True,
                            )

                if self.is_lesson_time(now):
                    # Если уведомление еще не отправлено, отправляем его
                    if not self.notified:
                        self.notified = True  # Устанавливаем флаг, что уведомление отправлено

                    # Пытаемся выполнить клик
                    logging.debug("Попытка кликнуть занятие для пользователя %s", self.user_id)
                    clicked = await self.api.click_start_lesson()
                    if clicked > 0:
                        logging.info("Клик выполнен. Отправлено запросов: %s", clicked)
                        # Оповещение в TG: ровно одно сообщение на одну пару
                        now_dt = datetime.now(moscow_tz)
                        interval_idx = self._current_lesson_interval_index(now_dt.time())
                        # Ключ пары: дата + номер интервала (если по какой-то причине idx=None,
                        # то fallback на дату+час, чтобы не спамить)
                        if interval_idx is None:
                            lesson_key = now_dt.strftime("%Y-%m-%d_%H")
                        else:
                            lesson_key = f"{now_dt.strftime('%Y-%m-%d')}_lesson_{interval_idx}"

                        if self._last_success_lesson_key != lesson_key:
                            try:
                                await self.bot.send_message(
                                    self.user_id,
                                    "✅ Автоотметка: отметка выполнена.",
                                )
                                self._last_success_lesson_key = lesson_key
                            except Exception as mark_notify_error:
                                logging.warning(
                                    "Не удалось отправить сообщение об автоотметке для user_id=%s: %s",
                                    self.user_id,
                                    mark_notify_error,
                                    exc_info=True,
                                )
                    else:
                        logging.warning("Клик не выполнен: кандидатов для клика не найдено.")
                else:
                    # Если время пар закончилось, сбрасываем флаг уведомления
                    self.notified = False
                    logging.info("Сейчас не время пар. Клик не выполнен.")
                await asyncio.sleep(60)  # Минутный тик для точного напоминания перед парой
            except ValueError as e:
                # Обрабатываем ошибку истекшей сессии
                if "Session expired" in str(e) or "login=no" in str(e):
                    logging.warning(f"Сессия истекла для пользователя {self.user_id}. Попытка переавторизации...")
                    try:
                        # Пытаемся переавторизоваться
                        await self.reauthenticate()
                        logging.info(f"Переавторизация успешна для пользователя {self.user_id}")
                    except Exception as reauth_error:
                        logging.error(f"Ошибка переавторизации для пользователя {self.user_id}: {reauth_error}")
                        await self.bot.send_message(self.user_id, "⚠️ Ваша сессия истекла. Пожалуйста, выполните /login для повторной авторизации.")
                        self.is_running = False
                        break
                else:
                    if "LK group not defined" in str(e):
                        logging.error("В ЛК не назначена группа для пользователя %s — автоклик невозможен.", self.user_id)
                        try:
                            await self.bot.send_message(
                                self.user_id,
                                "⚠️ В вашем ЛК не назначена учебная группа (страница расписания пишет: «Ваша группа не определена»).\n"
                                "Автоотметка не сможет работать, пока деканат не проведёт приказ о распределении в группу.\n\n"
                                "После назначения группы — выполните /login ещё раз и запустите /start_lesson.",
                            )
                        except Exception:
                            pass
                        self.is_running = False
                        break
                    logging.error(f"Ошибка при выполнении клика: {e}", exc_info=True)
                    await self.capture_debug_artifacts(e)
                await asyncio.sleep(60)  # Пауза перед повторной попыткой (1 минута)
                continue  # Продолжаем цикл для повторной попытки
            except Exception as e:
                logging.error(f"Ошибка при выполнении клика: {e}", exc_info=True)
                await self.capture_debug_artifacts(e)
                await asyncio.sleep(60)  # Пауза перед повторной попыткой (1 минута)
                continue  # Продолжаем цикл для повторной попытки

        return "Автокликалка запущена."

    async def stop_lesson(self, user_id: int):
        if not self.is_running:
            return "Автокликалка уже остановлена."

        self.is_running = False
        if self.task:
            self.task.cancel()
            logging.info(f'Пользователь {user_id} остановил автокликалку.')
        return "Автокликалка остановлена."

    async def get_status(self):
        return "Автокликалка запущена." if self.is_running else "Автокликалка остановлена."

    async def reauthenticate(self):
        """
        Переавторизует пользователя при истечении сессии.
        """
        cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (self.user_id,))
        result = cursor.fetchone()
        if not result:
            raise ValueError(f"Не найдены данные для переавторизации пользователя {self.user_id}")
        
        email, password = result
        password = decrypt_password(password)
        # Создаем новый экземпляр API и авторизуемся
        apis[self.user_id] = DebuggableBonchAPI()
        await apis[self.user_id].login(email, password)
        # Обновляем ссылку на API в контроллере
        self.api = apis[self.user_id]

    async def dump_timetable_snapshot(self, reason: str) -> Optional[Path]:
        """
        Сохраняет HTML расписания для дальнейшего анализа.
        """
        try:
            raw_html = await self.api.get_raw_timetable()
            
            # Проверяем, не является ли ответ редиректом на login=no (истекшая сессия)
            if raw_html and ("login=no" in raw_html or "index.php?login=no" in raw_html):
                logging.warning("Обнаружен редирект на login=no - сессия истекла. Не сохраняем снимок. (%s)", reason)
                raise ValueError("Session expired - redirect to login=no")
            
            file_path = save_debug_dump(str(self.user_id), raw_html)
            if file_path:
                logging.error("Снимок расписания сохранен в %s (%s)", file_path, reason)
            return file_path
        except ValueError:
            # Не логируем ValueError как ошибку - это ожидаемая ситуация с истекшей сессией
            raise
        except Exception as dump_error:
            logging.error("Не удалось сохранить снимок расписания: %s", dump_error, exc_info=True)
            return None

    async def capture_debug_artifacts(self, error: Exception):
        """
        Снимает дополнительную отладочную информацию для проблемных сценариев.
        """
        # Не сохраняем снимки при истекшей сессии
        if isinstance(error, ValueError) and ("Session expired" in str(error) or "login=no" in str(error)):
            logging.warning("Пропускаем сохранение снимка из-за истекшей сессии")
            return
        
        if isinstance(error, AttributeError) and "NoneType" in str(error):
            try:
                await self.dump_timetable_snapshot("parser_get_week_failed")
            except ValueError:
                # Игнорируем ошибки истекшей сессии при сохранении снимка
                pass
    
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if is_registered(user_id):
        await message.answer(
            "👋 С возвращением!\n\nВыбери раздел в меню снизу 👇",
            reply_markup=main_menu_kb(),
        )
        return
    await message.answer(
        "👋 Привет! Я SatanBonchBot — помощник студента СПбГУТ.\n\n"
        "Что я умею:\n"
        "📅 Расписание — групп, преподавателей и аудиторий\n"
        "✅ Автоотметка — сам отмечаю тебя на парах в ЛК\n"
        "✉️ Сообщения ЛК — читать и отправлять\n"
        "🔔 Уведомления о начале пар\n\n"
        "Расписание доступно сразу — жми «📅 Расписание» в меню снизу.\n"
        "Для автоотметки, личного расписания и сообщений нужно войти "
        "в личный кабинет СПбГУТ.",
        reply_markup=main_menu_kb(),
    )
    await message.answer(
        "Подключить личный кабинет?",
        reply_markup=login_prompt_kb(),
    )

@dp.message(Command("login"))
async def cmd_login(message: types.Message, state: FSMContext):
    await state.clear()
    parsed = parse_login_credentials(message.text or "")
    try:
        await message.delete()
    except Exception:
        pass
    if not parsed:
        await message.answer(
            "Чтобы войти в ЛК, нажми кнопку ниже.\n"
            "Либо отправь одной строкой: /login <email> <пароль>",
            reply_markup=login_prompt_kb(),
        )
        return
    email, password = parsed
    retry_after = check_login_rate_limit(message.from_user.id)
    if retry_after:
        await message.answer(
            f"⏳ Слишком много попыток входа. Попробуй снова через {format_retry_after(retry_after)}.",
            reply_markup=login_prompt_kb(),
        )
        return
    status = await message.answer("⏳ Вхожу в ЛК...")
    ok = await perform_login(message.from_user.id, email, password)
    if ok:
        try:
            await status.edit_text("✅ Готово! Ты вошёл в личный кабинет.")
        except Exception:
            pass
        await message.answer("Меню — снизу 👇", reply_markup=main_menu_kb())
    else:
        try:
            await status.edit_text("❌ Не удалось войти. Проверь email и пароль.")
        except Exception:
            pass

@dp.message(Command("start_lesson"))
async def cmd_start_lesson(message: types.Message):
    user_id = message.from_user.id
    if user_id not in controllers:
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in controllers:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    controller = controllers[user_id]  # Используем контроллер пользователя
    if controller.is_running:
        await message.answer("Автокликалка уже запущена.")
        return

    controller.task = asyncio.create_task(controller.start_lesson())
    set_autoclick_enabled(user_id, True)
    await message.answer("Автокликалка запущена.")

@dp.message(Command("stop_lesson"))
async def cmd_stop_lesson(message: types.Message):
    user_id = message.from_user.id
    if user_id not in controllers:
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in controllers:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    controller = controllers[user_id]  # Используем контроллер пользователя
    if not controller.is_running:
        await message.answer("Автокликалка уже остановлена.")
        return

    await controller.stop_lesson(user_id)
    set_autoclick_enabled(user_id, False)
    await message.answer("Автокликалка остановлена.")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    if user_id not in controllers:
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in controllers:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    controller = controllers[user_id]  # Используем контроллер пользователя
    status = await controller.get_status()
    await message.answer(status)

@dp.message(Command("test_notify"))
async def cmd_test_notify(message: types.Message, uid: int = None):
    """
    Ручная проверка доставки уведомления о паре в Telegram.
    """
    user_id = uid if uid is not None else message.from_user.id

    test_msg = (
        "🔔 Через 10 мин начнётся 3-я пара.\n"
        "📚 Тестовое уведомление (проверка доставки)\n"
        "🚪 Аудитория: 531; Б22/2\n"
        "👨‍🏫 Преподаватель: Тест Т.Т."
    )

    try:
        await bot.send_message(user_id, test_msg)
        await message.answer("✅ Тестовое уведомление отправлено.")
    except Exception as e:
        logging.warning("Не удалось отправить тестовое уведомление для user_id=%s: %s", user_id, e, exc_info=True)
        await message.answer("❌ Не удалось отправить тестовое уведомление. Попробуй позже.")

@dp.message(Command("my_account"))
async def cmd_my_account(message: types.Message):
    user_id = message.from_user.id
    logging.info(f"Команда /my_account от пользователя {user_id}")
    
    cursor.execute('SELECT email FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    status_parts = []
    if result:
        status_parts.append(f"📧 Email: {result[0]}")
    else:
        status_parts.append("❌ Нет сохраненного аккаунта в БД")
        await message.answer("\n".join(status_parts))
        return
    
    # Проверяем состояние авторизации ДО попытки восстановления
    has_api_before = user_id in apis
    has_controller_before = user_id in controllers
    
    status_parts.append(f"🔑 API авторизован: {'✅ Да' if has_api_before else '❌ Нет'}")
    status_parts.append(f"🎮 Контроллер создан: {'✅ Да' if has_controller_before else '❌ Нет'}")
    
    # Если пользователь есть в БД, но нет авторизации, пытаемся восстановить
    if not has_api_before or not has_controller_before:
        status_parts.append("\n⚠️ Обнаружена проблема с авторизацией. Попробую восстановить...")
        logging.info(f"Попытка восстановить авторизацию для пользователя {user_id}")
        success = await auto_login_user(user_id)
        
        # Проверяем состояние ПОСЛЕ попытки восстановления
        has_api_after = user_id in apis
        has_controller_after = user_id in controllers
        
        if success and has_api_after and has_controller_after:
            status_parts.append("✅ Авторизация восстановлена!")
            status_parts.append(f"🔑 API авторизован: ✅ Да")
            status_parts.append(f"🎮 Контроллер создан: ✅ Да")
        else:
            status_parts.append("❌ Не удалось восстановить авторизацию.")
            status_parts.append("💡 Выполните /login <email> <password> для повторной авторизации.")
            logging.warning(f"Не удалось восстановить авторизацию для пользователя {user_id}. Success: {success}, has_api: {has_api_after}, has_controller: {has_controller_after}")
    
    # Показываем статус автокликалки, если контроллер есть
    if user_id in controllers:
        controller = controllers[user_id]
        status_parts.append(f"⏯️ Автокликалка: {'🟢 Запущена' if controller.is_running else '🔴 Остановлена'}")
    
    await message.answer("\n".join(status_parts))

def format_timetable(timetable) -> str:
    """
    Форматирует список занятий в читаемый текст.
    :param timetable: Список занятий.
    :return: Отформатированная строка с расписанием.
    """
    formatted_timetable = "📅 Ваше расписание:\n\n"
    
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
            
            formatted_timetable += f"⏰ *{time_str}*\n"
            formatted_timetable += f"📚 {subject}\n"
            if group:
                formatted_timetable += f"👥 Группа: {group}\n"
            if teacher and teacher != 'Не указано':
                formatted_timetable += f"🎓 {teacher}\n"
            if room and room != 'Не указано':
                formatted_timetable += f"🏫 {room}\n"
            if lesson_type:
                formatted_timetable += f"🔹 Тип: {lesson_type}\n"
            formatted_timetable += "\n"
    
    return formatted_timetable


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

@dp.callback_query(F.data.startswith("image_week_"))
async def process_image_week(callback_query: CallbackQuery):
    # Извлекаем смещение недели из callback_data
    week_offset = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id

    if user_id not in apis:
        await callback_query.answer("Сначала авторизуйтесь с помощью /login.", show_alert=True)
        return

    try:
        # Получаем расписание для выбранной недели
        timetable = await apis[user_id].get_timetable(week_offset=week_offset)

        # Генерируем изображение
        image_path = generate_timetable_image(timetable)

        # Проверяем, что файл существует
        if not os.path.exists(image_path):
            await callback_query.answer("Ошибка: изображение не было создано.", show_alert=True)
            return

        # Создаем объект FSInputFile
        photo = FSInputFile(image_path)

        # Отправляем изображение пользователю
        await callback_query.message.answer_photo(photo)

        # Подтверждаем обработку callback
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Ошибка при отправке изображения: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось выполнить действие. Попробуй позже.", show_alert=True)
        
def get_week_navigation_buttons(week_offset: int = 0) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с кнопками для навигации по неделям.
    :param week_offset: Текущее смещение недели.
    :return: InlineKeyboardMarkup с кнопками.
    """
    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Предыдущая неделя", callback_data=f"prev_week_{week_offset - 1}"),
            InlineKeyboardButton(text="Следующая неделя ➡️", callback_data=f"next_week_{week_offset + 1}"),
        ],
        [
            InlineKeyboardButton(text="Эта неделя", callback_data="current_week_0"),
        ],
        [
            InlineKeyboardButton(text="🖼️ Показать картинкой", callback_data=f"image_week_{week_offset}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_teacher_week_navigation_buttons(teacher_name: str, week_number: int = None) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с кнопками для навигации по неделям расписания преподавателя.
    :param teacher_name: Имя преподавателя.
    :param week_number: Текущий номер недели.
    :return: InlineKeyboardMarkup с кнопками.
    """
    if week_number is None:
        week_number = 0
    
    # Кодируем имя преподавателя для безопасной передачи в callback_data
    import base64
    encoded_name = base64.b64encode(teacher_name.encode('utf-8')).decode('utf-8')
    
    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Предыдущая неделя", callback_data=f"prev_teacher_week_{encoded_name}_{week_number - 1}"),
            InlineKeyboardButton(text="Следующая неделя ➡️", callback_data=f"next_teacher_week_{encoded_name}_{week_number + 1}"),
        ],
        [
            InlineKeyboardButton(text="Все недели", callback_data=f"all_teacher_weeks_{encoded_name}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_classroom_week_navigation_buttons(classroom_number: str, week_number: int = None) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с кнопками для навигации по неделям расписания кабинета.
    :param classroom_number: Номер кабинета.
    :param week_number: Текущий номер недели.
    :return: InlineKeyboardMarkup с кнопками.
    """
    if week_number is None:
        week_number = 0
    
    # Кодируем номер кабинета для безопасной передачи в callback_data
    import base64
    encoded_number = base64.b64encode(classroom_number.encode('utf-8')).decode('utf-8')
    
    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Предыдущая неделя", callback_data=f"prev_classroom_week_{encoded_number}_{week_number - 1}"),
            InlineKeyboardButton(text="Следующая неделя ➡️", callback_data=f"next_classroom_week_{encoded_number}_{week_number + 1}"),
        ],
        [
            InlineKeyboardButton(text="Все недели", callback_data=f"all_classroom_weeks_{encoded_number}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_group_week_navigation_buttons(group_name: str, week_number: int = None) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с кнопками для навигации по неделям расписания группы.
    :param group_name: Название группы.
    :param week_number: Текущий номер недели.
    :return: InlineKeyboardMarkup с кнопками.
    """
    if week_number is None:
        week_number = 0
    
    # Кодируем название группы для безопасной передачи в callback_data
    import base64
    encoded_name = base64.b64encode(group_name.encode('utf-8')).decode('utf-8')
    
    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Предыдущая неделя", callback_data=f"prev_group_week_{encoded_name}_{week_number - 1}"),
            InlineKeyboardButton(text="Следующая неделя ➡️", callback_data=f"next_group_week_{encoded_name}_{week_number + 1}"),
        ],
        [
            InlineKeyboardButton(text="🖼️ Картинка", callback_data=f"image_group_week_{encoded_name}_{week_number}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.callback_query(F.data.startswith("prev_teacher_week_") | F.data.startswith("next_teacher_week_") | F.data.startswith("all_teacher_weeks_"))
async def process_teacher_week_navigation(callback_query: CallbackQuery):
    """
    Обработчик переключения недель для расписания преподавателя.
    """
    callback_data = callback_query.data
    
    try:
        import base64
        
        if callback_data.startswith("prev_teacher_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            teacher_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("next_teacher_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            teacher_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("all_teacher_weeks_"):
            encoded_name = callback_data.replace("all_teacher_weeks_", "")
            teacher_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
            week_number = None
        else:
            await callback_query.answer("Неизвестная команда", show_alert=True)
            return
        
        # Получаем расписание всех групп
        global all_groups_timetable_cache
        if all_groups_timetable_cache is None:
            await callback_query.answer("Расписание еще не загружено. Используйте команду /teacher_timetable", show_alert=True)
            return
        
        # Фильтруем по преподавателю
        teacher_timetable = TimetableBonchAPI.teacher_timetable(all_groups_timetable_cache, teacher_name)
        
        if not teacher_timetable:
            await callback_query.answer(f"Не найдено занятий для преподавателя: {teacher_name}", show_alert=True)
            return
        
        # Форматируем расписание
        formatted_timetable = format_timetable_dict(teacher_timetable, f"Расписание преподавателя: {teacher_name}", week_number=week_number)
        
        # Обновляем кнопки
        reply_markup = get_teacher_week_navigation_buttons(teacher_name, week_number)
        
        # Редактируем сообщение
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        await callback_query.answer()
    
    except Exception as e:
        logging.error(f"Ошибка при переключении недели преподавателя: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось выполнить действие. Попробуй позже.", show_alert=True)

@dp.callback_query(F.data.startswith("prev_classroom_week_") | F.data.startswith("next_classroom_week_") | F.data.startswith("all_classroom_weeks_"))
async def process_classroom_week_navigation(callback_query: CallbackQuery):
    """
    Обработчик переключения недель для расписания кабинета.
    """
    callback_data = callback_query.data
    
    try:
        import base64
        
        if callback_data.startswith("prev_classroom_week_"):
            parts = callback_data.split("_", 3)
            encoded_number = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            classroom_number = base64.b64decode(encoded_number.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("next_classroom_week_"):
            parts = callback_data.split("_", 3)
            encoded_number = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            classroom_number = base64.b64decode(encoded_number.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("all_classroom_weeks_"):
            encoded_number = callback_data.replace("all_classroom_weeks_", "")
            classroom_number = base64.b64decode(encoded_number.encode('utf-8')).decode('utf-8')
            week_number = None
        else:
            await callback_query.answer("Неизвестная команда", show_alert=True)
            return
        
        # Получаем расписание всех групп
        global all_groups_timetable_cache
        if all_groups_timetable_cache is None:
            await callback_query.answer("Расписание еще не загружено. Используйте команду /classroom_timetable", show_alert=True)
            return
        
        # Фильтруем по кабинету
        classroom_timetable = TimetableBonchAPI.classroom_timetable(all_groups_timetable_cache, classroom_number)
        
        if not classroom_timetable:
            await callback_query.answer(f"Не найдено занятий для кабинета: {classroom_number}", show_alert=True)
            return
        
        # Форматируем расписание
        formatted_timetable = format_timetable_dict(classroom_timetable, f"Расписание кабинета: {classroom_number}", week_number=week_number)
        
        # Обновляем кнопки
        reply_markup = get_classroom_week_navigation_buttons(classroom_number, week_number)
        
        # Редактируем сообщение
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        await callback_query.answer()
    
    except Exception as e:
        logging.error(f"Ошибка при переключении недели кабинета: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось выполнить действие. Попробуй позже.", show_alert=True)

@dp.callback_query(F.data.startswith("prev_group_week_") | F.data.startswith("next_group_week_") | F.data.startswith("image_group_week_"))
async def process_group_week_navigation(callback_query: CallbackQuery):
    """
    Обработчик переключения недель для расписания группы и генерации изображений.
    """
    callback_data = callback_query.data
    
    try:
        import base64
        
        if callback_data.startswith("prev_group_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            group_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("next_group_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            group_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("image_group_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            group_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        else:
            await callback_query.answer("Неизвестная команда", show_alert=True)
            return
        
        # Получаем расписание всех групп
        global all_groups_timetable_cache
        if all_groups_timetable_cache is None:
            await callback_query.answer("Расписание еще не загружено. Используйте команду /group_timetable", show_alert=True)
            return
        
        # Получаем расписание группы
        if group_name not in all_groups_timetable_cache:
            await callback_query.answer(f"Группа '{group_name}' не найдена в расписании", show_alert=True)
            return
        
        timetable = all_groups_timetable_cache[group_name]
        
        if not timetable or isinstance(timetable, str):
            await callback_query.answer(f"Расписание для группы '{group_name}' недоступно", show_alert=True)
            return
        
        # Если это запрос на генерацию изображения
        if callback_data.startswith("image_group_week_"):
            await callback_query.answer("⏳ Генерирую изображение...")
            try:
                # Генерируем изображение для текущей недели
                image_path = generate_timetable_image_from_dict(
                    timetable,
                    f"Расписание группы {group_name}",
                    week_number=week_number,
                    group_name=group_name
                )
                
                # Проверяем, что файл существует
                if os.path.exists(image_path):
                    photo = FSInputFile(image_path)
                    await callback_query.message.answer_photo(
                        photo,
                        caption=f"📅 Расписание группы {group_name} (Неделя №{week_number})"
                    )
                    # Удаляем временный файл после отправки
                    try:
                        os.remove(image_path)
                    except Exception as e:
                        logging.warning(f"Не удалось удалить временный файл {image_path}: {e}")
                    await callback_query.answer("✅ Изображение отправлено")
                else:
                    logging.error(f"Изображение не было создано: {image_path}")
                    await callback_query.answer("❌ Ошибка при генерации изображения", show_alert=True)
            except Exception as e:
                logging.error(f"Ошибка при генерации изображения: {e}", exc_info=True)
                await callback_query.answer("⚠️ Что-то пошло не так. Попробуй позже.", show_alert=True)
            return
        
        # Форматируем расписание
        formatted_timetable = format_timetable_dict(timetable, f"Расписание группы {group_name}", week_number=week_number)
        
        # Проверяем длину сообщения (лимит Telegram - 4096 символов)
        max_length = 4000  # Оставляем запас
        reply_markup = get_group_week_navigation_buttons(group_name, week_number)
        
        # Если сообщение слишком длинное, обрезаем
        if len(formatted_timetable) > max_length:
            formatted_timetable = formatted_timetable[:max_length] + "\n\n... (сообщение обрезано, используйте навигацию по неделям)"
        
        # Редактируем сообщение
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        await callback_query.answer()
    
    except Exception as e:
        logging.error(f"Ошибка при переключении недели группы: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось выполнить действие. Попробуй позже.", show_alert=True)

@dp.callback_query(F.data.startswith("prev_week_") | F.data.startswith("next_week_") | F.data.startswith("current_week_"))
async def process_week_navigation(callback_query: CallbackQuery):
    # Извлекаем смещение недели из callback_data
    callback_data = callback_query.data
    if callback_data.startswith("prev_week_"):
        week_offset = int(callback_data.split("_")[2])
    elif callback_data.startswith("next_week_"):
        week_offset = int(callback_data.split("_")[2])
    elif callback_data.startswith("current_week_"):
        week_offset = 0

    try:
        # Получаем расписание для выбранной недели
        user_id = callback_query.from_user.id
        if user_id not in apis:
            await callback_query.answer("Сначала авторизуйтесь с помощью /login.", show_alert=True)
            return

        timetable = await apis[user_id].get_timetable(week_offset=week_offset)
        
        # Форматируем расписание
        formatted_timetable = format_timetable(timetable)
        
        # Обновляем инлайн-кнопки
        reply_markup = get_week_navigation_buttons(week_offset=week_offset)
        
        # Редактируем сообщение с новым расписанием и кнопками
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        
        # Подтверждаем обработку callback
        await callback_query.answer()
    
    except Exception as e:
        logging.error("Ошибка при переключении недели расписания: %s", e, exc_info=True)
        await callback_query.answer("⚠️ Не удалось переключить неделю. Попробуй позже.", show_alert=True)

@dp.message(Command("timetable"))
async def cmd_timetable(message: types.Message, uid: int = None):
    user_id = uid if uid is not None else message.from_user.id
    if user_id not in apis:  # Проверяем, есть ли api для пользователя
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in apis:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    try:
        # Получаем расписание для текущей недели
        timetable = await apis[user_id].get_timetable(week_offset=0)
        
        # Форматируем расписание
        formatted_timetable = format_timetable(timetable)
        
        # Добавляем инлайн-кнопки
        reply_markup = get_week_navigation_buttons(week_offset=0)
        
        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
    
    except Exception as e:
        logging.error("Ошибка при получении расписания: %s", e, exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")

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

async def all_groups_timetable_with_progress(api):
    """
    Загружает расписание всех групп с отслеживанием прогресса.
    Использует оригинальный метод API, но отслеживает прогресс через глобальную переменную.
    """
    from datetime import datetime
    import aiohttp
    from tqdm.asyncio import tqdm_asyncio
    
    start = datetime.now()
    
    await api.get_schet()
    await api.get_groups()
    
    timetable = {}
    group_items = list(api.groups_id.items())
    total_groups = len(group_items)
    
    # Инициализируем прогресс
    global timetable_progress
    timetable_progress['total'] = total_groups
    timetable_progress['current'] = 0
    timetable_progress['start_time'] = start
    
    connector = aiohttp.TCPConnector(limit=api.limit)
    async with aiohttp.ClientSession(connector=connector, trust_env=True, headers=BROWSER_HEADERS) as session:
        completed = 0
        async def track_progress(coro):
            nonlocal completed
            result = await coro
            completed += 1
            timetable_progress['current'] = min(completed, total_groups)
            return result

        # cabinet.sut.ru нестабилен под нагрузкой: за один проход часть групп
        # отдаётся с ошибкой. Делаем несколько проходов, дозабирая только неудачные.
        pending = list(group_items)
        for pass_num in range(3):
            if not pending:
                break
            tracked_tasks = [track_progress(api.get_timetable(session, '1', group_id))
                             for group_id, _ in pending]
            results = await tqdm_asyncio.gather(*tracked_tasks,
                desc=f'Загрузка групп (проход {pass_num + 1})', unit=' Групп',
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} | {rate_fmt}{postfix}')

            still_failed = []
            for (group_id, group_name), result in zip(pending, results):
                if isinstance(result, str):
                    still_failed.append((group_id, group_name))
                else:
                    timetable[group_name] = result
            pending = still_failed

            if pending and pass_num < 2:
                logging.info('Проход %s завершён, не удалось %s групп — повтор через 20с',
                             pass_num + 1, len(pending))
                await asyncio.sleep(20)

        if pending:
            logging.warning('После всех проходов не загрузилось групп: %s', len(pending))
    
    # Сохраняем в JSON (как в оригинальном методе)
    TimetableBonchAPI.save_to_json(timetable, 'timetable.json')
    
    end = datetime.now()
    logging.info(f'Всего групп получено: {len(timetable)}')
    logging.info(f'Потрачено времени: {(end - start).total_seconds()} секунд')
    
    return timetable

async def send_progress_update(user_id: int, current: int, total: int, start_time):
    """
    Отправляет обновление прогресса пользователю.
    """
    global timetable_progress_users
    if user_id not in timetable_progress_users:
        return
    
    from datetime import datetime
    elapsed = (datetime.now() - start_time).total_seconds()
    percent = (current / total * 100) if total > 0 else 0
    rate = current / elapsed if elapsed > 0 else 0
    remaining = (total - current) / rate if rate > 0 else 0
    
    progress_bar_length = 20
    filled = int(progress_bar_length * current / total) if total > 0 else 0
    bar = '█' * filled + '░' * (progress_bar_length - filled)
    
    message_text = (
        f"📥 Загрузка расписания...\n\n"
        f"Прогресс: {bar}\n"
        f"{current}/{total} групп ({percent:.1f}%)\n"
        f"Скорость: {rate:.1f} групп/сек\n"
        f"Осталось: ~{remaining:.0f} сек"
    )
    
    try:
        msg = timetable_progress_users[user_id]
        await msg.edit_text(message_text)
    except Exception as e:
        logging.error(f"Ошибка при отправке прогресса пользователю {user_id}: {e}")

async def progress_updater():
    """
    Фоновая задача для отправки обновлений прогресса каждые 10 секунд.
    """
    global timetable_progress, timetable_progress_users, timetable_loading
    
    while timetable_loading:
        await asyncio.sleep(10)  # Обновляем каждые 10 секунд
        
        if not timetable_loading:
            break
        
        current = timetable_progress.get('current', 0)
        total = timetable_progress.get('total', 0)
        start_time = timetable_progress.get('start_time')
        
        if start_time and total > 0 and timetable_progress_users:
            # Отправляем обновления всем пользователям, которые ждут
            for user_id in list(timetable_progress_users.keys()):
                try:
                    await send_progress_update(user_id, current, total, start_time)
                except Exception as e:
                    logging.error(f"Ошибка при отправке прогресса пользователю {user_id}: {e}")

async def get_all_groups_timetable(force_reload: bool = False, user_id: int = None, progress_message=None):
    """
    Получает расписание всех групп с кэшированием и отслеживанием прогресса.
    Сначала пытается загрузить из JSON файла, если он существует и не требуется принудительная перезагрузка.
    """
    global all_groups_timetable_cache, timetable_loading, timetable_progress_users
    
    if all_groups_timetable_cache is None or force_reload:
        # Если не требуется принудительная перезагрузка, пытаемся загрузить из JSON
        if not force_reload:
            try:
                timetable_from_json = TimetableBonchAPI.load_from_json('timetable.json')
                if timetable_from_json:
                    all_groups_timetable_cache = timetable_from_json
                    logging.info(f"Расписание загружено из JSON файла: {len(all_groups_timetable_cache)} групп")
                    return all_groups_timetable_cache
            except Exception as e:
                logging.warning(f"Не удалось загрузить расписание из JSON: {e}. Загружаю с сервера...")
        
        if timetable_loading:
            # Если уже идет загрузка, добавляем пользователя в список ожидающих
            if user_id and progress_message:
                timetable_progress_users[user_id] = progress_message
            # Ждем завершения загрузки
            while timetable_loading:
                await asyncio.sleep(1)
            # Удаляем пользователя из списка после завершения
            if user_id and user_id in timetable_progress_users:
                del timetable_progress_users[user_id]
            return all_groups_timetable_cache
        
        timetable_loading = True
        
        # Добавляем пользователя в список для получения прогресса
        if user_id and progress_message:
            timetable_progress_users[user_id] = progress_message
        
        # Запускаем задачу для отправки прогресса
        progress_task = None
        if timetable_progress_users:
            progress_task = asyncio.create_task(progress_updater())
        
        try:
            api = await get_timetable_api()
            logging.info("Загрузка расписания всех групп с сервера...")
            
            all_groups_timetable_cache = await all_groups_timetable_with_progress(api)
            logging.info(f"Расписание всех групп загружено: {len(all_groups_timetable_cache)} групп")
            # Сохранение в JSON уже выполняется в all_groups_timetable_with_progress
            
            # Отправляем финальное сообщение всем пользователям
            for user_id in list(timetable_progress_users.keys()):
                try:
                    msg = timetable_progress_users[user_id]
                    await msg.edit_text(f"✅ Расписание успешно загружено! Загружено {len(all_groups_timetable_cache)} групп.")
                except Exception as e:
                    logging.error(f"Ошибка при отправке финального сообщения пользователю {user_id}: {e}")
            
        except Exception as e:
            logging.error(f"Ошибка при загрузке расписания: {e}", exc_info=True)
            # Отправляем сообщение об ошибке всем пользователям
            for user_id in list(timetable_progress_users.keys()):
                try:
                    msg = timetable_progress_users[user_id]
                    await msg.edit_text("❌ Не удалось загрузить расписание. Попробуй позже.")
                except Exception as err:
                    logging.error(f"Ошибка при отправке сообщения об ошибке пользователю {user_id}: {err}")
            raise
        finally:
            timetable_loading = False
            # Останавливаем задачу прогресса
            if progress_task:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            # Очищаем список пользователей
            timetable_progress_users.clear()
            timetable_progress = {'current': 0, 'total': 0, 'start_time': None}
    
    return all_groups_timetable_cache

@dp.message(Command("teacher_timetable"))
async def cmd_teacher_timetable(message: types.Message, override: str = None):
    """
    Команда для получения расписания преподавателя.
    Использование: /teacher_timetable <Фамилия преподавателя>
    """
    if override is not None:
        teacher_name = override
    else:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Используйте: /teacher_timetable <Фамилия преподавателя>\n\n"
                "Пример: /teacher_timetable Иванов"
            )
            return
        teacher_name = args[1]
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил расписание преподавателя: {teacher_name}")
    
    try:
        # Проверяем наличие кэша
        global all_groups_timetable_cache, timetable_loading
        status_msg = None
        if all_groups_timetable_cache is None:
            if timetable_loading:
                status_msg = await message.answer("⏳ Расписание уже загружается, пожалуйста подождите...")
            else:
                status_msg = await message.answer("⏳ Загружаю расписание всех групп... Это может занять несколько минут.")
            all_timetable = await get_all_groups_timetable(user_id=user_id, progress_message=status_msg)
            # Не удаляем сообщение, так как оно будет обновляться с прогрессом
        else:
            all_timetable = all_groups_timetable_cache
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
        
        # Используем статический метод для фильтрации по преподавателю
        teacher_timetable = TimetableBonchAPI.teacher_timetable(all_timetable, teacher_name)
        
        if not teacher_timetable:
            await message.answer(f"❌ Не найдено занятий для преподавателя: {teacher_name}")
            return
        
        # Определяем текущую неделю (первая неделя с занятиями или текущая)
        weeks = sorted(set(lesson.get('Номер недели', 0) for lesson in teacher_timetable))
        current_week = weeks[0] if weeks else None
        
        formatted_timetable = format_timetable_dict(teacher_timetable, f"Расписание преподавателя: {teacher_name}", week_number=current_week)
        reply_markup = get_teacher_week_navigation_buttons(teacher_name, current_week)
        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
    
    except Exception as e:
        logging.error(f"Ошибка при получении расписания преподавателя: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")

@dp.message(Command("classroom_timetable"))
async def cmd_classroom_timetable(message: types.Message, override: str = None):
    """
    Команда для получения расписания кабинета.
    Использование: /classroom_timetable <Номер кабинета>
    """
    if override is not None:
        classroom_number = override
    else:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Используйте: /classroom_timetable <Номер кабинета>\n\n"
                "Пример: /classroom_timetable 101"
            )
            return
        classroom_number = args[1]
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил расписание кабинета: {classroom_number}")
    
    try:
        # Проверяем наличие кэша
        global all_groups_timetable_cache, timetable_loading
        status_msg = None
        if all_groups_timetable_cache is None:
            if timetable_loading:
                status_msg = await message.answer("⏳ Расписание уже загружается, пожалуйста подождите...")
            else:
                status_msg = await message.answer("⏳ Загружаю расписание всех групп... Это может занять несколько минут.")
            all_timetable = await get_all_groups_timetable(user_id=user_id, progress_message=status_msg)
            # Не удаляем сообщение, так как оно будет обновляться с прогрессом
        else:
            all_timetable = all_groups_timetable_cache
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
        
        # Используем статический метод для фильтрации по кабинету
        classroom_timetable = TimetableBonchAPI.classroom_timetable(all_timetable, classroom_number)
        
        if not classroom_timetable:
            await message.answer(f"❌ Не найдено занятий для кабинета: {classroom_number}")
            return
        
        # Определяем текущую неделю (первая неделя с занятиями или текущая)
        weeks = sorted(set(lesson.get('Номер недели', 0) for lesson in classroom_timetable))
        current_week = weeks[0] if weeks else None
        
        formatted_timetable = format_timetable_dict(classroom_timetable, f"Расписание кабинета: {classroom_number}", week_number=current_week)
        reply_markup = get_classroom_week_navigation_buttons(classroom_number, current_week)
        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
    
    except Exception as e:
        logging.error(f"Ошибка при получении расписания кабинета: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")

@dp.message(Command("teachers"))
async def cmd_teachers(message: types.Message):
    """
    Команда для получения списка преподавателей из расписания.
    """
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил список преподавателей (/teachers)")
    
    try:
        # Проверяем наличие кэша
        global all_groups_timetable_cache, timetable_loading
        status_msg = None
        if all_groups_timetable_cache is None:
            if timetable_loading:
                status_msg = await message.answer("⏳ Расписание уже загружается, пожалуйста подождите...")
            else:
                status_msg = await message.answer("⏳ Загружаю расписание всех групп... Это может занять несколько минут.")
            all_timetable = await get_all_groups_timetable(user_id=user_id, progress_message=status_msg)
            # Не удаляем сообщение, так как оно будет обновляться с прогрессом
        else:
            all_timetable = all_groups_timetable_cache
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
        
        # Извлекаем уникальных преподавателей из расписания
        teachers_set = set()
        for group_name, lessons in all_timetable.items():
            for lesson in lessons:
                teacher = lesson.get('ФИО преподавателя')
                if teacher:
                    # Разделяем преподавателей по точке с запятой, если их несколько
                    for t in teacher.split(';'):
                        teachers_set.add(t.strip())
        
        if not teachers_set:
            await message.answer("❌ Не найдено преподавателей в расписании")
            return
        
        teachers_list = sorted(list(teachers_set))
        logging.info(f"Найдено {len(teachers_list)} преподавателей")
        
        # Формируем список для отправки
        teachers_text = f"👤 Список преподавателей ({len(teachers_list)}):\n\n"
        for teacher in teachers_list[:100]:  # Показываем первые 100
            teachers_text += f"• {teacher}\n"
        
        if len(teachers_list) > 100:
            teachers_text += f"\n... и еще {len(teachers_list) - 100} преподавателей"
        
        await message.answer(teachers_text)
    
    except Exception as e:
        logging.error(f"Ошибка при получении списка преподавателей для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось получить список преподавателей. Попробуй позже.")


@dp.message(Command("send_lk"))
async def cmd_send_lk(message: types.Message, override_text: str = None):
    """
    Команда для отправки сообщения в ЛК.
    Варианты:
      /send_lk <id_в_ЛК> <текст>        — отправка по известному ID
      /send_lk <Фамилия[ И.О.]> <текст> — поиск получателя по ФИО и выбор из списка
    """
    user_id = message.from_user.id

    try:
        # Текст из меню (override_text) либо из самой команды /send_lk
        if override_text is not None:
            text_after_command = override_text.strip()
        else:
            text_after_command = message.text[len("/send_lk"):].strip()
        if not text_after_command:
            await message.answer(
                "Использование:\n"
                "/send_lk <id_в_ЛК> <текст сообщения>\n"
                "или\n"
                "/send_lk <Фамилия[ И.О.]> <текст сообщения>\n\n"
                "Примеры:\n"
                "/send_lk 113714 Привет из Telegram бота!\n"
                "/send_lk Платонов Д.И. Реально работает?"
            )
            return

        # Разбиваем на слова
        words = text_after_command.split()
        
        # Если первое слово — число, это ID
        if words[0].isdigit():
            recipient_raw = words[0]
            text = " ".join(words[1:]).strip()
            if not text:
                await message.answer("Текст сообщения не может быть пустым.")
                return
        else:
            # Ищем границу между ФИО и текстом сообщения
            # ФИО обычно: Фамилия И.О. (1-3 слова)
            # Эвристика: после инициалов (формат "X.X.") следующее слово с заглавной — это начало текста
            recipient_words = []
            text_start_idx = None
            
            def is_initials(word):
                """Проверяет, является ли слово инициалами (формат X.X. или X.X)"""
                if not word:
                    return False
                # Убираем точку в конце, если есть
                word_clean = word.rstrip('.')
                # Проверяем формат: одна буква, точка, одна буква (и опционально точка в конце)
                if len(word_clean) == 3 and word_clean[1] == '.':
                    return word_clean[0].isupper() and word_clean[2].isupper()
                return False
            
            for i, word in enumerate(words):
                # Если слово заканчивается на знак препинания (кроме точки в инициалах) — это начало текста
                if word and word[-1] in "!?," and i > 0:
                    text_start_idx = i
                    break
                
                # Если предыдущее слово было инициалами, а текущее начинается с заглавной — это начало текста
                if i > 0 and is_initials(words[i-1]) and word and word[0].isupper():
                    text_start_idx = i
                    break
                
                # Если уже есть 2+ слова и текущее слово длинное (более 8 символов) — это начало текста
                if i >= 2 and len(word) > 8:
                    text_start_idx = i
                    break
                
                # Если уже есть 3 слова — считаем, что ФИО закончилось
                if i >= 3:
                    text_start_idx = i
                    break
                
                recipient_words.append(word)
            
            # Если не нашли границу, пробуем взять первые 2-3 слова как ФИО, остальное — текст
            if text_start_idx is None:
                if len(words) >= 3:
                    # Если есть 3+ слова, берём первые 2 (фамилия + инициалы), остальное — текст
                    recipient_words = words[:2]
                    text_start_idx = 2
                elif len(words) == 2:
                    # Если только 2 слова, возможно это ФИО без текста или текст без ФИО
                    # Проверяем, является ли второе слово инициалами
                    if is_initials(words[1]):
                        await message.answer(
                            "Не указан текст сообщения.\n\n"
                            "Использование:\n"
                            "/send_lk <id_в_ЛК> <текст сообщения>\n"
                            "или\n"
                            "/send_lk <Фамилия[ И.О.]> <текст сообщения>\n\n"
                            "Примеры:\n"
                            "/send_lk 113714 Привет из Telegram бота!\n"
                            "/send_lk Платонов Д.И. Реально работает?"
                        )
                        return
                    else:
                        # Возможно, это фамилия + текст (без инициалов)
                        recipient_words = words[:1]
                        text_start_idx = 1
                else:
                    # Только одно слово — это либо фамилия без текста, либо ошибка
                    await message.answer(
                        "Не удалось определить ФИО и текст сообщения.\n\n"
                        "Использование:\n"
                        "/send_lk <id_в_ЛК> <текст сообщения>\n"
                        "или\n"
                        "/send_lk <Фамилия[ И.О.]> <текст сообщения>\n\n"
                        "Примеры:\n"
                        "/send_lk 113714 Привет из Telegram бота!\n"
                        "/send_lk Платонов Д.И. Реально работает?"
                    )
                    return
            
            recipient_raw = " ".join(recipient_words)
            text = " ".join(words[text_start_idx:]).strip()
            if not text:
                await message.answer("Текст сообщения не может быть пустым.")
                return

        # Получаем API с авторизацией и cookies
        message_api = await get_message_api(user_id)
        if not message_api:
            await message.answer("❌ Не удалось авторизоваться в ЛК. Выполните /login и попробуйте снова.")
            return

        # Если передан числовой ID — отправляем сразу
        if recipient_raw.isdigit():
            recipient_id = int(recipient_raw)
            status_msg = await message.answer("⏳ Отправляю сообщение в ЛК по ID...")

            ok = await lk_send_message(
                message_api=message_api,
                recipient_id=recipient_id,
                title="",
                message_text=text,
                idinfo=0,
            )

            if ok:
                await status_msg.edit_text(f"✅ Сообщение успешно отправлено в ЛК (id={recipient_id}).")
            else:
                await status_msg.edit_text("❌ Не удалось отправить сообщение в ЛК. Проверьте ID адресата и авторизацию.")
            return

        # Иначе ищем получателя по ФИО через subconto
        query = recipient_raw
        search_msg = await message.answer(f"⏳ Ищу получателя по запросу: {query!r}...")
        results = await lk_search_recipients(message_api, query)

        if not results:
            await search_msg.edit_text(f"❌ Получатели по запросу {query!r} не найдены.")
            return

        # Если ровно один результат — отправляем сразу
        if len(results) == 1:
            recipient_id = results[0]["id"]
            label = results[0]["label"]
            await search_msg.edit_text(f"⏳ Найден получатель: {label}\nОтправляю сообщение...")

            ok = await lk_send_message(
                message_api=message_api,
                recipient_id=recipient_id,
                title="",
                message_text=text,
                idinfo=0,
            )

            if ok:
                await search_msg.edit_text(f"✅ Сообщение успешно отправлено в ЛК получателю: {label}")
            else:
                await search_msg.edit_text(f"❌ Не удалось отправить сообщение в ЛК получателю: {label}")
            return

        # Если несколько результатов — предлагаем выбрать из списка
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        # Ограничим до 10 верхних результатов
        choices = results[:10]

        keyboard = []
        for r in choices:
            rid = r["id"]
            label = r["label"]
            keyboard.append(
                [InlineKeyboardButton(text=label, callback_data=f"lk_send_{rid}")]
            )

        # Сохраняем текст сообщения для последующей отправки после выбора
        for r in choices:
            key = (user_id, r["id"])
            pending_lk_messages[key] = {
                "text": text,
                "title": "",
                "label": r["label"],
            }

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await search_msg.edit_text(
            "🔎 Найдено несколько получателей.\n"
            "Выберите нужного, чтобы отправить сообщение:",
            reply_markup=reply_markup,
        )

    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения в ЛК для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("❌ Не удалось отправить сообщение. Попробуй позже.")


@dp.callback_query(F.data.startswith("lk_send_"))
async def handle_lk_send_callback(callback_query: CallbackQuery):
    """
    Обработчик выбора получателя для отправки сообщения в ЛК.
    """
    user_id = callback_query.from_user.id

    try:
        data = callback_query.data
        recipient_id = int(data.split("_")[-1])
        key = (user_id, recipient_id)

        if key not in pending_lk_messages:
            await callback_query.answer("Сохраненное сообщение не найдено, попробуйте снова через /send_lk.", show_alert=True)
            return

        payload = pending_lk_messages.pop(key)
        text = payload.get("text", "")
        title = payload.get("title", "")
        label = payload.get("label", f"id={recipient_id}")

        message_api = await get_message_api(user_id)
        if not message_api:
            await callback_query.answer("❌ Ошибка авторизации в ЛК. Выполните /login.", show_alert=True)
            return

        await callback_query.answer("⏳ Отправляю сообщение...", show_alert=False)

        ok = await lk_send_message(
            message_api=message_api,
            recipient_id=recipient_id,
            title=title,
            message_text=text,
            idinfo=0,
        )

        if ok:
            await callback_query.message.edit_text(f"✅ Сообщение успешно отправлено в ЛК получателю: {label}")
        else:
            await callback_query.message.edit_text(f"❌ Не удалось отправить сообщение в ЛК получателю: {label}")

    except Exception as e:
        logging.error(f"Ошибка при обработке callback отправки ЛК сообщения для пользователя {user_id}: {e}", exc_info=True)
        await callback_query.answer("⚠️ Что-то пошло не так. Попробуй позже.", show_alert=True)

@dp.message(Command("classrooms"))
async def cmd_classrooms(message: types.Message):
    """
    Команда для получения списка кабинетов из расписания.
    """
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил список кабинетов (/classrooms)")
    
    try:
        # Проверяем наличие кэша
        global all_groups_timetable_cache, timetable_loading
        status_msg = None
        if all_groups_timetable_cache is None:
            if timetable_loading:
                status_msg = await message.answer("⏳ Расписание уже загружается, пожалуйста подождите...")
            else:
                status_msg = await message.answer("⏳ Загружаю расписание всех групп... Это может занять несколько минут.")
            all_timetable = await get_all_groups_timetable(user_id=user_id, progress_message=status_msg)
            # Не удаляем сообщение, так как оно будет обновляться с прогрессом
        else:
            all_timetable = all_groups_timetable_cache
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
        
        # Извлекаем уникальные кабинеты из расписания
        classrooms_set = set()
        for group_name, lessons in all_timetable.items():
            for lesson in lessons:
                room = lesson.get('Номер кабинета')
                if room:
                    classrooms_set.add(room.strip())
        
        if not classrooms_set:
            await message.answer("❌ Не найдено кабинетов в расписании")
            return
        
        classrooms_list = sorted(list(classrooms_set))
        logging.info(f"Найдено {len(classrooms_list)} кабинетов")
        
        # Формируем список для отправки
        classrooms_text = f"🏫 Список кабинетов ({len(classrooms_list)}):\n\n"
        for classroom in classrooms_list[:100]:  # Показываем первые 100
            classrooms_text += f"• {classroom}\n"
        
        if len(classrooms_list) > 100:
            classrooms_text += f"\n... и еще {len(classrooms_list) - 100} кабинетов"
        
        await message.answer(classrooms_text)
    
    except Exception as e:
        logging.error(f"Ошибка при получении списка кабинетов для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось получить список аудиторий. Попробуй позже.")

@dp.message(Command("groups"))
async def cmd_groups(message: types.Message):
    """
    Команда для получения списка групп.
    """
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил список групп (/groups)")
    
    try:
        api = await get_timetable_api()
        
        if not hasattr(api, 'groups_id') or not api.groups_id:
            logging.error(f"Не удалось получить список групп для пользователя {user_id}")
            await message.answer("❌ Не удалось получить список групп. Попробуйте позже.")
            return
        
        logging.info(f"Формирование списка групп для пользователя {user_id}. Всего групп: {len(api.groups_id)}")
        groups_list = f"👥 Список групп ({len(api.groups_id)}):\n\n"
        for group_id, group_name in list(api.groups_id.items())[:100]:  # Показываем первые 100
            groups_list += f"• {group_name} (ID: {group_id})\n"
        
        if len(api.groups_id) > 100:
            groups_list += f"\n... и еще {len(api.groups_id) - 100} групп"
        
        groups_list += "\n\n💡 Используйте /group_timetable <название или ID> для получения расписания"
        
        await message.answer(groups_list)
        logging.info(f"Список групп успешно отправлен пользователю {user_id}")
    
    except Exception as e:
        logging.error(f"Ошибка при получении списка групп для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось получить список групп. Попробуй позже.")

@dp.message(Command("group_timetable"))
async def cmd_group_timetable(message: types.Message, override: str = None):
    """
    Команда для получения расписания группы.
    Использование: /group_timetable <ID_группы или название группы>
    Использует расписание из загруженного кэша всех групп.
    """
    if override is not None:
        group_input = override
    else:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Используйте: /group_timetable <ID_группы или название группы>\n\n"
                "Пример: /group_timetable ИКПИ-22\n"
                "Или: /group_timetable 12345\n\n"
                "Для получения списка групп используйте: /groups"
            )
            return
        group_input = args[1]
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил расписание группы: {group_input}")
    
    try:
        # Проверяем наличие кэша расписания всех групп
        global all_groups_timetable_cache, timetable_loading
        status_msg = None
        if all_groups_timetable_cache is None:
            if timetable_loading:
                status_msg = await message.answer("⏳ Расписание уже загружается, пожалуйста подождите...")
            else:
                status_msg = await message.answer("⏳ Загружаю расписание всех групп... Это может занять несколько минут.")
            all_timetable = await get_all_groups_timetable(user_id=user_id, progress_message=status_msg)
            # Не удаляем сообщение, так как оно будет обновляться с прогрессом
        else:
            all_timetable = all_groups_timetable_cache
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
        
        api = await get_timetable_api()
        
        # Пытаемся найти группу по ID или названию
        group_id = None
        group_name = None
        
        # Сначала проверяем, является ли ввод ID
        if group_input.isdigit():
            if hasattr(api, 'groups_id') and group_input in api.groups_id:
                group_id = group_input
                group_name = api.groups_id[group_id]
        else:
            # Ищем по названию группы
            if hasattr(api, 'groups_id'):
                for gid, gname in api.groups_id.items():
                    if group_input.lower() in gname.lower():
                        group_id = gid
                        group_name = gname
                        break
        
        if not group_id or not group_name:
            await message.answer(f"❌ Группа '{group_input}' не найдена. Используйте /groups для просмотра списка групп.")
            return
        
        # Получаем расписание группы из загруженного кэша
        if group_name not in all_timetable:
            await message.answer(f"❌ Расписание для группы '{group_name}' не найдено в загруженных данных.")
            return
        
        timetable = all_timetable[group_name]
        
        if isinstance(timetable, str):
            logging.warning(f"Ошибка при получении расписания группы {group_id} для пользователя {user_id}: {timetable}")
            await message.answer(f"❌ {timetable}")
            return
        
        if not timetable:
            await message.answer(f"❌ Расписание для группы '{group_name}' пусто.")
            return
        
        # Определяем текущую неделю (первая неделя с занятиями или текущая)
        weeks = sorted(set(lesson.get('Номер недели', 0) for lesson in timetable))
        current_week = weeks[0] if weeks else None
        
        logging.info(f"Расписание группы {group_id} ({group_name}) успешно получено для пользователя {user_id}. Занятий: {len(timetable)}")
        
        # Форматируем расписание для текущей недели
        formatted_timetable = format_timetable_dict(timetable, f"Расписание группы {group_name}", week_number=current_week)
        
        # Проверяем длину сообщения (лимит Telegram - 4096 символов)
        max_length = 4000  # Оставляем запас для форматирования
        reply_markup = get_group_week_navigation_buttons(group_name, current_week)
        
        # Если сообщение слишком длинное, разбиваем на части
        if len(formatted_timetable) > max_length:
            # Пытаемся разбить по дням
            parts = formatted_timetable.split("----------------------")
            if len(parts) > 1:
                current_part = parts[0]  # Заголовок
                for part in parts[1:]:
                    if len(current_part + "----------------------" + part) > max_length:
                        # Отправляем текущую часть
                        await message.answer(current_part, parse_mode="Markdown", reply_markup=reply_markup if current_part == parts[0] else None)
                        current_part = "----------------------" + part
                    else:
                        current_part += "----------------------" + part
                # Отправляем последнюю часть
                if current_part:
                    await message.answer(current_part, parse_mode="Markdown")
            else:
                # Если не удалось разбить, просто обрезаем
                formatted_timetable = formatted_timetable[:max_length] + "\n\n... (сообщение обрезано, используйте навигацию по неделям)"
                await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
    
    except Exception as e:
        logging.error(f"Ошибка при получении расписания группы {group_input} для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")

@dp.message(Command("reload_timetable"))
async def cmd_reload_timetable(message: types.Message):
    """
    Команда для перезагрузки расписания всех групп.
    """
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил перезагрузку расписания")
    
    try:
        status_msg = await message.answer("⏳ Перезагружаю расписание всех групп... Это может занять некоторое время.")
        all_timetable = await get_all_groups_timetable(force_reload=True, user_id=user_id, progress_message=status_msg)
        # Финальное сообщение уже отправлено в get_all_groups_timetable, но обновим его
        try:
            await status_msg.edit_text(f"✅ Расписание успешно перезагружено! Загружено {len(all_timetable)} групп.")
        except:
            await message.answer(f"✅ Расписание успешно перезагружено! Загружено {len(all_timetable)} групп.")
    except Exception as e:
        logging.error(f"Ошибка при перезагрузке расписания для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("❌ Не удалось перезагрузить расписание. Попробуй позже.")

@dp.message(Command("messages"))
async def cmd_messages(message: types.Message, uid: int = None):
    """
    Команда для просмотра входящих сообщений.
    """
    user_id = uid if uid is not None else message.from_user.id
    logging.info(f"Пользователь {user_id} запросил просмотр сообщений")
    
    try:
        # Получаем API для работы с сообщениями
        message_api = await get_message_api(user_id)
        if not message_api:
            await message.answer("❌ Не удалось авторизоваться. Пожалуйста, выполните /login для авторизации.")
            return
        
        # Получаем список сообщений
        status_msg = await message.answer("⏳ Загружаю сообщения...")
        messages = await message_api.get_messages()
        
        if not messages:
            # Проверяем, может быть проблема с авторизацией
            if not hasattr(message_api, 'cookies') or not message_api.cookies:
                await status_msg.edit_text("❌ Ошибка авторизации. Пожалуйста, выполните /login еще раз.")
            else:
                await status_msg.edit_text("📭 У вас нет входящих сообщений.\n\n💡 Если сообщения должны быть, проверьте авторизацию через /login")
            return
        
        # Сохраняем состояние для навигации
        message_states[user_id] = {
            'messages': messages,
            'current_index': 0
        }
        
        # Отображаем первое сообщение
        await show_message_list(user_id, message.chat.id, 0)
        
    except Exception as e:
        logging.error(f"Ошибка при получении сообщений для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("❌ Не удалось загрузить сообщения. Попробуй позже.")

async def show_message_list(user_id: int, chat_id: int, index: int):
    """
    Отображает список сообщений с навигацией.
    """
    if user_id not in message_states:
        return
    
    messages = message_states[user_id]['messages']
    if not messages or index < 0 or index >= len(messages):
        return
    
    msg = messages[index]
    
    # Формируем текст сообщения
    unread_marker = "🔴" if msg.get('is_unread', False) else ""
    files_marker = "📎" if msg.get('has_files', False) else ""
    date = msg.get('date', '')[:10] if msg.get('date') else ''
    sender = msg.get('sender', 'Неизвестно')
    if sender and '(' in sender:
        sender = sender.split('(')[0].strip()
    
    title = msg.get('title', 'Без названия')
    if len(title) > 100:
        title = title[:97] + '...'
    
    text = f"{unread_marker} *Сообщение {index + 1} из {len(messages)}*\n\n"
    text += f"📅 *Дата:* {date}\n"
    text += f"👤 *Отправитель:* {sender}\n"
    text += f"📋 *Тема:* {title}\n"
    if files_marker:
        text += f"{files_marker} *Есть файлы*\n"
    
    # Создаем клавиатуру для навигации
    keyboard = []
    row = []
    
    if index > 0:
        row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"msg_prev_{index}"))
    if index < len(messages) - 1:
        row.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"msg_next_{index}"))
    
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton(text="📖 Открыть сообщение", callback_data=f"msg_open_{msg['id']}")])
    keyboard.append([InlineKeyboardButton(text="🔄 Обновить список", callback_data="msg_refresh")])
    
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Ошибка при отправке списка сообщений: {e}")

@dp.callback_query(F.data.startswith("msg_"))
async def handle_message_callback(callback_query: CallbackQuery):
    """
    Обработчик callback для навигации по сообщениям.
    """
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    try:
        if data.startswith("msg_prev_"):
            # Переход к предыдущему сообщению
            index = int(data.split("_")[-1]) - 1
            if user_id in message_states and index >= 0:
                message_states[user_id]['current_index'] = index
                await callback_query.answer()
                await callback_query.message.delete()
                await show_message_list(user_id, callback_query.message.chat.id, index)
            else:
                await callback_query.answer("Это первое сообщение", show_alert=True)
        
        elif data.startswith("msg_next_"):
            # Переход к следующему сообщению
            index = int(data.split("_")[-1]) + 1
            if user_id in message_states and index < len(message_states[user_id]['messages']):
                message_states[user_id]['current_index'] = index
                await callback_query.answer()
                await callback_query.message.delete()
                await show_message_list(user_id, callback_query.message.chat.id, index)
            else:
                await callback_query.answer("Это последнее сообщение", show_alert=True)
        
        elif data.startswith("msg_open_"):
            # Открытие конкретного сообщения
            message_id = data.split("_")[-1]
            await callback_query.answer("⏳ Загружаю сообщение...")
            
            message_api = await get_message_api(user_id)
            if not message_api:
                await callback_query.message.answer("❌ Ошибка авторизации")
                return
            
            message_data = await message_api.get_message(message_id)
            
            if not message_data:
                await callback_query.message.answer("❌ Не удалось загрузить сообщение")
                return
            
            # Находим информацию о сообщении из списка
            msg_info = None
            if user_id in message_states:
                for msg in message_states[user_id]['messages']:
                    if msg['id'] == message_id:
                        msg_info = msg
                        break
            
            # Формируем текст сообщения
            title = message_data.get("name", msg_info.get("title", "Без названия") if msg_info else "Без названия")
            annotation = message_data.get("annotation", "Нет текста")
            
            # Декодируем HTML и удаляем теги
            if annotation:
                annotation = html.unescape(annotation)
                annotation = re.sub(r'<[^>]+>', '', annotation)
            
            text = f"📋 *{title}*\n\n"
            if msg_info:
                text += f"📅 *Дата:* {msg_info.get('date', 'Не указана')}\n"
                text += f"👤 *Отправитель:* {msg_info.get('sender', 'Неизвестно')}\n"
                text += f"━━━━━━━━━━━━━━━━━━━━\n\n"
            
            text += f"{annotation}\n\n"
            text += f"━━━━━━━━━━━━━━━━━━━━\n"
            
            if msg_info and msg_info.get("files"):
                text += f"\n📎 *Файлы:*\n"
                for file_info in msg_info["files"]:
                    file_name = file_info.get("name", "Файл")
                    file_url = file_info.get("url", "")
                    if file_url:
                        # Используем Markdown формат для ссылки: [текст](url)
                        # Экранируем специальные символы в URL и имени файла для Markdown
                        file_name_escaped = file_name.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]")
                        text += f"  • [{file_name_escaped}]({file_url})\n"
                    else:
                        text += f"  • {file_name}\n"
            
            text += f"\n🆔 ID: `{message_id}`"
            
            keyboard = [[InlineKeyboardButton(text="🔙 Назад к списку", callback_data="msg_back_to_list")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            
            await callback_query.message.answer(text, parse_mode="Markdown", reply_markup=reply_markup)
            await callback_query.message.delete()
        
        elif data == "msg_refresh":
            # Обновление списка сообщений
            await callback_query.answer("🔄 Обновляю список...")
            
            message_api = await get_message_api(user_id)
            if not message_api:
                await callback_query.message.answer("❌ Ошибка авторизации")
                return
            
            messages = await message_api.get_messages()
            if not messages:
                await callback_query.message.answer("📭 У вас нет входящих сообщений.")
                await callback_query.message.delete()
                return
            
            message_states[user_id] = {
                'messages': messages,
                'current_index': 0
            }
            
            await callback_query.message.delete()
            await show_message_list(user_id, callback_query.message.chat.id, 0)
        
        elif data == "msg_back_to_list":
            # Возврат к списку сообщений
            if user_id in message_states:
                current_index = message_states[user_id].get('current_index', 0)
                await callback_query.message.delete()
                await show_message_list(user_id, callback_query.message.chat.id, current_index)
            else:
                await callback_query.message.answer("❌ Состояние навигации потеряно. Используйте /messages для обновления.")
    
    except Exception as e:
        logging.error(f"Ошибка при обработке callback сообщений для пользователя {user_id}: {e}", exc_info=True)
        await callback_query.answer("⚠️ Что-то пошло не так. Попробуй позже.", show_alert=True)

async def auto_login_user(user_id):
    """
    Автоматически авторизует пользователя, если он есть в базе данных.
    Возвращает True, если авторизация успешна, False в противном случае.
    """
    cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if not result:
        logging.info(f"Пользователь {user_id} не найден в базе данных.")
        return False
    
    email, password = result
    password = decrypt_password(password)
    logging.info(f"Попытка автоматической авторизации для пользователя {user_id} (email: {email})")
    try:
        apis[user_id] = DebuggableBonchAPI()
        ok = await apis[user_id].login(email, password)
        if not ok:
            raise ValueError("auto_login_failed")

        controllers[user_id] = LessonController(apis[user_id], bot, user_id)  # Передаем bot и user_id

        logging.info("✅ Пользователь %s успешно автоматически авторизован.", user_id)
        return True
    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ Ошибка автоматической авторизации для пользователя {user_id} (email: {email}): {error_msg}", exc_info=True)
        # Удаляем частично созданные объекты при ошибке
        if user_id in apis:
            del apis[user_id]
        if user_id in controllers:
            del controllers[user_id]
        return False

async def auto_start_lesson(user_id):
    """
    Автоматически запускает автокликалку для пользователя при старте бота —
    но только если пользователь не выключил её вручную (autoclick_enabled).
    """
    if not get_autoclick_enabled(user_id):
        logging.info(
            "Автокликалка для пользователя %s выключена вручную — не запускаем при старте бота.",
            user_id,
        )
        return
    if user_id in controllers:  # Проверяем, есть ли контроллер для пользователя
        controller = controllers[user_id]
        if not controller.is_running:  # Если автокликалка не запущена, запускаем её
            controller.task = asyncio.create_task(controller.start_lesson())
            logging.info(f"Автокликалка автоматически запущена для пользователя {user_id}.")

async def get_message_api(user_id: int) -> Optional[TimetableBonchAPI]:
    """
    Получает экземпляр TimetableBonchAPI для работы с сообщениями пользователя.
    Использует cookies из существующего авторизованного API пользователя.
    """
    # Проверяем, есть ли уже авторизованный API для пользователя
    if user_id not in apis:
        # Пытаемся автоматически авторизовать
        success = await auto_login_user(user_id)
        if not success or user_id not in apis:
            logging.warning(f"Не удалось получить API для пользователя {user_id}")
            return None
    
    # Используем cookies из существующего API
    existing_api = apis[user_id]
    if not hasattr(existing_api, 'cookies') or not existing_api.cookies:
        logging.warning(f"У пользователя {user_id} нет cookies в API")
        # Попробуем переавторизоваться
        cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
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

# ==========================================================================
#  Пользовательский интерфейс: меню, онбординг, пошаговые диалоги
# ==========================================================================

BTN_SCHEDULE = "📅 Расписание"
BTN_AUTOCLICK = "✅ Автоотметка"
BTN_MESSAGES = "✉️ Сообщения"
BTN_PROFILE = "👤 Профиль"
BTN_HELP = "❓ Помощь"


class UIStates(StatesGroup):
    login_email = State()
    login_password = State()
    ask_group = State()
    ask_teacher = State()
    ask_classroom = State()
    write_recipient = State()
    write_pick = State()
    write_title = State()
    write_text = State()


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SCHEDULE), KeyboardButton(text=BTN_AUTOCLICK)],
            [KeyboardButton(text=BTN_MESSAGES), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери раздел в меню снизу",
    )


HELP_TEXT = (
    "❓ <b>Помощь — SatanBonchBot</b>\n\n"
    "Я помощник студента СПбГУТ. Разделы — кнопками в меню снизу.\n\n"
    "📅 <b>Расписание</b>\n"
    "Расписание групп, преподавателей и аудиторий. Доступно без входа в ЛК. "
    "После входа добавляется «Моё расписание».\n\n"
    "✅ <b>Автоотметка</b>\n"
    "Бот сам отмечает тебя на парах в личном кабинете, пока идёт занятие. "
    "Нужен вход в ЛК. Включается и выключается кнопкой.\n\n"
    "✉️ <b>Сообщения</b>\n"
    "Чтение входящих и отправка сообщений через ЛК. Нужен вход в ЛК.\n\n"
    "👤 <b>Профиль</b>\n"
    "Твой email, статус входа в ЛК, настройки уведомлений, "
    "повторный вход и выход.\n\n"
    "🔑 <b>Вход в личный кабинет</b>\n"
    "Кнопка «🔑 Войти в ЛК» либо команда одной строкой:\n"
    "<code>/login email пароль</code>\n"
    "Пароль хранится в зашифрованном виде.\n\n"
    "🔔 <b>Уведомления</b>\n"
    "Бот предупреждает о начале пар. Включение и время напоминания "
    "настраиваются в разделе «👤 Профиль» → «🔔 Уведомления».\n\n"
    "<b>Команды:</b>\n"
    "/start — главное меню\n"
    "/login — войти в ЛК\n"
    "/help — эта справка\n"
    "/cancel — отменить текущее действие"
)


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="m:cancel")]]
    )


def login_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔑 Войти в ЛК", callback_data="m:login")]]
    )


def schedule_menu_kb(logged_in: bool) -> InlineKeyboardMarkup:
    rows = []
    if logged_in:
        rows.append([InlineKeyboardButton(text="🎓 Моё расписание", callback_data="m:sched:my")])
    rows.append([InlineKeyboardButton(text="👥 Группа", callback_data="m:sched:group")])
    rows.append([InlineKeyboardButton(text="🧑‍🏫 Преподаватель", callback_data="m:sched:teacher")])
    rows.append([InlineKeyboardButton(text="🚪 Аудитория", callback_data="m:sched:room")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def autoclick_menu_kb(is_running: bool) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton(text="⏹ Выключить", callback_data="m:auto:stop")
        if is_running
        else InlineKeyboardButton(text="▶️ Включить", callback_data="m:auto:start")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [toggle],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="m:auto:refresh")],
        [InlineKeyboardButton(text="🔔 Проверить уведомления", callback_data="m:auto:notify")],
    ])


def messages_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Входящие", callback_data="m:msg:inbox")],
        [InlineKeyboardButton(text="✏️ Написать", callback_data="m:msg:write")],
    ])


def profile_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="m:profile:notify")],
        [InlineKeyboardButton(text="🔄 Войти заново", callback_data="m:profile:relogin")],
        [InlineKeyboardButton(text="🚪 Выйти", callback_data="m:profile:logout")],
    ])


def notify_settings_text(enabled: bool, minutes: int) -> str:
    if enabled:
        return (
            "🔔 Настройки уведомлений\n\n"
            f"Предупреждаю о начале пары за {minutes} мин.\n"
            "Выбери, за сколько минут предупреждать, или выключи уведомления."
        )
    return (
        "🔕 Настройки уведомлений\n\n"
        "Уведомления о начале пар выключены."
    )


def notify_settings_kb(enabled: bool, minutes: int) -> InlineKeyboardMarkup:
    toggle_text = "🔔 Уведомления включены" if enabled else "🔕 Уведомления выключены"
    rows = [[InlineKeyboardButton(text=toggle_text, callback_data="m:notify:toggle")]]
    if enabled:
        rows.append([
            InlineKeyboardButton(
                text=f"{opt} мин" + (" ✅" if opt == minutes else ""),
                callback_data=f"m:notify:min:{opt}",
            )
            for opt in NOTIFY_MINUTE_OPTIONS
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def is_registered(user_id: int) -> bool:
    cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
    return cursor.fetchone() is not None


# --- Настройки уведомлений о парах -------------------------------------------
NOTIFY_DEFAULT_MINUTES = 10
NOTIFY_MINUTE_OPTIONS = (5, 10, 15, 30)


def get_notify_settings(user_id: int) -> tuple[bool, int]:
    """Возвращает (уведомления включены, за сколько минут предупреждать о паре)."""
    cursor.execute(
        'SELECT notify_enabled, notify_minutes FROM users WHERE user_id = ?',
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return True, NOTIFY_DEFAULT_MINUTES
    enabled_raw, minutes_raw = row
    enabled = True if enabled_raw is None else bool(enabled_raw)
    minutes = int(minutes_raw) if minutes_raw else NOTIFY_DEFAULT_MINUTES
    return enabled, minutes


def set_notify_enabled(user_id: int, enabled: bool) -> None:
    with conn:
        cursor.execute(
            'UPDATE users SET notify_enabled = ? WHERE user_id = ?',
            (1 if enabled else 0, user_id),
        )


def set_notify_minutes(user_id: int, minutes: int) -> None:
    with conn:
        cursor.execute(
            'UPDATE users SET notify_minutes = ? WHERE user_id = ?',
            (minutes, user_id),
        )


def get_autoclick_enabled(user_id: int) -> bool:
    """
    Включена ли автоотметка пользователем. Учитывается при автозапуске
    автокликалки на старте бота: выключил вручную — не запускаем снова.
    """
    cursor.execute('SELECT autoclick_enabled FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row or row[0] is None:
        return True
    return bool(row[0])


def set_autoclick_enabled(user_id: int, enabled: bool) -> None:
    with conn:
        cursor.execute(
            'UPDATE users SET autoclick_enabled = ? WHERE user_id = ?',
            (1 if enabled else 0, user_id),
        )


async def perform_login(user_id: int, email: str, password: str) -> bool:
    """Входит в ЛК и сохраняет данные в БД только при успешном входе."""
    try:
        api = DebuggableBonchAPI()
        ok = await api.login(email, password)
        if not ok:
            return False
        apis[user_id] = api
        controllers[user_id] = LessonController(api, bot, user_id)
        cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        existing = cursor.fetchone()
        encrypted_password = encrypt_password(password)
        with conn:
            if existing:
                cursor.execute('UPDATE users SET email = ?, password = ? WHERE user_id = ?',
                               (email, encrypted_password, user_id))
            else:
                cursor.execute('INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)',
                               (user_id, email, encrypted_password))
        return True
    except Exception as e:
        logging.error("perform_login: ошибка для %s: %s", user_id, e, exc_info=True)
        return False


async def send_autoclick_panel(user_id: int, chat_id: int):
    """Показывает панель автоотметки со статусом и кнопками."""
    if user_id not in controllers:
        await auto_login_user(user_id)
    if user_id not in controllers:
        await bot.send_message(
            chat_id,
            "Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return
    controller = controllers[user_id]
    status_text = await controller.get_status()
    await bot.send_message(
        chat_id,
        f"✅ Автоотметка\n\n{status_text}",
        reply_markup=autoclick_menu_kb(controller.is_running),
    )


# --- Кнопки главного меню (reply-клавиатура) ---

@dp.message(F.text == BTN_SCHEDULE)
async def menu_schedule(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📅 Чьё расписание показать?",
        reply_markup=schedule_menu_kb(is_registered(message.from_user.id)),
    )


@dp.message(F.text == BTN_AUTOCLICK)
async def menu_autoclick(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if not is_registered(user_id):
        await message.answer(
            "✅ Автоотметка сама отмечает тебя на парах в ЛК.\n"
            "Чтобы включить — войди в личный кабинет.",
            reply_markup=login_prompt_kb(),
        )
        return
    await send_autoclick_panel(user_id, message.chat.id)


@dp.message(F.text == BTN_MESSAGES)
async def menu_messages(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_registered(message.from_user.id):
        await message.answer(
            "✉️ Здесь можно читать и отправлять сообщения в ЛК.\n"
            "Для этого нужно войти в личный кабинет.",
            reply_markup=login_prompt_kb(),
        )
        return
    await message.answer("✉️ Сообщения личного кабинета:", reply_markup=messages_menu_kb())


@dp.message(F.text == BTN_PROFILE)
async def menu_profile(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if not is_registered(user_id):
        await message.answer(
            "👤 Ты ещё не вошёл в личный кабинет СПбГУТ.",
            reply_markup=login_prompt_kb(),
        )
        return
    cursor.execute('SELECT email FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    email = row[0] if row else "—"
    active = "🟢 активен" if user_id in apis else "🟡 восстановится при первом действии"
    notify_enabled, notify_minutes = get_notify_settings(user_id)
    notify_line = (
        f"🔔 Уведомления: за {notify_minutes} мин до пары"
        if notify_enabled
        else "🔕 Уведомления: выключены"
    )
    await message.answer(
        f"👤 Профиль\n\n📧 Email: {email}\n🔑 Вход в ЛК: {active}\n{notify_line}",
        reply_markup=profile_menu_kb(),
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=main_menu_kb())


@dp.message(F.text == BTN_HELP)
async def menu_help(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, parse_mode="HTML")


# --- /cancel и отмена диалога ---

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    had_state = await state.get_state()
    await state.clear()
    await message.answer(
        "Окей, отменил." if had_state else "Сейчас нечего отменять.",
        reply_markup=main_menu_kb(),
    )


@dp.callback_query(F.data == "m:cancel")
async def cb_cancel(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.answer("Отменено")
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback_query.message.answer("Окей. Меню — снизу 👇", reply_markup=main_menu_kb())


# --- Вход в ЛК ---

@dp.callback_query(F.data == "m:login")
async def cb_login(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.login_email)
    await callback_query.message.answer(
        "🔑 Вход в личный кабинет СПбГУТ.\n\nВведи свой email (логин от ЛК):",
        reply_markup=cancel_kb(),
    )


@dp.message(UIStates.login_email)
async def fsm_login_email(message: types.Message, state: FSMContext):
    email = (message.text or "").strip()
    if not EMAIL_RE.match(email):
        await message.answer(
            "Это не похоже на email. Введи корректный адрес, например ivan@mail.ru:",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(email=email)
    await state.set_state(UIStates.login_password)
    await message.answer("Принято. Теперь введи пароль от ЛК:", reply_markup=cancel_kb())


@dp.message(UIStates.login_password)
async def fsm_login_password(message: types.Message, state: FSMContext):
    password = message.text or ""
    data = await state.get_data()
    email = data.get("email", "")
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    retry_after = check_login_rate_limit(message.from_user.id)
    if retry_after:
        await message.answer(
            f"⏳ Слишком много попыток входа. Попробуй снова через {format_retry_after(retry_after)}.",
            reply_markup=login_prompt_kb(),
        )
        return
    status = await message.answer("⏳ Вхожу в ЛК...")
    ok = await perform_login(message.from_user.id, email, password)
    if ok:
        try:
            await status.edit_text("✅ Готово! Ты вошёл в личный кабинет.")
        except Exception:
            pass
        await message.answer("Теперь доступны все разделы 👇", reply_markup=main_menu_kb())
    else:
        try:
            await status.edit_text("❌ Не удалось войти. Проверь email и пароль.")
        except Exception:
            pass
        await message.answer("Попробовать ещё раз?", reply_markup=login_prompt_kb())


# --- Расписание ---

@dp.callback_query(F.data == "m:sched:my")
async def cb_sched_my(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    if not is_registered(user_id):
        await callback_query.answer()
        await callback_query.message.answer("Сначала войди в ЛК.", reply_markup=login_prompt_kb())
        return
    await callback_query.answer("Загружаю...")
    await cmd_timetable(callback_query.message, uid=user_id)


@dp.callback_query(F.data == "m:sched:group")
async def cb_sched_group(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_group)
    await callback_query.message.answer(
        "👥 Введи название или ID группы (например: ИКВТ-21):",
        reply_markup=cancel_kb(),
    )


@dp.callback_query(F.data == "m:sched:teacher")
async def cb_sched_teacher(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_teacher)
    await callback_query.message.answer(
        "🧑‍🏫 Введи фамилию преподавателя (например: Иванов):",
        reply_markup=cancel_kb(),
    )


@dp.callback_query(F.data == "m:sched:room")
async def cb_sched_room(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_classroom)
    await callback_query.message.answer(
        "🚪 Введи номер аудитории (например: 401):",
        reply_markup=cancel_kb(),
    )


@dp.message(UIStates.ask_group)
async def fsm_ask_group(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_group_timetable(message, override=(message.text or "").strip())


@dp.message(UIStates.ask_teacher)
async def fsm_ask_teacher(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_teacher_timetable(message, override=(message.text or "").strip())


@dp.message(UIStates.ask_classroom)
async def fsm_ask_classroom(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_classroom_timetable(message, override=(message.text or "").strip())


# --- Автоотметка ---

@dp.callback_query(F.data.startswith("m:auto:"))
async def cb_autoclick(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    action = callback_query.data.split(":")[2]

    if user_id not in controllers:
        await auto_login_user(user_id)
    if user_id not in controllers:
        await callback_query.answer()
        await callback_query.message.answer(
            "Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return

    controller = controllers[user_id]

    if action == "notify":
        await callback_query.answer()
        await cmd_test_notify(callback_query.message, uid=user_id)
        return

    if action == "start":
        if controller.is_running:
            await callback_query.answer("Уже включена")
        else:
            controller.task = asyncio.create_task(controller.start_lesson())
            await callback_query.answer("Включил ✅")
        set_autoclick_enabled(user_id, True)
        running = True
    elif action == "stop":
        if controller.is_running:
            await controller.stop_lesson(user_id)
            await callback_query.answer("Выключил ⏹")
        else:
            await callback_query.answer("Уже выключена")
        set_autoclick_enabled(user_id, False)
        running = False
    else:
        await callback_query.answer("Обновил")
        running = controller.is_running

    status_text = await controller.get_status()
    panel = f"✅ Автоотметка\n\n{status_text}"
    try:
        await callback_query.message.edit_text(panel, reply_markup=autoclick_menu_kb(running))
    except Exception:
        await callback_query.message.answer(panel, reply_markup=autoclick_menu_kb(running))


# --- Сообщения ЛК ---

@dp.callback_query(F.data == "m:msg:inbox")
async def cb_msg_inbox(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    if not is_registered(user_id):
        await callback_query.answer()
        await callback_query.message.answer("Сначала войди в ЛК.", reply_markup=login_prompt_kb())
        return
    await callback_query.answer("Загружаю...")
    await cmd_messages(callback_query.message, uid=user_id)


@dp.callback_query(F.data == "m:msg:write")
async def cb_msg_write(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    if not is_registered(user_id):
        await callback_query.answer()
        await callback_query.message.answer("Сначала войди в ЛК.", reply_markup=login_prompt_kb())
        return
    await callback_query.answer()
    await state.set_state(UIStates.write_recipient)
    await callback_query.message.answer(
        "✏️ Кому отправить?\n\nВведи ID получателя в ЛК или его фамилию (можно с инициалами):",
        reply_markup=cancel_kb(),
    )


def title_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Без темы", callback_data="mw:notitle")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="m:cancel")],
    ])


RECIPIENTS_PER_PAGE = 8


def recipients_page_kb(results: list, page: int) -> InlineKeyboardMarkup:
    total = len(results)
    pages = max(1, (total + RECIPIENTS_PER_PAGE - 1) // RECIPIENTS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    start = page * RECIPIENTS_PER_PAGE
    rows = [
        [InlineKeyboardButton(text=results[i]["label"], callback_data=f"mw:pick:{i}")]
        for i in range(start, min(start + RECIPIENTS_PER_PAGE, total))
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"mw:page:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="mw:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"mw:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="m:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def start_recipient_pick(target_message: types.Message, user_id: int, query: str, state: FSMContext):
    """Ищет получателя: ID или единственный — сразу к тексту, несколько — список с прокруткой."""
    message_api = await get_message_api(user_id)
    if not message_api:
        await state.clear()
        await target_message.answer(
            "❌ Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return

    if query.isdigit():
        await state.update_data(recipient_id=int(query), recipient_label=f"id={query}")
        await state.set_state(UIStates.write_title)
        await target_message.answer(
            f"Получатель: id={query}\n\nВведи тему сообщения:",
            reply_markup=title_kb(),
        )
        return

    status = await target_message.answer(f"⏳ Ищу получателя «{query}»...")
    results = await lk_search_recipients(message_api, query)

    if not results:
        await state.set_state(UIStates.write_recipient)
        await status.edit_text(
            f"❌ Не нашёл получателя «{query}».\n"
            "Введи фамилию ещё раз (без инициалов) или числовой ID:",
            reply_markup=cancel_kb(),
        )
        return

    if len(results) == 1:
        r = results[0]
        await state.update_data(recipient_id=r["id"], recipient_label=r["label"])
        await state.set_state(UIStates.write_title)
        await status.edit_text(
            f"Получатель: {r['label']}\n\nВведи тему сообщения:",
            reply_markup=title_kb(),
        )
        return

    await state.update_data(results=results)
    await state.set_state(UIStates.write_pick)
    await status.edit_text(
        f"🔎 Нашёл {len(results)} получателей по запросу «{query}».\n"
        "Выбери нужного (или введи фамилию точнее):",
        reply_markup=recipients_page_kb(results, 0),
    )


@dp.message(UIStates.write_recipient)
async def fsm_write_recipient(message: types.Message, state: FSMContext):
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введи ID или фамилию получателя:", reply_markup=cancel_kb())
        return
    await start_recipient_pick(message, message.from_user.id, query, state)


@dp.message(UIStates.write_pick)
async def fsm_write_pick_refine(message: types.Message, state: FSMContext):
    query = (message.text or "").strip()
    if not query:
        return
    await start_recipient_pick(message, message.from_user.id, query, state)


@dp.callback_query(F.data == "mw:noop")
async def cb_write_noop(callback_query: CallbackQuery):
    await callback_query.answer()


@dp.callback_query(F.data.startswith("mw:page:"))
async def cb_write_page(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    page = int(callback_query.data.split(":")[2])
    data = await state.get_data()
    results = data.get("results", [])
    if not results:
        return
    try:
        await callback_query.message.edit_reply_markup(reply_markup=recipients_page_kb(results, page))
    except Exception:
        pass


@dp.callback_query(F.data.startswith("mw:pick:"))
async def cb_write_pick(callback_query: CallbackQuery, state: FSMContext):
    idx = int(callback_query.data.split(":")[2])
    data = await state.get_data()
    results = data.get("results", [])
    if idx < 0 or idx >= len(results):
        await callback_query.answer("Список устарел, начни заново.", show_alert=True)
        return
    r = results[idx]
    await state.update_data(recipient_id=r["id"], recipient_label=r["label"])
    await state.set_state(UIStates.write_title)
    await callback_query.answer()
    try:
        await callback_query.message.edit_text(f"✅ Получатель: {r['label']}")
    except Exception:
        pass
    await callback_query.message.answer("Введи тему сообщения:", reply_markup=title_kb())


@dp.message(UIStates.write_title)
async def fsm_write_title(message: types.Message, state: FSMContext):
    title = (message.text or "").strip()
    await state.update_data(title=title)
    await state.set_state(UIStates.write_text)
    await message.answer("Тема принята. Теперь введи текст сообщения:", reply_markup=cancel_kb())


@dp.callback_query(F.data == "mw:notitle")
async def cb_notitle(callback_query: CallbackQuery, state: FSMContext):
    await state.update_data(title="")
    await state.set_state(UIStates.write_text)
    await callback_query.answer()
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback_query.message.answer("Без темы. Введи текст сообщения:", reply_markup=cancel_kb())


@dp.message(UIStates.write_text)
async def fsm_write_text(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст пустой. Введи текст сообщения:", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    recipient_id = data.get("recipient_id")
    recipient_label = data.get("recipient_label") or (f"id={recipient_id}" if recipient_id else "—")
    title = data.get("title", "")
    await state.clear()
    if recipient_id is None:
        await message.answer(
            "Получатель не выбран. Начни заново: ✉️ Сообщения → Написать.",
            reply_markup=main_menu_kb(),
        )
        return
    user_id = message.from_user.id
    message_api = await get_message_api(user_id)
    if not message_api:
        await message.answer(
            "❌ Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return
    status = await message.answer(f"⏳ Отправляю сообщение: {recipient_label}...")
    ok = await lk_send_message(
        message_api=message_api, recipient_id=int(recipient_id),
        title=title, message_text=text, idinfo=0,
    )
    await status.edit_text(
        f"✅ Сообщение отправлено: {recipient_label}" if ok
        else f"❌ Не удалось отправить сообщение: {recipient_label}"
    )


# --- Профиль ---

@dp.callback_query(F.data == "m:profile:notify")
async def cb_notify_settings(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.answer()
    user_id = callback_query.from_user.id
    enabled, minutes = get_notify_settings(user_id)
    await callback_query.message.answer(
        notify_settings_text(enabled, minutes),
        reply_markup=notify_settings_kb(enabled, minutes),
    )


@dp.callback_query(F.data == "m:notify:toggle")
async def cb_notify_toggle(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    enabled, minutes = get_notify_settings(user_id)
    new_enabled = not enabled
    set_notify_enabled(user_id, new_enabled)
    await callback_query.answer("Уведомления включены" if new_enabled else "Уведомления выключены")
    await callback_query.message.edit_text(
        notify_settings_text(new_enabled, minutes),
        reply_markup=notify_settings_kb(new_enabled, minutes),
    )


@dp.callback_query(F.data.startswith("m:notify:min:"))
async def cb_notify_minutes(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    try:
        minutes = int(callback_query.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback_query.answer()
        return
    set_notify_minutes(user_id, minutes)
    enabled, _ = get_notify_settings(user_id)
    await callback_query.answer(f"Буду предупреждать за {minutes} мин")
    await callback_query.message.edit_text(
        notify_settings_text(enabled, minutes),
        reply_markup=notify_settings_kb(enabled, minutes),
    )


@dp.callback_query(F.data == "m:profile:relogin")
async def cb_relogin(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.login_email)
    await callback_query.message.answer(
        "🔄 Повторный вход. Введи email (логин от ЛК):",
        reply_markup=cancel_kb(),
    )


@dp.callback_query(F.data == "m:profile:logout")
async def cb_logout(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    controller = controllers.pop(user_id, None)
    if controller is not None and getattr(controller, "is_running", False):
        try:
            await controller.stop_lesson(user_id)
        except Exception:
            pass
    apis.pop(user_id, None)
    with conn:
        cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
    await callback_query.answer("Вы вышли")
    await callback_query.message.answer(
        "🚪 Ты вышел из личного кабинета, сохранённые данные удалены.\n"
        "Расписание по-прежнему доступно без входа.",
        reply_markup=main_menu_kb(),
    )


# --- Подсказка на нераспознанный ввод ---

@dp.message()
async def fallback_handler(message: types.Message):
    await message.answer(
        "Не понял 🤔 Пользуйся кнопками меню снизу 👇",
        reply_markup=main_menu_kb(),
    )


async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Помощь и описание разделов"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="login", description="Войти в личный кабинет"),
    ]
    await bot.set_my_commands(commands)

async def auto_login_all_users():
    """
    Автоматически авторизует всех пользователей в фоновом режиме.
    """
    logging.info("👥 Проверка пользователей в базе данных...")
    cursor.execute('SELECT user_id FROM users')
    users = cursor.fetchall()
    logging.info(f"📊 Найдено пользователей: {len(users)}")
    
    for idx, user in enumerate(users):
        user_id = user[0]
        logging.info(f"🔐 Авторизация пользователя {user_id}...")
        try:
            success = await auto_login_user(user_id)
            if success:
                logging.info(f"✅ Пользователь {user_id} авторизован, запуск автокликалки...")
                await auto_start_lesson(user_id)
            else:
                logging.warning(f"❌ Не удалось автоматически авторизовать пользователя {user_id} при старте бота.")
        except Exception as e:
            logging.error(f"❌ Ошибка при авторизации пользователя {user_id}: {e}", exc_info=True)
        
        # Стагерим логины, чтобы не получить бан/ERR_MSG на стороне ЛК
        delay = LK_LOGIN_DELAY_SEC + random.random() * LK_LOGIN_JITTER_SEC
        logging.debug("Пауза между логинами пользователей: %.2fs", delay)
        await asyncio.sleep(delay)

async def preload_timetable():
    """
    Предзагружает расписание всех групп в фоновом режиме при старте бота.
    Сначала пытается загрузить из JSON, если не получается - загружает с сервера.
    """
    try:
        logging.info("📅 Начало предзагрузки расписания всех групп...")
        # Пытаемся загрузить из JSON (не принудительно)
        await get_all_groups_timetable(force_reload=False, user_id=None, progress_message=None)
        logging.info("✅ Расписание всех групп предзагружено")
    except Exception as e:
        logging.error(f"❌ Ошибка при предзагрузке расписания: {e}", exc_info=True)

async def on_startup(dp):
    logging.info("🚀 Запуск бота...")
    logging.info("📝 Установка команд бота...")
    await set_bot_commands(bot)
    logging.info("✅ Команды бота установлены")
    
    # Запускаем авторизацию пользователей в фоновом режиме
    logging.info("🔄 Запуск авторизации пользователей в фоновом режиме...")
    asyncio.create_task(auto_login_all_users())
    
    # Запускаем предзагрузку расписания в фоновом режиме
    logging.info("📅 Запуск предзагрузки расписания всех групп в фоновом режиме...")
    asyncio.create_task(preload_timetable())
    
    logging.info("✅ Инициализация завершена, polling готов к запуску...")

async def main():
    logging.info("🎯 Функция main() запущена")
    try:
        await on_startup(dp)
        logging.info("🔄 Запуск polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"❌ Критическая ошибка в main(): {e}", exc_info=True)
        raise

if __name__ == "__main__":
    asyncio.run(main())