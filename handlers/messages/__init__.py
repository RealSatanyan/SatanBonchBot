"""Пакет обработчиков сообщений ЛК.

Разрезан из единого handlers/messages.py (задача C.1) по поддоменам — каждый
файл заводит свой Router(). ``handlers.messages.router`` агрегирует их,
поэтому внешний интерфейс пакета не изменился: handlers/__init__.py по-прежнему
импортирует messages, main.py делает include_router(messages.router) на том же
месте — precedence относительно других пакетов не сдвинулась.

Хэндлеры взаимоисключающие — у каждого уникальный фильтр (своя команда,
префикс callback или FSM-состояние), поэтому порядок include саброутеров на
маршрутизацию не влияет.

Поддомены:
- inbox   — чтение входящих ЛК (/messages, навигация, открытие сообщения)
- compose — отправка сообщения ЛК (получатель, тема, текст, вложение)
"""

from aiogram import Router

from handlers.messages import inbox, compose

router = Router()
router.include_router(inbox.router)
router.include_router(compose.router)

__all__ = ["router"]
