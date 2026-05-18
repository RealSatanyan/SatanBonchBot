"""Обработчики aiogram: автоотметка занятий (команды LessonController + меню).

Извлечено из main.py (задача 4.1, шаг 12e). Содержит хэндлер-хелпер
send_autoclick_panel, используемый этим доменом и handlers/common.py.
Поведение хэндлеров не менялось — только декоратор @dp.* → @router.* и импорты.
"""

import asyncio
import logging

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from botcore import bot
from keyboards import login_prompt_kb, autoclick_menu_kb
from db import set_autoclick_enabled
import db
import lk_client
import lesson_controller
from login_service import auto_login_user

router = Router()


async def send_autoclick_panel(user_id: int, chat_id: int):
    """Показывает панель автоотметки со статусом и кнопками."""
    if user_id not in lesson_controller.controllers:
        await auto_login_user(user_id)
    if user_id not in lesson_controller.controllers:
        await bot.send_message(
            chat_id,
            "Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return
    controller = lesson_controller.controllers[user_id]
    status_text = await controller.get_status()
    await bot.send_message(
        chat_id,
        f"✅ Автоотметка\n\n{status_text}",
        reply_markup=autoclick_menu_kb(controller.is_running),
    )


@router.message(Command("start_lesson"))
async def cmd_start_lesson(message: types.Message):
    user_id = message.from_user.id
    if user_id not in lesson_controller.controllers:
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in lesson_controller.controllers:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    controller = lesson_controller.controllers[user_id]  # Используем контроллер пользователя
    if controller.is_running:
        await message.answer("Автокликалка уже запущена.")
        return

    controller.task = asyncio.create_task(controller.start_lesson())
    set_autoclick_enabled(user_id, True)
    await message.answer("Автокликалка запущена.")

@router.message(Command("stop_lesson"))
async def cmd_stop_lesson(message: types.Message):
    user_id = message.from_user.id
    if user_id not in lesson_controller.controllers:
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in lesson_controller.controllers:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    controller = lesson_controller.controllers[user_id]  # Используем контроллер пользователя
    if not controller.is_running:
        await message.answer("Автокликалка уже остановлена.")
        return

    await controller.stop_lesson(user_id)
    set_autoclick_enabled(user_id, False)
    await message.answer("Автокликалка остановлена.")

@router.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    if user_id not in lesson_controller.controllers:
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in lesson_controller.controllers:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    controller = lesson_controller.controllers[user_id]  # Используем контроллер пользователя
    status = await controller.get_status()
    await message.answer(status)

@router.message(Command("test_notify"))
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

@router.message(Command("my_account"))
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
    has_controller_before = user_id in lesson_controller.controllers

    status_parts.append(f"🔑 API авторизован: {'✅ Да' if has_api_before else '❌ Нет'}")
    status_parts.append(f"🎮 Контроллер создан: {'✅ Да' if has_controller_before else '❌ Нет'}")

    # Если пользователь есть в БД, но нет авторизации, пытаемся восстановить
    if not has_api_before or not has_controller_before:
        status_parts.append("\n⚠️ Обнаружена проблема с авторизацией. Попробую восстановить...")
        logging.info(f"Попытка восстановить авторизацию для пользователя {user_id}")
        success = await auto_login_user(user_id)

        # Проверяем состояние ПОСЛЕ попытки восстановления
        has_api_after = user_id in lk_client.apis
        has_controller_after = user_id in lesson_controller.controllers

        if success and has_api_after and has_controller_after:
            status_parts.append("✅ Авторизация восстановлена!")
            status_parts.append(f"🔑 API авторизован: ✅ Да")
            status_parts.append(f"🎮 Контроллер создан: ✅ Да")
        else:
            status_parts.append("❌ Не удалось восстановить авторизацию.")
            status_parts.append("💡 Выполните /login <email> <password> для повторной авторизации.")
            logging.warning(f"Не удалось восстановить авторизацию для пользователя {user_id}. Success: {success}, has_api: {has_api_after}, has_controller: {has_controller_after}")

    # Показываем статус автокликалки, если контроллер есть
    if user_id in lesson_controller.controllers:
        controller = lesson_controller.controllers[user_id]
        status_parts.append(f"⏯️ Автокликалка: {'🟢 Запущена' if controller.is_running else '🔴 Остановлена'}")

    await message.answer("\n".join(status_parts))


# --- Автоотметка (меню) ---

@router.callback_query(F.data.startswith("m:auto:"))
async def cb_autoclick(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    action = callback_query.data.split(":")[2]

    if user_id not in lesson_controller.controllers:
        await auto_login_user(user_id)
    if user_id not in lesson_controller.controllers:
        await callback_query.answer()
        await callback_query.message.answer(
            "Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return

    controller = lesson_controller.controllers[user_id]

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
