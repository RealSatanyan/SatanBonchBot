"""SatanBonchBot — точка входа и оркестратор.

Тонкий оркестратор: подключает доменные роутеры обработчиков (`handlers/`),
поднимает фоновые задачи (heartbeat, автологин пользователей, предзагрузка
расписания, опрос ЛК-сообщений, периодический рефреш) и запускает polling.
Прикладная логика — в модулях `satanbonchbot.*`, импортируется явно и
точечно.
"""
import asyncio
import logging
import random

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

# config импортируется первым: его import-side-effects (load_dotenv / настройка
# логирования / прокси) должны отработать до создания Bot/БД.
from satanbonchbot.config import (
    HEARTBEAT_FILE,
    HEARTBEAT_INTERVAL_SEC,
    LK_LOGIN_DELAY_SEC,
    LK_LOGIN_JITTER_SEC,
    _write_heartbeat,
)
# db импортируется ПОСЛЕ config: при импорте делает sqlite3.connect + CREATE
# TABLE / миграции (зависит от уже отработавшего load_dotenv). Курсор берём как
# db.cursor — чтобы подмена БД в тестовой фикстуре temp_db была видна.
from satanbonchbot import db, lesson_controller
from satanbonchbot.botcore import bot, dp
from satanbonchbot.login_service import auto_login_user, auto_start_lesson
from satanbonchbot.messages_service import message_poll_loop
from satanbonchbot.timetable_service import periodic_refresh_loop, preload_timetable

# Доменные роутеры обработчиков. Порядок include важен: common.router содержит
# fallback_handler без фильтра и обязан включаться ПОСЛЕДНИМ. Остальные домены
# используют непересекающиеся фильтры (Command / F.data-префиксы / состояния
# FSM), поэтому их относительный порядок на маршрутизацию не влияет —
# зафиксирован как в исходнике.
from satanbonchbot.handlers import autoclick, common, messages, profile, schedule


# Хэндлы фоновых задач (heartbeat, автологин, предзагрузка, опрос ЛК,
# рефреш расписания) — для graceful shutdown.
_background_tasks: list = []


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

    # Фоновый опрос входящих ЛК — уведомления о новых сообщениях (задача C.2).
    logging.info("📨 Запуск фонового опроса сообщений ЛК...")
    _background_tasks.append(asyncio.create_task(message_poll_loop()))

    # Периодический рефреш расписания + переопределение групп (задача A.3):
    # без него дифф уведомлений об изменении расписания (C.1) почти не срабатывает.
    logging.info("🔁 Запуск фонового рефреша расписания...")
    _background_tasks.append(asyncio.create_task(periodic_refresh_loop()))

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