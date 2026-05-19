"""Обработчики aiogram: профиль (настройки уведомлений, повторный вход, выход).

Извлечено из main.py (задача 4.1, шаг 12e). Поведение хэндлеров не менялось —
только декоратор @dp.* → @router.* и импорты из извлечённых модулей.
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from satanbonchbot.states import  UIStates
from satanbonchbot.keyboards import  (
    cancel_kb,
    main_menu_kb,
    notify_settings_text,
    notify_settings_kb,
    subs_settings_text,
    subs_settings_kb,
)
from satanbonchbot.db import  (
    get_notify_settings,
    set_notify_enabled,
    set_notify_minutes,
    get_notify_schedule_enabled,
    set_notify_schedule_enabled,
    get_notify_messages_enabled,
    set_notify_messages_enabled,
)
from satanbonchbot import db
from satanbonchbot import lk_client
from satanbonchbot import lesson_controller

router = Router()


@router.callback_query(F.data == "m:profile:notify")
async def cb_notify_settings(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.answer()
    user_id = callback_query.from_user.id
    enabled, minutes = get_notify_settings(user_id)
    await callback_query.message.answer(
        notify_settings_text(enabled, minutes),
        reply_markup=notify_settings_kb(enabled, minutes),
    )


@router.callback_query(F.data == "m:notify:toggle")
async def cb_notify_toggle(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    enabled, minutes = get_notify_settings(user_id)
    new_enabled = not enabled
    set_notify_enabled(user_id, new_enabled)
    await callback_query.answer("Уведомления включены" if new_enabled else "Уведомления выключены")
    await callback_query.message.edit_text(
        notify_settings_text(new_enabled, minutes),
        reply_markup=notify_settings_kb(new_enabled, minutes),
    )


@router.callback_query(F.data.startswith("m:notify:min:"))
async def cb_notify_minutes(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    try:
        minutes = int(callback_query.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback_query.answer()
        return
    set_notify_minutes(user_id, minutes)
    enabled, _ = get_notify_settings(user_id)
    await callback_query.answer(f"Буду предупреждать за {minutes} мин")
    await callback_query.message.edit_text(
        notify_settings_text(enabled, minutes),
        reply_markup=notify_settings_kb(enabled, minutes),
    )


@router.callback_query(F.data == "m:profile:subs")
async def cb_subs_settings(callback_query: CallbackQuery, state: FSMContext):
    """Экран тумблеров уведомлений бота — изменения расписания / сообщения ЛК (B.1)."""
    await state.clear()
    await callback_query.answer()
    user_id = callback_query.from_user.id
    schedule_on = get_notify_schedule_enabled(user_id)
    messages_on = get_notify_messages_enabled(user_id)
    await callback_query.message.answer(
        subs_settings_text(schedule_on, messages_on),
        reply_markup=subs_settings_kb(schedule_on, messages_on),
    )


@router.callback_query(F.data.startswith("m:subs:toggle:"))
async def cb_subs_toggle(callback_query: CallbackQuery, state: FSMContext):
    """Переключает один из тумблеров уведомлений бота (B.1)."""
    user_id = callback_query.from_user.id
    which = callback_query.data.rsplit(":", 1)[-1]
    if which == "schedule":
        set_notify_schedule_enabled(user_id, not get_notify_schedule_enabled(user_id))
    elif which == "messages":
        set_notify_messages_enabled(user_id, not get_notify_messages_enabled(user_id))
    else:
        await callback_query.answer()
        return
    schedule_on = get_notify_schedule_enabled(user_id)
    messages_on = get_notify_messages_enabled(user_id)
    await callback_query.answer("Готово")
    await callback_query.message.edit_text(
        subs_settings_text(schedule_on, messages_on),
        reply_markup=subs_settings_kb(schedule_on, messages_on),
    )


@router.callback_query(F.data == "m:profile:relogin")
async def cb_relogin(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.login_email)
    await callback_query.message.answer(
        "🔄 Повторный вход. Введи email (логин от ЛК):",
        reply_markup=cancel_kb(),
    )


@router.callback_query(F.data == "m:profile:logout")
async def cb_logout(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    controller = lesson_controller.controllers.pop(user_id, None)
    if controller is not None and getattr(controller, "is_running", False):
        try:
            await controller.stop_lesson(user_id)
        except Exception:
            pass
    lk_client.apis.pop(user_id, None)
    with db.conn:
        db.cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
    await callback_query.answer("Вы вышли")
    await callback_query.message.answer(
        "🚪 Ты вышел из личного кабинета, сохранённые данные удалены.\n"
        "Расписание по-прежнему доступно без входа.",
        reply_markup=main_menu_kb(),
    )
