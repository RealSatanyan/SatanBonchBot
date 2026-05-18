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
import json
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

# Конфигурация извлечена в config.py (задача 4.1, шаг 1). main.py остаётся
# фасадом — реэкспортит перенесённые имена, чтобы main.X и тесты работали.
# Импорт config выполняется здесь, рано, чтобы его import-side-effects
# (load_dotenv / настройка логирования / прокси) отработали до создания Bot/БД.
import config
from config import *
from config import (
    _LK_SEMAPHORE_BY_LOOP,
    _LOG_LEVEL,
    _resolve_log_level,
    _parse_admin_ids,
    _write_heartbeat,
)

# Безопасность извлечена в security.py (задача 4.1, шаг 3). main.py остаётся
# фасадом — реэкспортит перенесённые имена. Импорт идёт ПОСЛЕ config, т.к.
# security.py читает ENCRYPTION_KEY из окружения при импорте, а load_dotenv()
# отрабатывает в config.py. Приватные имена (_fernet, _login_attempts) не
# попадают под `import *` — импортируем их явно.
import security
from security import *
from security import _fernet, _login_attempts

# Работа с БД извлечена в db.py (задача 4.1, шаг 4). main.py остаётся фасадом —
# реэкспортит DB-хелперы. Импорт идёт ПОСЛЕ security: db.py при импорте делает
# side-effects (sqlite3.connect + CREATE TABLE / миграции). conn/cursor НЕ
# реэкспортируются именами — main.py обращается к ним как db.conn / db.cursor,
# чтобы подмена БД в тестовой фикстуре temp_db была видна.
import db
from db import (
    is_registered,
    get_notify_settings,
    set_notify_enabled,
    set_notify_minutes,
    get_autoclick_enabled,
    set_autoclick_enabled,
    NOTIFY_DEFAULT_MINUTES,
)

# Сборщики UI-клавиатур извлечены в keyboards.py (задача 4.1, шаг 5). main.py
# остаётся фасадом — реэкспортит публичные имена клавиатур (*_kb, BTN_*,
# HELP_TEXT, get_*_navigation_buttons, notify_settings_text, NOTIFY_MINUTE_OPTIONS,
# RECIPIENTS_PER_PAGE), чтобы хэндлеры и тесты обращались к ним как main.<имя>.
# keyboards.py — чистый модуль без side-effects, main НЕ импортирует.
import keyboards
from keyboards import *

# Мониторинг сбоёв парсера ЛК извлечён в monitoring.py (задача 4.1, шаг 6).
# main.py остаётся фасадом — реэкспортит перенесённые имена. Приватные имена
# (_parser_failure_monitor, _alert_admins_parser_broken, _note_parser_failure)
# не попадают под `import *` — импортируем их явно.
import monitoring
from monitoring import *
from monitoring import (
    _parser_failure_monitor,
    _alert_admins_parser_broken,
    _note_parser_failure,
)
import timetable_cache
from timetable_cache import *
from timetable_cache import (
    _write_timetable_meta,
    _read_timetable_meta,
    _timetable_age_seconds,
    _is_timetable_stale,
    _format_cache_age,
    _timetable_cache_age_now,
)

import formatting
from formatting import *
from formatting import _week_offset_for_date, _moscow_today

# Генерация PNG-изображений расписания извлечена в rendering.py
# (задача 4.1, шаг 9). main.py остаётся фасадом — реэкспортит публичные имена.
import rendering
from rendering import *
from rendering import (
    draw_rounded_rectangle,
    draw_lesson,
    draw_text_with_emoji,
)

# Импорт для работы с расписанием без авторизации
try:
    from TImetabels import BonchAPI as TimetableBonchAPI, BROWSER_HEADERS
except ImportError:
    # Если импорт не работает, используем альтернативный путь
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from TImetabels import BonchAPI as TimetableBonchAPI, BROWSER_HEADERS

# Клиент личного кабинета ЛК извлечён в lk_client.py (задача 4.1, шаг 10).
# main.py остаётся фасадом — реэкспортит публичные имена (DebuggableBonchAPI,
# save_debug_dump, get_timetable_api, get_message_api, lk_*). Приватный
# _prune_debug_dumps не попадает под `import *` — импортируем явно.
# Разделяемое состояние apis/timetable_api живёт в lk_client и доступно как
# lk_client.apis / lk_client.timetable_api (модуль-квалифицированный доступ).
import lk_client
from lk_client import *
from lk_client import _prune_debug_dumps


# Шифрование паролей (ENCRYPTION_KEY, _fernet, encrypt_password,
# decrypt_password) извлечено в security.py — реэкспортируется выше.


# Telegram-сессия БЕЗ прокси.
tg_session = AiohttpSession()
# Соединение с БД (conn/cursor), схема и миграции извлечены в db.py — импорт db
# выше уже выполнил их side-effects. Доступ к курсору: db.cursor / db.conn.

bot = Bot(token=BOT_TOKEN, session=tg_session)
dp = Dispatcher()

controllers = {}  # Словарь для хранения контроллеров
# Реестр API-инстансов `apis` и синглтон `timetable_api` извлечены в lk_client.py
# (задача 4.1, шаг 10). Доступ — через lk_client.apis / lk_client.timetable_api.

# Хэндлы фоновых задач (heartbeat, автологин, предзагрузка) — для graceful shutdown.
_background_tasks: list = []


# Мониторинг сбоёв парсера ЛК (ParserFailureMonitor, _parser_failure_monitor,
# _alert_admins_parser_broken, _note_parser_failure) извлечён в monitoring.py
# (задача 4.1, шаг 6). Доступен через реэкспорт выше.

all_groups_timetable_cache = None  # Кэш расписания всех групп
timetable_loading = False  # Флаг загрузки расписания
timetable_progress_users = {}  # Словарь {user_id: message} для отправки прогресса
timetable_progress = {'current': 0, 'total': 0, 'start_time': None}  # Прогресс загрузки
# Состояния навигации по сообщениям
message_states = {}  # Словарь {user_id: {'messages': [], 'current_index': 0}} для навигации по сообщениям
# Тёплый кэш списка сообщений: повторный заход в /messages в пределах TTL
# показывает уже загруженное, не перезапрашивая первую страницу из ЛК.
MESSAGES_CACHE_TTL_SEC = 300


def format_message_count(loaded: int, total_pages: int, per_page: int, has_more: bool) -> str:
    """Счётчик сообщений: точное число либо оценка «≈N» (страниц × размер)."""
    if not has_more:
        return str(loaded)
    if total_pages > 1 and per_page > 0:
        return f"≈{total_pages * per_page}"
    return f"{loaded}+"


def _messages_cache_fresh(state, now_ts: float, ttl_sec: float) -> bool:
    """True — кэш списка сообщений ещё свежий, можно показать без перезапроса."""
    if not state or not state.get('messages'):
        return False
    fetched_at = state.get('fetched_at')
    if fetched_at is None:
        return False
    return (now_ts - fetched_at) < ttl_sec


def _build_message_state(api, first_page: dict) -> dict:
    """Собирает запись message_states после загрузки первой страницы сообщений."""
    messages = first_page['messages']
    return {
        'api': api,
        'messages': messages,
        'total_pages': first_page['total_pages'],
        'loaded_pages': 1,
        'current_index': 0,
        'per_page': len(messages),
        'fetched_at': time_module.time(),
    }


def _invalidate_messages_cache(user_id) -> None:
    """Помечает кэш сообщений устаревшим — следующий /messages перезапросит ЛК."""
    state = message_states.get(user_id)
    if state:
        state['fetched_at'] = None
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


# Rate-limit на попытки входа (LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SEC,
# _login_attempts, check_login_rate_limit, format_retry_after) извлечён в
# security.py — реэкспортируется выше.


# Debug-дампы HTML-страниц ЛК (save_debug_dump, _prune_debug_dumps) извлечены
# в lk_client.py (задача 4.1, шаг 10) — реэкспортируются выше.


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
                    clicked = await self.api.click_start_lesson(self.user_id)
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
        db.cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (self.user_id,))
        result = db.cursor.fetchone()
        if not result:
            raise ValueError(f"Не найдены данные для переавторизации пользователя {self.user_id}")
        
        email, password = result
        password = decrypt_password(password)
        # Создаем новый экземпляр API и авторизуемся
        lk_client.apis[self.user_id] = lk_client.DebuggableBonchAPI()
        await lk_client.apis[self.user_id].login(email, password)
        # Обновляем ссылку на API в контроллере
        self.api = lk_client.apis[self.user_id]

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
    
    db.cursor.execute('SELECT email FROM users WHERE user_id = ?', (user_id,))
    result = db.cursor.fetchone()

    status_parts = []
    if result:
        status_parts.append(f"📧 Email: {result[0]}")
    else:
        status_parts.append("❌ Нет сохраненного аккаунта в БД")
        await message.answer("\n".join(status_parts))
        return
    
    # Проверяем состояние авторизации ДО попытки восстановления
    has_api_before = user_id in lk_client.apis
    has_controller_before = user_id in controllers
    
    status_parts.append(f"🔑 API авторизован: {'✅ Да' if has_api_before else '❌ Нет'}")
    status_parts.append(f"🎮 Контроллер создан: {'✅ Да' if has_controller_before else '❌ Нет'}")
    
    # Если пользователь есть в БД, но нет авторизации, пытаемся восстановить
    if not has_api_before or not has_controller_before:
        status_parts.append("\n⚠️ Обнаружена проблема с авторизацией. Попробую восстановить...")
        logging.info(f"Попытка восстановить авторизацию для пользователя {user_id}")
        success = await auto_login_user(user_id)
        
        # Проверяем состояние ПОСЛЕ попытки восстановления
        has_api_after = user_id in lk_client.apis
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

# --- Пресеты расписания «Сегодня» / «Завтра» ---------------------------------

@dp.callback_query(F.data.startswith("image_week_"))
async def process_image_week(callback_query: CallbackQuery):
    # Извлекаем смещение недели из callback_data
    week_offset = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id

    if user_id not in lk_client.apis:
        await callback_query.answer("Сначала авторизуйтесь с помощью /login.", show_alert=True)
        return

    try:
        # Получаем расписание для выбранной недели
        timetable = await lk_client.apis[user_id].get_timetable(week_offset=week_offset)

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
        
# Функции навигации по неделям (get_*_week_navigation_buttons) извлечены в
# keyboards.py (задача 4.1, шаг 5) — реэкспортируются через `from keyboards import *`.

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

@dp.callback_query(F.data.startswith("group_day_"))
async def process_group_day(callback_query: CallbackQuery):
    """Пресет «Сегодня» / «Завтра» для расписания группы (offset 0 / 1)."""
    try:
        import base64
        rest = callback_query.data[len("group_day_"):]
        encoded_name, offset_str = rest.rsplit("_", 1)
        offset = int(offset_str)
        group_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')

        global all_groups_timetable_cache
        if all_groups_timetable_cache is None or group_name not in all_groups_timetable_cache:
            await callback_query.answer("Расписание группы недоступно.", show_alert=True)
            return

        timetable = all_groups_timetable_cache[group_name]
        if not timetable or isinstance(timetable, str):
            await callback_query.answer(f"Расписание для группы '{group_name}' недоступно", show_alert=True)
            return

        target = _moscow_today() + timedelta(days=offset)
        day_lessons = filter_group_lessons_by_date(timetable, target.strftime("%Y.%m.%d"))
        label = "Сегодня" if offset == 0 else "Завтра"
        title = f"Группа {group_name} — {label} ({target.strftime('%d.%m')})"

        if not day_lessons:
            text = f"📅 {title}\n\nЗанятий не найдено 🎉"
        else:
            text = format_timetable_dict(day_lessons, title)
            if len(text) > 4000:
                text = text[:4000] + "\n\n... (сообщение обрезано)"

        await callback_query.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=get_group_week_navigation_buttons(group_name, 0),
        )
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Ошибка пресета расписания группы: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось показать расписание. Попробуй позже.", show_alert=True)

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
        if user_id not in lk_client.apis:
            await callback_query.answer("Сначала авторизуйтесь с помощью /login.", show_alert=True)
            return

        timetable = await lk_client.apis[user_id].get_timetable(week_offset=week_offset)
        
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

@dp.callback_query(F.data.startswith("my_day_"))
async def process_my_day(callback_query: CallbackQuery):
    """Пресет «Сегодня» / «Завтра» для личного расписания (offset 0 / 1)."""
    user_id = callback_query.from_user.id
    if user_id not in lk_client.apis:
        await callback_query.answer("Сначала авторизуйтесь с помощью /login.", show_alert=True)
        return
    try:
        offset = int(callback_query.data.split("_")[2])
        today = _moscow_today()
        target = today + timedelta(days=offset)
        week_offset = _week_offset_for_date(target, today)

        timetable = await lk_client.apis[user_id].get_timetable(week_offset=week_offset)
        day_lessons = filter_personal_lessons_by_date(timetable, target.strftime("%Y-%m-%d"))
        label = "Сегодня" if offset == 0 else "Завтра"
        title = f"{label} ({target.strftime('%d.%m')})"

        await callback_query.message.edit_text(
            format_timetable(day_lessons, title=title),
            parse_mode="Markdown",
            reply_markup=get_week_navigation_buttons(week_offset=0),
        )
        await callback_query.answer()
    except Exception as e:
        logging.error("Ошибка пресета личного расписания: %s", e, exc_info=True)
        await callback_query.answer("⚠️ Не удалось показать расписание. Попробуй позже.", show_alert=True)

@dp.message(Command("timetable"))
async def cmd_timetable(message: types.Message, uid: int = None):
    user_id = uid if uid is not None else message.from_user.id
    if user_id not in lk_client.apis:  # Проверяем, есть ли api для пользователя
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in lk_client.apis:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    try:
        # Получаем расписание для текущей недели
        timetable = await lk_client.apis[user_id].get_timetable(week_offset=0)
        
        # Форматируем расписание
        formatted_timetable = format_timetable(timetable)
        
        # Добавляем инлайн-кнопки
        reply_markup = get_week_navigation_buttons(week_offset=0)
        
        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
    
    except Exception as e:
        logging.error("Ошибка при получении расписания: %s", e, exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")

# get_timetable_api (вместе с синглтоном timetable_api) извлечён в lk_client.py
# (задача 4.1, шаг 10) — реэкспортируется выше.

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

# --- TTL-кэш расписания групп ------------------------------------------------
# TTL-хелперы вынесены в timetable_cache.py (задача 4.1, шаг 7), доступны здесь
# через реэкспорт. Сервис загрузки расписания остаётся в main.py.


async def _refresh_timetable_quietly() -> None:
    """Фоновое обновление расписания групп без прогресс-сообщений."""
    try:
        await get_all_groups_timetable(force_reload=True)
        logging.info("Фоновое обновление расписания групп завершено")
    except Exception:
        logging.warning("Фоновое обновление расписания не удалось", exc_info=True)


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
            # Сохранение в JSON уже выполняется в all_groups_timetable_with_progress.
            # Метку времени пишем в sidecar — для TTL и текста «обновлено N назад».
            _write_timetable_meta(datetime.now(pytz.timezone("Europe/Moscow")))
            
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

    # TTL: если кэш в памяти устарел и фоновая загрузка не идёт — обновляем в
    # фоне; пользователю сразу отдаём текущие (возможно устаревшие) данные.
    if (
        all_groups_timetable_cache is not None
        and not force_reload
        and not timetable_loading
        and _is_timetable_stale(_timetable_cache_age_now(), TIMETABLE_TTL_HOURS)
    ):
        logging.info("Кэш расписания устарел — запускаю фоновое обновление")
        asyncio.create_task(_refresh_timetable_quietly())

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
        # Тёплый кэш: повторный заход в пределах TTL — показываем без перезапроса
        # первой страницы из ЛК (экономит ~2–3 с). Кнопка «🔄 Обновить список»
        # остаётся; кэш сбрасывается после отправки сообщения.
        cached = message_states.get(user_id)
        if _messages_cache_fresh(cached, time_module.time(), MESSAGES_CACHE_TTL_SEC):
            logging.info(f"Сообщения для {user_id} показаны из кэша (без перезапроса ЛК)")
            await show_message_list(user_id, message.chat.id, 0)
            return

        # Получаем API для работы с сообщениями
        message_api = await get_message_api(user_id)
        if not message_api:
            await message.answer("❌ Не удалось авторизоваться. Пожалуйста, выполните /login для авторизации.")
            return

        # Ленивая загрузка: тянем только первую страницу (~20 свежих сообщений),
        # остальные подгружаются по мере листания. Так /messages открывается за
        # пару секунд вместо ~10 на все 35 страниц.
        status_msg = await message.answer("⏳ Загружаю сообщения...")
        first_page = await message_api.get_messages_page(1)
        messages = first_page['messages']

        if not messages:
            # Проверяем, может быть проблема с авторизацией
            if not hasattr(message_api, 'cookies') or not message_api.cookies:
                await status_msg.edit_text("❌ Ошибка авторизации. Пожалуйста, выполните /login еще раз.")
            else:
                await status_msg.edit_text("📭 У вас нет входящих сообщений.\n\n💡 Если сообщения должны быть, проверьте авторизацию через /login")
            return

        # Сохраняем состояние для навигации (включая api для подгрузки страниц)
        message_states[user_id] = _build_message_state(message_api, first_page)

        try:
            await status_msg.delete()
        except Exception:
            pass
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

    state = message_states[user_id]
    messages = state['messages']
    if not messages or index < 0 or index >= len(messages):
        return

    # Есть ли ещё не загруженные страницы (для счётчика и кнопки «Вперёд»).
    has_more_pages = state.get('loaded_pages', 1) < state.get('total_pages', 1)

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
    
    count_display = format_message_count(
        len(messages), state.get('total_pages', 1), state.get('per_page', 0), has_more_pages
    )
    text = f"{unread_marker} *Сообщение {index + 1} из {count_display}*\n\n"
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
    if index < len(messages) - 1 or has_more_pages:
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
            state = message_states.get(user_id)
            if not state:
                await callback_query.answer("Список устарел — открой «Сообщения» заново", show_alert=True)
                return
            # Дошли до конца загруженного — лениво подгружаем следующую страницу.
            if index >= len(state['messages']) and state.get('loaded_pages', 1) < state.get('total_pages', 1):
                next_page = state.get('loaded_pages', 1) + 1
                page_data = await state['api'].get_messages_page(next_page)
                state['messages'].extend(page_data['messages'])
                state['loaded_pages'] = next_page
            if index < len(state['messages']):
                state['current_index'] = index
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
            # Обновление списка сообщений — заново тянем первую страницу.
            await callback_query.answer("🔄 Обновляю список...")

            message_api = await get_message_api(user_id)
            if not message_api:
                await callback_query.message.answer("❌ Ошибка авторизации")
                return

            first_page = await message_api.get_messages_page(1)
            if not first_page['messages']:
                await callback_query.message.answer("📭 У вас нет входящих сообщений.")
                await callback_query.message.delete()
                return

            message_states[user_id] = _build_message_state(message_api, first_page)

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
    db.cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
    result = db.cursor.fetchone()
    if not result:
        logging.info(f"Пользователь {user_id} не найден в базе данных.")
        return False
    
    email, password = result
    password = decrypt_password(password)
    logging.info(f"Попытка автоматической авторизации для пользователя {user_id} (email: {email})")
    try:
        lk_client.apis[user_id] = lk_client.DebuggableBonchAPI()
        ok = await lk_client.apis[user_id].login(email, password)
        if not ok:
            raise ValueError("auto_login_failed")

        controllers[user_id] = LessonController(lk_client.apis[user_id], bot, user_id)  # Передаем bot и user_id

        logging.info("✅ Пользователь %s успешно автоматически авторизован.", user_id)
        return True
    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ Ошибка автоматической авторизации для пользователя {user_id} (email: {email}): {error_msg}", exc_info=True)
        # Удаляем частично созданные объекты при ошибке
        if user_id in lk_client.apis:
            del lk_client.apis[user_id]
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

# get_message_api и LK-сервис-функции (lk_search_recipients, lk_upload_file,
# lk_send_message) извлечены в lk_client.py (задача 4.1, шаг 10) —
# реэкспортируются выше.

# ==========================================================================
#  Пользовательский интерфейс: меню, онбординг, пошаговые диалоги
# ==========================================================================

from states import UIStates

# Сборщики клавиатур и UI-константы (BTN_*, HELP_TEXT, *_kb, notify_settings_text,
# NOTIFY_MINUTE_OPTIONS, RECIPIENTS_PER_PAGE) извлечены в keyboards.py
# (задача 4.1, шаг 5) — реэкспортируются через `from keyboards import *` выше.

# DB-хелперы (is_registered, get/set_notify_*, get/set_autoclick_enabled) и
# константа NOTIFY_DEFAULT_MINUTES извлечены в db.py — реэкспортируются выше.


async def perform_login(user_id: int, email: str, password: str) -> bool:
    """Входит в ЛК и сохраняет данные в БД только при успешном входе."""
    try:
        api = lk_client.DebuggableBonchAPI()
        ok = await api.login(email, password)
        if not ok:
            return False
        lk_client.apis[user_id] = api
        controllers[user_id] = LessonController(api, bot, user_id)
        db.cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        existing = db.cursor.fetchone()
        encrypted_password = encrypt_password(password)
        with db.conn:
            if existing:
                db.cursor.execute('UPDATE users SET email = ?, password = ? WHERE user_id = ?',
                                  (email, encrypted_password, user_id))
            else:
                db.cursor.execute('INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)',
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
    age = _format_cache_age(_timetable_cache_age_now())
    await message.answer(
        f"📅 Чьё расписание показать?\n\n🗂 Расписание групп обновлено: {age}",
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
    db.cursor.execute('SELECT email FROM users WHERE user_id = ?', (user_id,))
    row = db.cursor.fetchone()
    email = row[0] if row else "—"
    active = "🟢 активен" if user_id in lk_client.apis else "🟡 восстановится при первом действии"
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


@dp.callback_query(F.data == "m:sched:reload")
async def cb_sched_reload(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    await callback_query.answer("Обновляю расписание...")
    logging.info(f"Пользователь {user_id} запросил обновление расписания групп (меню)")
    status_msg = await callback_query.message.answer(
        "⏳ Обновляю расписание всех групп… Это может занять некоторое время."
    )
    try:
        all_timetable = await get_all_groups_timetable(
            force_reload=True, user_id=user_id, progress_message=status_msg
        )
        await status_msg.edit_text(
            f"✅ Расписание обновлено! Загружено {len(all_timetable)} групп."
        )
    except Exception as e:
        logging.error(f"Ошибка обновления расписания (меню) для {user_id}: {e}", exc_info=True)
        try:
            await status_msg.edit_text("❌ Не удалось обновить расписание. Попробуй позже.")
        except Exception:
            await callback_query.message.answer("❌ Не удалось обновить расписание. Попробуй позже.")


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


# title_kb, RECIPIENTS_PER_PAGE и recipients_page_kb извлечены в keyboards.py
# (задача 4.1, шаг 5) — реэкспортируются через `from keyboards import *`.


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
    if ok:
        # Список сообщений мог измениться — сбрасываем тёплый кэш.
        _invalidate_messages_cache(user_id)


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
    lk_client.apis.pop(user_id, None)
    with db.conn:
        db.cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
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
    db.cursor.execute('SELECT user_id FROM users')
    users = db.cursor.fetchall()
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

async def heartbeat_loop():
    """Периодически обновляет heartbeat-файл — для Docker healthcheck."""
    while True:
        _write_heartbeat(HEARTBEAT_FILE)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

async def on_startup(dp):
    logging.info("🚀 Запуск бота...")
    logging.info("📝 Установка команд бота...")
    await set_bot_commands(bot)
    logging.info("✅ Команды бота установлены")

    # Heartbeat для healthcheck: пишем сразу, дальше обновляем по таймеру.
    _write_heartbeat(HEARTBEAT_FILE)
    _background_tasks.append(asyncio.create_task(heartbeat_loop()))

    # Запускаем авторизацию пользователей в фоновом режиме
    logging.info("🔄 Запуск авторизации пользователей в фоновом режиме...")
    _background_tasks.append(asyncio.create_task(auto_login_all_users()))

    # Запускаем предзагрузку расписания в фоновом режиме
    logging.info("📅 Запуск предзагрузки расписания всех групп в фоновом режиме...")
    _background_tasks.append(asyncio.create_task(preload_timetable()))

    logging.info("✅ Инициализация завершена, polling готов к запуску...")

async def on_shutdown():
    """Корректная остановка: гасим автокликалки и фоновые задачи."""
    logging.info("🛑 Остановка бота: завершаем фоновые задачи...")

    # Сигналим автокликалкам остановиться и даём текущим LK-запросам доработать.
    controller_tasks = []
    for controller in list(controllers.values()):
        controller.is_running = False
        task = getattr(controller, "task", None)
        if task and not task.done():
            controller_tasks.append(task)
    if controller_tasks:
        _, still_running = await asyncio.wait(controller_tasks, timeout=15)
        for task in still_running:
            task.cancel()

    # Гасим остальные фоновые задачи (heartbeat, автологин, предзагрузка).
    for task in _background_tasks:
        if not task.done():
            task.cancel()

    leftovers = [t for t in (*controller_tasks, *_background_tasks) if not t.done()]
    if leftovers:
        try:
            await asyncio.wait(leftovers, timeout=10)
        except Exception:
            logging.warning("Не все фоновые задачи завершились вовремя", exc_info=True)

    HEARTBEAT_FILE.unlink(missing_ok=True)
    logging.info("🛑 Бот остановлен.")

async def main():
    logging.info("🎯 Функция main() запущена")
    try:
        await on_startup(dp)
        logging.info("🔄 Запуск polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"❌ Критическая ошибка в main(): {e}", exc_info=True)
        raise
    finally:
        await on_shutdown()

if __name__ == "__main__":
    asyncio.run(main())