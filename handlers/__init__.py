"""Пакет aiogram-обработчиков бота.

Извлечён из main.py (задача 4.1, шаг 12e). Каждый модуль домена заводит свой
aiogram Router; main.py подключает их через dp.include_router() в порядке,
сохраняющем исходную маршрутизацию (common — последним из-за fallback_handler).

Домены:
- common   — старт/login/help/cancel/fallback + пункты reply-меню верхнего уровня
- schedule — расписание (личное/группа/преподаватель/аудитория)
- autoclick — автоотметка занятий (LessonController-команды + меню)
- messages — сообщения ЛК (чтение списка + отправка)
- profile  — профиль (уведомления, повторный вход, выход)
"""

from handlers import common, schedule, autoclick, messages, profile

__all__ = ["common", "schedule", "autoclick", "messages", "profile"]
