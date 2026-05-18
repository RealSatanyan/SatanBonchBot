"""SatanBonchBot — точка входа.

После декомпозиции (задача 4.1) main.py — тонкий оркестратор: импортирует
модули-фасады (config / db / security / lk_client / handlers и т.д.),
регистрирует роутеры обработчиков и запускает polling. Прикладная логика —
в этих модулях; main.py реэкспортит их публичные имена ради обратной
совместимости (`main.X`) и тестов.
"""
import asyncio
import logging
import random
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

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

# Клиент личного кабинета ЛК извлечён в lk_client.py (задача 4.1, шаг 10).
# main.py остаётся фасадом — реэкспортит публичные имена (DebuggableBonchAPI,
# save_debug_dump, get_timetable_api, get_message_api, lk_*). Приватный
# _prune_debug_dumps не попадает под `import *` — импортируем явно.
# Разделяемое состояние apis/timetable_api живёт в lk_client и доступно как
# lk_client.apis / lk_client.timetable_api (модуль-квалифицированный доступ).
import lk_client
from lk_client import *
from lk_client import _prune_debug_dumps

# Контроллер автоотметки занятий (LessonController) извлечён в
# lesson_controller.py (задача 4.1, шаг 11). main.py остаётся фасадом —
# реэкспортит класс LessonController. Импорт идёт ПОСЛЕ lk_client, т.к.
# lesson_controller зависит от lk_client (вниз по слоям; цикла нет).
# Реестр контроллеров `controllers` НЕ реэкспортируется именем — main.py
# обращается к нему как lesson_controller.controllers (модуль-квалифицированный
# доступ), чтобы переприсваивания/мутации словаря были видны всем.
import lesson_controller
from lesson_controller import LessonController

# Сервис загрузки расписания всех групп извлечён в timetable_service.py
# (задача 4.1, шаг 12b). main.py остаётся фасадом — реэкспортит публичные
# функции сервиса. Внутреннее изменяемое состояние сервиса
# (all_groups_timetable_cache, timetable_loading, timetable_progress,
# timetable_progress_users) НЕ реэкспортируется именами — main.py обращается
# к нему как timetable_service.<имя> (модуль-квалифицированный доступ), т.к.
# эти переменные переприсваиваются на уровне модуля сервиса.
import timetable_service
from timetable_service import (
    all_groups_timetable_with_progress,
    send_progress_update,
    progress_updater,
    get_all_groups_timetable,
    _refresh_timetable_quietly,
    preload_timetable,
)

# Сервис списка сообщений ЛК извлечён в messages_service.py (задача 4.1,
# шаг 12c). main.py остаётся фасадом — реэкспортит публичные функции сервиса
# и константу MESSAGES_CACHE_TTL_SEC. Внутреннее изменяемое состояние сервиса
# (message_states, pending_lk_messages) НЕ реэкспортируется именами — main.py
# обращается к нему как messages_service.<имя> (модуль-квалифицированный
# доступ), т.к. эти словари читаются/пишутся хэндлерами и их мутации должны
# быть видны всем.
import messages_service
from messages_service import (
    MESSAGES_CACHE_TTL_SEC,
    show_message_list,
    format_message_count,
    _messages_cache_fresh,
    _build_message_state,
    _invalidate_messages_cache,
)

# Сервис авторизации пользователей в ЛК извлечён в login_service.py (задача 4.1,
# шаг 12d). main.py остаётся фасадом — реэкспортит публичные функции и константы
# валидации логина. Реестры apis/controllers сервис мутирует
# модуль-квалифицированно (lk_client.apis / lesson_controller.controllers).
# Стартовая оркестрация auto_login_all_users остаётся в main.py (шаг 13) и
# вызывает auto_login_user / auto_start_lesson через этот реэкспорт.
import login_service
from login_service import (
    perform_login,
    auto_login_user,
    auto_start_lesson,
    parse_login_credentials,
    LOGIN_CMD_RE,
    EMAIL_RE,
    MAX_EMAIL_LEN,
    MAX_PASSWORD_LEN,
)


# Шифрование паролей (ENCRYPTION_KEY, _fernet, encrypt_password,
# decrypt_password) извлечено в security.py — реэкспортируется выше.


# Соединение с БД (conn/cursor), схема и миграции извлечены в db.py — импорт db
# выше уже выполнил их side-effects. Доступ к курсору: db.cursor / db.conn.

# bot / dp / tg_session извлечены в botcore.py (задача 4.1, шаг 12a).
from botcore import bot, dp, tg_session

# Реестр контроллеров `controllers` извлечён в lesson_controller.py (задача 4.1,
# шаг 11). Доступ — через lesson_controller.controllers (модуль-квалифицированно).
# Реестр API-инстансов `apis` и синглтон `timetable_api` извлечены в lk_client.py
# (задача 4.1, шаг 10). Доступ — через lk_client.apis / lk_client.timetable_api.

# Хэндлы фоновых задач (heartbeat, автологин, предзагрузка) — для graceful shutdown.
_background_tasks: list = []


# Мониторинг сбоёв парсера ЛК (ParserFailureMonitor, _parser_failure_monitor,
# _alert_admins_parser_broken, _note_parser_failure) извлечён в monitoring.py
# (задача 4.1, шаг 6). Доступен через реэкспорт выше.

# Внутреннее изменяемое состояние сервиса загрузки расписания
# (all_groups_timetable_cache, timetable_loading, timetable_progress,
# timetable_progress_users) извлечено в timetable_service.py (задача 4.1,
# шаг 12b). Доступ — через timetable_service.<имя> (модуль-квалифицированно).
# Сервис списка сообщений ЛК (message_states, pending_lk_messages,
# MESSAGES_CACHE_TTL_SEC, show_message_list, format_message_count,
# _messages_cache_fresh, _build_message_state, _invalidate_messages_cache)
# извлечён в messages_service.py (задача 4.1, шаг 12c). Функции и константа
# реэкспортированы выше; изменяемые словари доступны как
# messages_service.message_states / messages_service.pending_lk_messages.
# Сервис авторизации в ЛК (perform_login, auto_login_user, auto_start_lesson,
# parse_login_credentials и константы валидации LOGIN_CMD_RE, EMAIL_RE,
# MAX_EMAIL_LEN, MAX_PASSWORD_LEN) извлечён в login_service.py (задача 4.1,
# шаг 12d) — реэкспортируется выше.

# Rate-limit на попытки входа (LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SEC,
# _login_attempts, check_login_rate_limit, format_retry_after) извлечён в
# security.py — реэкспортируется выше.


# Debug-дампы HTML-страниц ЛК (save_debug_dump, _prune_debug_dumps) извлечены
# в lk_client.py (задача 4.1, шаг 10) — реэкспортируются выше.


# Класс LessonController (контроллер автоотметки занятий) извлечён в
# lesson_controller.py (задача 4.1, шаг 11). main.py остаётся фасадом —
# LessonController реэкспортируется из lesson_controller (импорт выше).


# ==========================================================================
#  Обработчики aiogram (задача 4.1, шаг 12e)
# ==========================================================================
# Все 62 обработчика вынесены в пакет handlers/ — по доменам, каждый файл со
# своим Router(). Поведение не менялось: декораторы @dp.* стали @router.*,
# фильтры сохранены дословно. Регистрация роутеров — в on_startup ниже.
#
# Порядок include_router важен: aiogram перебирает роутеры в порядке
# регистрации, первый совпавший хэндлер выигрывает. common.router включается
# ПОСЛЕДНИМ — в нём fallback_handler (@router.message() без фильтра), который
# обязан проверяться после всех остальных. Домены используют непересекающиеся
# фильтры (Command / F.data-префиксы / состояния FSM), поэтому относительный
# порядок schedule/autoclick/messages/profile на маршрутизацию не влияет;
# зафиксирован как в исходнике для надёжности.
from handlers import common, schedule, autoclick, messages, profile


def register_routers(dispatcher: Dispatcher) -> None:
    """Подключает доменные роутеры к диспетчеру. common — последним (fallback)."""
    dispatcher.include_router(schedule.router)
    dispatcher.include_router(autoclick.router)
    dispatcher.include_router(messages.router)
    dispatcher.include_router(profile.router)
    dispatcher.include_router(common.router)


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

# preload_timetable извлечён в timetable_service.py (задача 4.1, шаг 12b) —
# реэкспортируется выше; on_startup использует его как preload_timetable.

async def heartbeat_loop():
    """Периодически обновляет heartbeat-файл — для Docker healthcheck."""
    while True:
        _write_heartbeat(HEARTBEAT_FILE)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

async def on_startup(dp):
    logging.info("🚀 Запуск бота...")

    # Подключаем доменные роутеры из пакета handlers/ (задача 4.1, шаг 12e).
    # common.router — последним из-за fallback_handler.
    register_routers(dp)
    logging.info("✅ Роутеры обработчиков подключены")

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
    for controller in list(lesson_controller.controllers.values()):
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