"""Ядро бота: экземпляры aiogram Bot и Dispatcher.

Извлечено из main.py (задача 4.1, декомпозиция). Листовой модуль —
импортируется обработчиками и сервисами, чтобы те не зависели от main.py.

Telegram-сессия создаётся БЕЗ прокси (прокси нужен только для запросов в ЛК).
"""
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from config import BOT_TOKEN

# Telegram-сессия БЕЗ прокси.
tg_session = AiohttpSession()

bot = Bot(token=BOT_TOKEN, session=tg_session)
dp = Dispatcher()
