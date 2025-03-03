import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from bonchapi import BonchAPI  # Импортируем ваш API
import sqlite3
from contextlib import closing
from dotenv import load_dotenv
from datetime import datetime, time
import pytz
import os
import sys
from aiogram.types import BotCommand

logging.getLogger('aiogram').setLevel(logging.DEBUG)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
conn = sqlite3.connect('users.db', check_same_thread=False)
cursor = conn.cursor()

with closing(sqlite3.connect('users.db')) as db:
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    db.commit()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

api = BonchAPI()
controller = None

class LessonController:
    def __init__(self, api):
        self.api = api
        self.is_running = False
        self.task = None

    def is_time_between(self, start_time, end_time, now_time):
        """Проверка, находится ли текущее время в заданном интервале."""
        if start_time <= end_time:
            return start_time <= now_time <= end_time
        else:  # Интервал переходит через полночь
            return start_time <= now_time or now_time <= end_time

    async def start_lesson(self):
        if self.is_running:
            return "Автокликалка уже запущена."

        self.is_running = True
        moscow_tz = pytz.timezone('Europe/Moscow')
        start_time = time(9, 0)  # 9:00 утра
        end_time = time(21, 0)   # 9:00 вечера

        while self.is_running:
            try:
                now = datetime.now(moscow_tz).time()
                if self.is_time_between(start_time, end_time, now):
                    await self.api.click_start_lesson()
                    logging.info("Клик выполнен.")
                else:
                    logging.info("Время вне рабочего интервала. Клик не выполнен.")
                await asyncio.sleep(5)  # Пауза между проверками
            except Exception as e:
                logging.error(f"Ошибка при выполнении клика: {e}", exc_info=True)
                self.is_running = False
                return f"Ошибка: {e}"
        return "Автокликалка запущена."

    async def stop_lesson(self):
        if not self.is_running:
            return "Автокликалка уже остановлена."

        self.is_running = False
        if self.task:
            self.task.cancel()
        return "Автокликалка остановлена."

    async def get_status(self):
        return "Автокликалка запущена." if self.is_running else "Автокликалка остановлена."

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Этот бот что то типо моей вариации BonchBot."
    )

@dp.message(Command("start_lesson"))
async def cmd_start_lesson(message: types.Message):
    global controller
    if not controller:
        await message.answer("Сначала авторизуйтесь с помощью /login.")
        return

    if controller.is_running:
        await message.answer("Автокликалка уже запущена.")
        return

    controller.task = asyncio.create_task(controller.start_lesson())
    await message.answer("Автокликалка запущена.")

@dp.message(Command("stop_lesson"))
async def cmd_stop_lesson(message: types.Message):
    global controller
    if not controller:
        await message.answer("Сначала авторизуйтесь с помощью /login.")
        return

    if not controller.is_running:
        await message.answer("Автокликалка уже остановлена.")
        return

    await controller.stop_lesson()
    await message.answer("Автокликалка остановлена.")


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    global controller
    if not controller:
        await message.answer("Сначала авторизуйтесь с помощью /login.")
        return

    status = await controller.get_status()
    await message.answer(status)


@dp.message(Command("login"))
async def cmd_login(message: types.Message):
    global controller
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Используйте: /login <email> <password>")
            return

        email, password = args[1], args[2]
        user_id = message.from_user.id

        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        existing_user = cursor.fetchone()

        if existing_user:
            cursor.execute('''
                UPDATE users 
                SET email = ?, password = ? 
                WHERE user_id = ?
            ''', (email, password, user_id))
        else:
            cursor.execute('INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)', (user_id, email, password))

        conn.commit()
        
        await api.login(email, password)
        controller = LessonController(api)
        await message.answer("Авторизация прошла успешно!")

    except Exception as e:
        await message.answer(f"Ошибка авторизации: {e}")

# Добавим команду /my_account для просмотра сохраненных данных
@dp.message(Command("my_account"))
async def cmd_my_account(message: types.Message):
    user_id = message.from_user.id
    cursor.execute('SELECT email FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if result:
        await message.answer(f"Ваш сохраненный email: {result[0]}")
    else:
        await message.answer("У вас нет сохраненного аккаунта.")

# Добавим команду /timetable для получения расписания
@dp.message(Command("timetable"))
async def cmd_timetable(message: types.Message):
    global controller
    if not controller:
        await message.answer("Сначала авторизуйтесь с помощью /login.")
        return

    try:
        timetable = await api.get_timetable()
        await message.answer(f"Ваше расписание:\n{timetable}")
    except Exception as e:
        await message.answer(f"Ошибка при получении расписания: {e}")

async def auto_login_user(user_id):
    global controller
    cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if result:
        email, password = result
        try:
            await api.login(email, password)
            controller = LessonController(api)
            logging.info(f"Пользователь {user_id} автоматически авторизован.")
        except Exception as e:
            logging.error(f"Ошибка автоматической авторизации для пользователя {user_id}: {e}")

async def auto_start_lesson(user_id):
    global controller
    if controller and not controller.is_running:
        controller.task = asyncio.create_task(controller.start_lesson())
        logging.info("Автокликалка запущена после перезапуска сервера.")
        
        
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="start_lesson", description="Запустить автокликалку"),
        BotCommand(command="stop_lesson", description="Остановить автокликалку"),
        BotCommand(command="status", description="Статус автокликалки"),
        BotCommand(command="login", description="Войти в аккаунт"),
        BotCommand(command="my_account", description="Просмотреть сохраненные данные"),
        BotCommand(command="timetable", description="Получить расписание")
    ]
    await bot.set_my_commands(commands)
    
    
async def on_startup(dp):
    await set_bot_commands(bot)
    # При старте бота проверяем всех пользователей и авторизуем их
    cursor.execute('SELECT user_id FROM users')
    users = cursor.fetchall()
    for user in users:
        user_id = user[0]
        await auto_login_user(user_id)
        await auto_start_lesson(user_id)  # Запускаем кликалку после авторизации

async def main():
    await on_startup(dp)
    await dp.start_polling(bot)
    logging.info("Этот лог должен выводиться")

if __name__ == "__main__":
    asyncio.run(main())