import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from bonchapi import BonchAPI  # Импортируем ваш API
import sqlite3
from contextlib import closing
from dotenv import load_dotenv
from datetime import datetime, time, timedelta
from aiogram.types import InputFile
from aiogram.types import FSInputFile
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from PIL import Image, ImageDraw, ImageFont
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

controllers = {}  # Словарь для хранения контроллеров
apis = {}  # Словарь для хранения экземпляров BonchAPI

class LessonController:
    def __init__(self, api):
        self.api = api
        self.is_running = False
        self.task = None

        # Интервалы пар (начало и конец)
        self.lesson_intervals = [
            (time(9, 0), time(10, 35)),   # 1 пара
            (time(10, 45), time(12, 20)),  # 2 пара
            (time(13, 0), time(14, 35)),   # 3 пара
            (time(14, 45), time(16, 20)),  # 4 пара
            (time(16, 30), time(18, 5)),   # 5 пара
            (time(18, 15), time(19, 50)), # 6 пара
            (time(20, 0), time(21, 35))   # 7 пара
        ]

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
                now = datetime.now(moscow_tz).time()
                if self.is_lesson_time(now):
                    await self.api.click_start_lesson()
                    logging.info("Клик выполнен.")
                else:
                    logging.info("Сейчас не время пар. Клик не выполнен.")
                await asyncio.sleep(1200)  # Пауза между проверками
            except Exception as e:
                logging.error(f"Ошибка при выполнении клика: {e}", exc_info=True)
                await asyncio.sleep(1200)  # Пауза перед повторной попыткой
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

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Этот бот что то типо моей вариации BonchBot."
    )

@dp.message(Command("login"))
async def cmd_login(message: types.Message):
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Используйте: /login <email> <password>")
            return

        email, password = args[1], args[2]
        user_id = message.from_user.id

        # Удаляем сообщение пользователя с данными
        await message.delete()

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

        # Создаем новый экземпляр BonchAPI для пользователя
        apis[user_id] = BonchAPI()
        await apis[user_id].login(email, password)
        controllers[user_id] = LessonController(apis[user_id])  # Передаем api пользователя в контроллер
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
    x = 10
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

    for date, lessons in sorted_days:
        # Заголовок дня
        draw_text_with_emoji(draw, f"{date} ({lessons[0].day})", x, y, text_font, emoji_font)
        y += 30

        # Отображаем все занятия подряд
        for lesson in lessons:
            # Информация о занятии
            lesson_info = (
                f"⏰ {lesson.time}\n"
                f"📚 {lesson.subject}\n"
                f"🎓 {lesson.teacher}\n"
                f"🏫 {lesson.location}\n"
                f"🔹 Тип: {lesson.lesson_type}\n"
            )
            draw_text_with_emoji(draw, lesson_info, x, y, text_font, emoji_font)
            y += 40  # Отступ между занятиями

        # Разделитель между днями
        draw.line((10, y, width - 10, y), fill=(0, 0, 0), width=2)
        y += 20

    # Сохраняем изображение
    image_path = "timetable.png"
    logging.info(f"Изображение успешно сохранено по пути: {image_path}")
    image.save(image_path)
    return image_path

def draw_text_with_emoji(draw, text, x, y, text_font, emoji_font):
    """
    Рисует текст с эмодзи, используя разные шрифты.
    :param draw: Объект ImageDraw.
    :param text: Текст для отрисовки.
    :param x: Начальная координата X.
    :param y: Начальная координата Y.
    :param text_font: Шрифт для текста.
    :param emoji_font: Шрифт для эмодзи.
    """
    current_x = x
    for char in text:
        if ord(char) > 0xFFFF:  # Проверяем, является ли символ эмодзи
            font = emoji_font
        else:
            font = text_font
        draw.text((current_x, y), char, fill=(0, 0, 0), font=font)
        current_x += font.getlength(char)  # Обновляем позицию X

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
        await callback_query.answer(f"Ошибка: {e}", show_alert=True)
        
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
        await callback_query.answer(f"Ошибка: {e}", show_alert=True)

@dp.message(Command("timetable"))
async def cmd_timetable(message: types.Message):
    user_id = message.from_user.id
    if user_id not in apis:  # Проверяем, есть ли api для пользователя
        await message.answer("Сначала авторизуйтесь с помощью /login.")
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
        await message.answer(f"Ошибка при получении расписания: {e}")

async def auto_login_user(user_id):
    cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if result:
        email, password = result
        try:
            apis[user_id] = BonchAPI()
            await apis[user_id].login(email, password)
            controllers[user_id] = LessonController(apis[user_id])  # Создаем контроллер для пользователя
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