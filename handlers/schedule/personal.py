"""Обработчики расписания: личное расписание из ЛК.

Поддомен пакета handlers.schedule (разрез A.2b из единого handlers/schedule.py).
Команда /timetable, навигация по неделям, пресеты «Сегодня/Завтра», пункт меню
«Моё расписание». Поведение хэндлеров не менялось — только перенос в свой файл
со своим Router().
"""

import os
import logging
from datetime import timedelta

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile

from keyboards import login_prompt_kb, get_week_navigation_buttons
from db import is_registered, get_user_group
import lk_client
import timetable_service
from formatting import (
    format_timetable,
    filter_personal_lessons_by_date,
    _week_offset_for_date,
    _moscow_today,
)
from rendering import generate_timetable_image
from login_service import auto_login_user

router = Router()


def _user_group_timetable(user_id: int):
    """
    Расписание группы пользователя из кэша — для обогащения личного
    расписания полным ФИО преподавателя (личная страница ЛК их не содержит).
    Возвращает список занятий группы либо None.
    """
    group = get_user_group(user_id)
    if not group:
        return None
    cache = timetable_service.all_groups_timetable_cache
    if not cache:
        return None
    return cache.get(group)


@router.callback_query(F.data.startswith("image_week_"))
async def process_image_week(callback_query: CallbackQuery):
    # Извлекаем смещение недели из callback_data
    week_offset = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id

    if user_id not in lk_client.apis:
        await callback_query.answer("Сначала авторизуйтесь с помощью /login.", show_alert=True)
        return

    try:
        # Получаем расписание для выбранной недели
        timetable = await lk_client.apis[user_id].get_timetable(week_offset=week_offset)

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
        await callback_query.answer("⚠️ Не удалось выполнить действие. Попробуй позже.", show_alert=True)


@router.callback_query(F.data.startswith("prev_week_") | F.data.startswith("next_week_") | F.data.startswith("current_week_"))
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
        if user_id not in lk_client.apis:
            await callback_query.answer("Сначала авторизуйтесь с помощью /login.", show_alert=True)
            return

        timetable = await lk_client.apis[user_id].get_timetable(week_offset=week_offset)

        # Форматируем расписание
        formatted_timetable = format_timetable(
            timetable, group_timetable=_user_group_timetable(user_id)
        )

        # Обновляем инлайн-кнопки
        reply_markup = get_week_navigation_buttons(week_offset=week_offset)

        # Редактируем сообщение с новым расписанием и кнопками
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)

        # Подтверждаем обработку callback
        await callback_query.answer()

    except Exception as e:
        logging.error("Ошибка при переключении недели расписания: %s", e, exc_info=True)
        await callback_query.answer("⚠️ Не удалось переключить неделю. Попробуй позже.", show_alert=True)


@router.callback_query(F.data.startswith("my_day_"))
async def process_my_day(callback_query: CallbackQuery):
    """Пресет «Сегодня» / «Завтра» для личного расписания (offset 0 / 1)."""
    user_id = callback_query.from_user.id
    if user_id not in lk_client.apis:
        await callback_query.answer("Сначала авторизуйтесь с помощью /login.", show_alert=True)
        return
    try:
        offset = int(callback_query.data.split("_")[2])
        today = _moscow_today()
        target = today + timedelta(days=offset)
        week_offset = _week_offset_for_date(target, today)

        timetable = await lk_client.apis[user_id].get_timetable(week_offset=week_offset)
        day_lessons = filter_personal_lessons_by_date(timetable, target.strftime("%Y-%m-%d"))
        label = "Сегодня" if offset == 0 else "Завтра"
        title = f"{label} ({target.strftime('%d.%m')})"

        await callback_query.message.edit_text(
            format_timetable(
                day_lessons, title=title,
                group_timetable=_user_group_timetable(user_id),
            ),
            parse_mode="Markdown",
            reply_markup=get_week_navigation_buttons(week_offset=0),
        )
        await callback_query.answer()
    except Exception as e:
        logging.error("Ошибка пресета личного расписания: %s", e, exc_info=True)
        await callback_query.answer("⚠️ Не удалось показать расписание. Попробуй позже.", show_alert=True)


@router.message(Command("timetable"))
async def cmd_timetable(message: types.Message, uid: int = None):
    user_id = uid if uid is not None else message.from_user.id
    if user_id not in lk_client.apis:  # Проверяем, есть ли api для пользователя
        # Пытаемся автоматически авторизовать пользователя, если он есть в БД
        success = await auto_login_user(user_id)
        if not success or user_id not in lk_client.apis:
            await message.answer("Сначала авторизуйтесь с помощью /login. Если вы уже авторизованы, попробуйте выполнить /login еще раз.")
            return

    try:
        # Получаем расписание для текущей недели
        timetable = await lk_client.apis[user_id].get_timetable(week_offset=0)

        # Форматируем расписание
        formatted_timetable = format_timetable(
            timetable, group_timetable=_user_group_timetable(user_id)
        )

        # Добавляем инлайн-кнопки
        reply_markup = get_week_navigation_buttons(week_offset=0)

        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logging.error("Ошибка при получении расписания: %s", e, exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")


@router.callback_query(F.data == "m:sched:my")
async def cb_sched_my(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    if not is_registered(user_id):
        await callback_query.answer()
        await callback_query.message.answer("Сначала войди в ЛК.", reply_markup=login_prompt_kb())
        return
    await callback_query.answer("Загружаю...")
    await cmd_timetable(callback_query.message, uid=user_id)
