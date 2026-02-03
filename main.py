import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiohttp
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
from PIL import Image, ImageDraw, ImageFont
import pytz
import os
import sys
from aiogram.types import BotCommand
from typing import Optional


class DebuggableBonchAPI(BonchAPI):
    """
    Расширяет стандартный BonchAPI подробными логами при клике.
    """

    async def click_start_lesson(self):
        URL = "https://lk.sut.ru/cabinet/project/cabinet/forms/raspisanie.php"

        timetable = await self.get_raw_timetable()
        
        # Проверяем, не является ли ответ редиректом на login=no (истекшая сессия)
        if timetable and ("login=no" in timetable or "index.php?login=no" in timetable):
            raise ValueError("Session expired - redirect to login=no. Need to re-authenticate.")
        
        week = await parser.get_week(timetable)
        lesson_ids = await parser.get_lesson_id(timetable)
        logging.debug("Неделя %s, найдено %s кандидат(ов) для клика: %s", week, len(lesson_ids), lesson_ids)

        for lesson_id in lesson_ids:
            data = {"open": 1, "rasp": lesson_id, "week": week}

            async with aiohttp.ClientSession() as session:
                async with session.post(URL, cookies=self.cookies, data=data) as resp:
                    text = await resp.text()
                    
                    # Проверяем ответ на ошибку авторизации
                    if text and ("login=no" in text or "index.php?login=no" in text):
                        raise ValueError("Session expired during lesson click - redirect to login=no. Need to re-authenticate.")
                    
                    logging.debug(
                        "Ответ на клик урока %s: статус %s, первые 200 символов: %s",
                        lesson_id,
                        resp.status,
                        text[:200],
                    )

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
    def __init__(self, api, bot, user_id):
        self.api = api
        self.bot = bot
        self.user_id = user_id
        self.is_running = False
        self.task = None
        self.notified = False  # Флаг для отслеживания отправки уведомления
        self.debug_dir = Path("debug_dumps")
        self.debug_dir.mkdir(exist_ok=True)

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
                    # Если уведомление еще не отправлено, отправляем его
                    if not self.notified:
                        # await self.bot.send_message(self.user_id, "Скорее всего ты отмечен на паре, если не отметило, то через 10 минут снова попробую (Если у тебя сейчас нет пары, то тебе всё равно придёт оповещение, чуть позже исправлю)")
                        self.notified = True  # Устанавливаем флаг, что уведомление отправлено

                    # Пытаемся выполнить клик
                    logging.debug("Попытка кликнуть занятие для пользователя %s", self.user_id)
                    await self.api.click_start_lesson()
                    logging.info("Клик выполнен.")
                else:
                    # Если время пар закончилось, сбрасываем флаг уведомления
                    self.notified = False
                    logging.info("Сейчас не время пар. Клик не выполнен.")
                await asyncio.sleep(600)  # Пауза между проверками
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
            
            timestamp = datetime.now(pytz.timezone('Europe/Moscow')).strftime("%Y%m%d_%H%M%S")
            file_path = self.debug_dir / f"{self.user_id}_{timestamp}.html"
            file_path.write_text(raw_html, encoding="utf-8")
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
        apis[user_id] = DebuggableBonchAPI()
        await apis[user_id].login(email, password)
        controllers[user_id] = LessonController(apis[user_id], bot, user_id)  # Передаем api, bot и user_id в контроллер
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
            apis[user_id] = DebuggableBonchAPI()
            await apis[user_id].login(email, password)
            controllers[user_id] = LessonController(apis[user_id], bot, user_id)  # Передаем bot и user_id
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
        await auto_login_user(user_id)  # Передаем user_id
        await auto_start_lesson(user_id)  # Запускаем кликалку после авторизации

async def main():
    await on_startup(dp)
    await dp.start_polling(bot)
    logging.info("Этот лог должен выводиться")

if __name__ == "__main__":
    asyncio.run(main())