"""Обработчики aiogram: старт/онбординг/login/help/cancel/fallback и пункты
reply-меню верхнего уровня.

Извлечено из main.py (задача 4.1, шаг 12e). Каждый файл handlers/ заводит свой
Router(); main.py включает их через dp.include_router(). Поведение хэндлеров не
менялось — только декоратор @dp.* → @router.* и импорты из извлечённых модулей.

Роутер common.router включается ПОСЛЕДНИМ — из-за fallback_handler (@router.message()
без фильтра), который обязан проверяться после всех остальных хэндлеров.
"""

import logging

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from botcore import bot
from states import UIStates
from keyboards import (
    BTN_SCHEDULE,
    BTN_AUTOCLICK,
    BTN_MESSAGES,
    BTN_PROFILE,
    BTN_HELP,
    HELP_TEXT,
    main_menu_kb,
    login_prompt_kb,
    cancel_kb,
    schedule_menu_kb,
    messages_menu_kb,
    profile_menu_kb,
)
from db import is_registered, get_notify_settings
import db
import lk_client
from timetable_cache import _format_cache_age, _timetable_cache_age_now
from login_service import (
    perform_login,
    parse_login_credentials,
    EMAIL_RE,
)
from security import check_login_rate_limit, format_retry_after
from handlers.autoclick import send_autoclick_panel

router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if is_registered(user_id):
        await message.answer(
            "👋 С возвращением!\n\nВыбери раздел в меню снизу 👇",
            reply_markup=main_menu_kb(),
        )
        return
    await message.answer(
        "👋 Привет! Я SatanBonchBot — помощник студента СПбГУТ.\n\n"
        "Что я умею:\n"
        "📅 Расписание — групп, преподавателей и аудиторий\n"
        "✅ Автоотметка — сам отмечаю тебя на парах в ЛК\n"
        "✉️ Сообщения ЛК — читать и отправлять\n"
        "🔔 Уведомления о начале пар\n\n"
        "Расписание доступно сразу — жми «📅 Расписание» в меню снизу.\n"
        "Для автоотметки, личного расписания и сообщений нужно войти "
        "в личный кабинет СПбГУТ.",
        reply_markup=main_menu_kb(),
    )
    await message.answer(
        "Подключить личный кабинет?",
        reply_markup=login_prompt_kb(),
    )

@router.message(Command("login"))
async def cmd_login(message: types.Message, state: FSMContext):
    await state.clear()
    parsed = parse_login_credentials(message.text or "")
    try:
        await message.delete()
    except Exception:
        pass
    if not parsed:
        await message.answer(
            "Чтобы войти в ЛК, нажми кнопку ниже.\n"
            "Либо отправь одной строкой: /login <email> <пароль>",
            reply_markup=login_prompt_kb(),
        )
        return
    email, password = parsed
    retry_after = check_login_rate_limit(message.from_user.id)
    if retry_after:
        await message.answer(
            f"⏳ Слишком много попыток входа. Попробуй снова через {format_retry_after(retry_after)}.",
            reply_markup=login_prompt_kb(),
        )
        return
    status = await message.answer("⏳ Вхожу в ЛК...")
    ok = await perform_login(message.from_user.id, email, password)
    if ok:
        try:
            await status.edit_text("✅ Готово! Ты вошёл в личный кабинет.")
        except Exception:
            pass
        await message.answer("Меню — снизу 👇", reply_markup=main_menu_kb())
    else:
        try:
            await status.edit_text("❌ Не удалось войти. Проверь email и пароль.")
        except Exception:
            pass


# --- Кнопки главного меню (reply-клавиатура) ---

@router.message(F.text == BTN_SCHEDULE)
async def menu_schedule(message: types.Message, state: FSMContext):
    await state.clear()
    age = _format_cache_age(_timetable_cache_age_now())
    await message.answer(
        f"📅 Чьё расписание показать?\n\n🗂 Расписание групп обновлено: {age}",
        reply_markup=schedule_menu_kb(is_registered(message.from_user.id)),
    )


@router.message(F.text == BTN_AUTOCLICK)
async def menu_autoclick(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if not is_registered(user_id):
        await message.answer(
            "✅ Автоотметка сама отмечает тебя на парах в ЛК.\n"
            "Чтобы включить — войди в личный кабинет.",
            reply_markup=login_prompt_kb(),
        )
        return
    await send_autoclick_panel(user_id, message.chat.id)


@router.message(F.text == BTN_MESSAGES)
async def menu_messages(message: types.Message, state: FSMContext):
    await state.clear()
    if not is_registered(message.from_user.id):
        await message.answer(
            "✉️ Здесь можно читать и отправлять сообщения в ЛК.\n"
            "Для этого нужно войти в личный кабинет.",
            reply_markup=login_prompt_kb(),
        )
        return
    await message.answer("✉️ Сообщения личного кабинета:", reply_markup=messages_menu_kb())


@router.message(F.text == BTN_PROFILE)
async def menu_profile(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if not is_registered(user_id):
        await message.answer(
            "👤 Ты ещё не вошёл в личный кабинет СПбГУТ.",
            reply_markup=login_prompt_kb(),
        )
        return
    db.cursor.execute('SELECT email FROM users WHERE user_id = ?', (user_id,))
    row = db.cursor.fetchone()
    email = row[0] if row else "—"
    active = "🟢 активен" if user_id in lk_client.apis else "🟡 восстановится при первом действии"
    notify_enabled, notify_minutes = get_notify_settings(user_id)
    notify_line = (
        f"🔔 Уведомления: за {notify_minutes} мин до пары"
        if notify_enabled
        else "🔕 Уведомления: выключены"
    )
    await message.answer(
        f"👤 Профиль\n\n📧 Email: {email}\n🔑 Вход в ЛК: {active}\n{notify_line}",
        reply_markup=profile_menu_kb(),
    )


@router.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=main_menu_kb())


@router.message(F.text == BTN_HELP)
async def menu_help(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, parse_mode="HTML")


# --- /cancel и отмена диалога ---

@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    had_state = await state.get_state()
    await state.clear()
    await message.answer(
        "Окей, отменил." if had_state else "Сейчас нечего отменять.",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "m:cancel")
async def cb_cancel(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.answer("Отменено")
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback_query.message.answer("Окей. Меню — снизу 👇", reply_markup=main_menu_kb())


# --- Вход в ЛК ---

@router.callback_query(F.data == "m:login")
async def cb_login(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.login_email)
    await callback_query.message.answer(
        "🔑 Вход в личный кабинет СПбГУТ.\n\nВведи свой email (логин от ЛК):",
        reply_markup=cancel_kb(),
    )


@router.message(UIStates.login_email)
async def fsm_login_email(message: types.Message, state: FSMContext):
    email = (message.text or "").strip()
    if not EMAIL_RE.match(email):
        await message.answer(
            "Это не похоже на email. Введи корректный адрес, например ivan@mail.ru:",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(email=email)
    await state.set_state(UIStates.login_password)
    await message.answer("Принято. Теперь введи пароль от ЛК:", reply_markup=cancel_kb())


@router.message(UIStates.login_password)
async def fsm_login_password(message: types.Message, state: FSMContext):
    password = message.text or ""
    data = await state.get_data()
    email = data.get("email", "")
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    retry_after = check_login_rate_limit(message.from_user.id)
    if retry_after:
        await message.answer(
            f"⏳ Слишком много попыток входа. Попробуй снова через {format_retry_after(retry_after)}.",
            reply_markup=login_prompt_kb(),
        )
        return
    status = await message.answer("⏳ Вхожу в ЛК...")
    ok = await perform_login(message.from_user.id, email, password)
    if ok:
        try:
            await status.edit_text("✅ Готово! Ты вошёл в личный кабинет.")
        except Exception:
            pass
        await message.answer("Теперь доступны все разделы 👇", reply_markup=main_menu_kb())
    else:
        try:
            await status.edit_text("❌ Не удалось войти. Проверь email и пароль.")
        except Exception:
            pass
        await message.answer("Попробовать ещё раз?", reply_markup=login_prompt_kb())


# --- Подсказка на нераспознанный ввод ---
# fallback_handler (@router.message() без фильтра) обязан быть последним
# хэндлером этого роутера, а common.router включается последним из всех.

@router.message()
async def fallback_handler(message: types.Message):
    await message.answer(
        "Не понял 🤔 Пользуйся кнопками меню снизу 👇",
        reply_markup=main_menu_kb(),
    )
