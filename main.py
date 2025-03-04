import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from bonchapi import BonchAPI  # Импортируем ваш API
import sqlite3
from contextlib import closing
from dotenv import load_dotenv
from datetime import datetime, time, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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

        # Время запуска автокликалки (9:00 и 16:00 по Москве)
        self.scheduled_times = [time(9, 0), time(16, 0)]

    async def start_lesson(self):
        if self.is_running:
            return "Автокликалка уже запущена."

        self.is_running = True
        moscow_tz = pytz.timezone('Europe/Moscow')

        while self.is_running:
            try:
                now = datetime.now(moscow_tz)
                next_run = self.get_next_run_time(now)

                # Ожидание до следующего времени запуска
                wait_seconds = (next_run - now).total_seconds()
                if wait_seconds > 0:
                    logging.info(f"Ожидание следующего запуска в {next_run.strftime('%H:%M')}.")
                    await asyncio.sleep(wait_seconds)

                # Проверяем, что автокликалка всё ещё запущена
                if not self.is_running:
                    break

                # Выполняем клик
                await self.api.click_start_lesson()
                logging.info("Клик выполнен.")

            except Exception as e:
                logging.error(f"Ошибка при выполнении клика: {e}", exc_info=True)
                await asyncio.sleep(60)  # Пауза перед повторной попыткой
                continue  # Продолжаем цикл для повторной попытки

        return "Автокликалка запущена."

    def get_next_run_time(self, now):
        """Вычисляет следующее время запуска."""
        for scheduled_time in self.scheduled_times:
            # Создаем datetime для следующего запуска
            next_run = datetime.combine(now.date(), scheduled_time, tzinfo=now.tzinfo)
            if next_run <= now:
                # Если время уже прошло сегодня, планируем на следующий день
                next_run += timedelta(days=1)
            return next_run
        return None

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

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Этот бот что то типо моей вариации BonchBot."
    )

controllers = {}  # Словарь для хранения контроллеров

@dp.message(Command("login"))
async def cmd_login(message: types.Message):
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
        controllers[user_id] = LessonController(api)  # Создаем контроллер для пользователя
        await message.answer("Авторизация прошла успешно!")

    except Exception as e:
        await message.answer(f"Ошибка авторизации: {e}")

@dp.message(Command("start_lesson"))
async def cmd_start_lesson(message: types.Message):
    user_id = message.from_user.id
    if user_id not in controllers:
        await message.answer("Сначала авторизуйтесь с помощью /login.")
        return

    controller = controllers[user_id]  # Используем контроллер пользователя
    if controller.is_running:
        await message.answer("Автокликалка уже запущена.")
        return

    controller.task = asyncio.create_task(controller.start_lesson())
    await message.answer("Автокликалка запущена.")

@dp.message(Command("stop_lesson"))
async def cmd_stop_lesson(message: types.Message):
    user_id = message.from_user.id
    if user_id not in controllers:
        await message.answer("Сначала авторизуйтесь с помощью /login.")
        return

    controller = controllers[user_id]  # Используем контроллер пользователя
    if not controller.is_running:
        await message.answer("Автокликалка уже остановлена.")
        return

    await controller.stop_lesson(user_id)
    await message.answer("Автокликалка остановлена.")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    if user_id not in controllers:
        await message.answer("Сначала авторизуйтесь с помощью /login.")
        return

    controller = controllers[user_id]  # Используем контроллер пользователя
    status = await controller.get_status()
    await message.answer(status)

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
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
        timetable = await api.get_timetable(week_offset=week_offset)
        
        # Форматируем расписание
        formatted_timetable = format_timetable(timetable)
        
        # Обновляем инлайн-кнопки
        reply_markup = get_week_navigation_buttons(week_offset=week_offset)
        
        # Редактируем сообщение с новым расписанием и кнопками
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        
        # Подтверждаем обработку callback
        await callback_query.answer()
    
    except Exception as e:
        await callback_query.answer(f"Ошибка: {e}", show_alert=True)
# Добавим команду /timetable для получения расписания
@dp.message(Command("timetable"))
async def cmd_timetable(message: types.Message):
    user_id = message.from_user.id
    if user_id not in controllers:  # Проверяем, есть ли контроллер для пользователя
        await message.answer("Сначала авторизуйтесь с помощью /login.")
        return

    controller = controllers[user_id]  # Используем контроллер пользователя
    try:
        # Получаем расписание для текущей недели
        timetable = await controller.api.get_timetable(week_offset=0)
        
        # Форматируем расписание
        formatted_timetable = format_timetable(timetable)
        
        # Добавляем инлайн-кнопки
        reply_markup = get_week_navigation_buttons(week_offset=0)
        
        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
    
    except Exception as e:
        await message.answer(f"Ошибка при получении расписания: {e}")

async def auto_login_user(user_id):
    cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if result:
        email, password = result
        try:
            await api.login(email, password)
            controllers[user_id] = LessonController(api)  # Создаем контроллер для пользователя
            logging.info(f"Пользователь {user_id} автоматически авторизован.")
        except Exception as e:
            logging.error(f"Ошибка автоматической авторизации для пользователя {user_id}: {e}")

async def auto_start_lesson(user_id):
    """
    Автоматически запускает автокликалку для пользователя, если она была активна.
    """
    if user_id in controllers:  # Проверяем, есть ли контроллер для пользователя
        controller = controllers[user_id]
        if not controller.is_running:  # Если автокликалка не запущена, запускаем её
            controller.task = asyncio.create_task(controller.start_lesson())
            logging.info(f"Автокликалка автоматически запущена для пользователя {user_id}.")
        
        
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