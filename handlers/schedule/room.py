"""Обработчики расписания: расписание аудитории.

Поддомен пакета handlers.schedule (разрез A.2b из единого handlers/schedule.py).
Команды /classroom_timetable и /classrooms, навигация по неделям, пункт меню и
FSM-диалог ввода номера аудитории. Поведение хэндлеров не менялось — только
перенос в свой файл со своим Router().

Разделяемое состояние сервиса расписания (all_groups_timetable_cache,
timetable_loading) читается модуль-квалифицированно через timetable_service.*,
т.к. оно переприсваивается на уровне модуля сервиса.
"""

import logging

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from states import UIStates
from keyboards import cancel_kb, get_classroom_week_navigation_buttons
from lk_client import TimetableBonchAPI
import timetable_service
from timetable_service import get_all_groups_timetable
from formatting import format_timetable_dict, merge_lessons_by_groups

router = Router()


@router.callback_query(F.data.startswith("prev_classroom_week_") | F.data.startswith("next_classroom_week_") | F.data.startswith("all_classroom_weeks_"))
async def process_classroom_week_navigation(callback_query: CallbackQuery):
    """
    Обработчик переключения недель для расписания кабинета.
    """
    callback_data = callback_query.data

    try:
        import base64

        if callback_data.startswith("prev_classroom_week_"):
            parts = callback_data.split("_", 3)
            encoded_number = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            classroom_number = base64.b64decode(encoded_number.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("next_classroom_week_"):
            parts = callback_data.split("_", 3)
            encoded_number = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            classroom_number = base64.b64decode(encoded_number.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("all_classroom_weeks_"):
            encoded_number = callback_data.replace("all_classroom_weeks_", "")
            classroom_number = base64.b64decode(encoded_number.encode('utf-8')).decode('utf-8')
            week_number = None
        else:
            await callback_query.answer("Неизвестная команда", show_alert=True)
            return

        # Получаем расписание всех групп
        if timetable_service.all_groups_timetable_cache is None:
            await callback_query.answer("Расписание еще не загружено. Используйте команду /classroom_timetable", show_alert=True)
            return

        # Фильтруем по кабинету
        classroom_timetable = merge_lessons_by_groups(
            TimetableBonchAPI.classroom_timetable(timetable_service.all_groups_timetable_cache, classroom_number)
        )

        if not classroom_timetable:
            await callback_query.answer(f"Не найдено занятий для кабинета: {classroom_number}", show_alert=True)
            return

        # Форматируем расписание
        formatted_timetable = format_timetable_dict(classroom_timetable, f"Расписание кабинета: {classroom_number}", week_number=week_number)

        # Обновляем кнопки
        reply_markup = get_classroom_week_navigation_buttons(classroom_number, week_number)

        # Редактируем сообщение
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        await callback_query.answer()

    except Exception as e:
        logging.error(f"Ошибка при переключении недели кабинета: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось выполнить действие. Попробуй позже.", show_alert=True)


@router.message(Command("classroom_timetable"))
async def cmd_classroom_timetable(message: types.Message, override: str = None):
    """
    Команда для получения расписания кабинета.
    Использование: /classroom_timetable <Номер кабинета>
    """
    if override is not None:
        classroom_number = override
    else:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Используйте: /classroom_timetable <Номер кабинета>\n\n"
                "Пример: /classroom_timetable 101"
            )
            return
        classroom_number = args[1]
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил расписание кабинета: {classroom_number}")

    try:
        # Проверяем наличие кэша
        status_msg = None
        if timetable_service.all_groups_timetable_cache is None:
            if timetable_service.timetable_loading:
                status_msg = await message.answer("⏳ Расписание уже загружается, пожалуйста подождите...")
            else:
                status_msg = await message.answer("⏳ Загружаю расписание всех групп... Это может занять несколько минут.")
            all_timetable = await get_all_groups_timetable(user_id=user_id, progress_message=status_msg)
            # Не удаляем сообщение, так как оно будет обновляться с прогрессом
        else:
            all_timetable = timetable_service.all_groups_timetable_cache
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass

        # Используем статический метод для фильтрации по кабинету
        classroom_timetable = merge_lessons_by_groups(
            TimetableBonchAPI.classroom_timetable(all_timetable, classroom_number)
        )

        if not classroom_timetable:
            await message.answer(f"❌ Не найдено занятий для кабинета: {classroom_number}")
            return

        # Определяем текущую неделю (первая неделя с занятиями или текущая)
        weeks = sorted(set(lesson.get('Номер недели', 0) for lesson in classroom_timetable))
        current_week = weeks[0] if weeks else None

        formatted_timetable = format_timetable_dict(classroom_timetable, f"Расписание кабинета: {classroom_number}", week_number=current_week)
        reply_markup = get_classroom_week_navigation_buttons(classroom_number, current_week)
        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logging.error(f"Ошибка при получении расписания кабинета: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")


@router.message(Command("classrooms"))
async def cmd_classrooms(message: types.Message):
    """
    Команда для получения списка кабинетов из расписания.
    """
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил список кабинетов (/classrooms)")

    try:
        # Проверяем наличие кэша
        status_msg = None
        if timetable_service.all_groups_timetable_cache is None:
            if timetable_service.timetable_loading:
                status_msg = await message.answer("⏳ Расписание уже загружается, пожалуйста подождите...")
            else:
                status_msg = await message.answer("⏳ Загружаю расписание всех групп... Это может занять несколько минут.")
            all_timetable = await get_all_groups_timetable(user_id=user_id, progress_message=status_msg)
            # Не удаляем сообщение, так как оно будет обновляться с прогрессом
        else:
            all_timetable = timetable_service.all_groups_timetable_cache
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass

        # Извлекаем уникальные кабинеты из расписания
        classrooms_set = set()
        for group_name, lessons in all_timetable.items():
            for lesson in lessons:
                room = lesson.get('Номер кабинета')
                if room:
                    classrooms_set.add(room.strip())

        if not classrooms_set:
            await message.answer("❌ Не найдено кабинетов в расписании")
            return

        classrooms_list = sorted(list(classrooms_set))
        logging.info(f"Найдено {len(classrooms_list)} кабинетов")

        # Формируем список для отправки
        classrooms_text = f"🏫 Список кабинетов ({len(classrooms_list)}):\n\n"
        for classroom in classrooms_list[:100]:  # Показываем первые 100
            classrooms_text += f"• {classroom}\n"

        if len(classrooms_list) > 100:
            classrooms_text += f"\n... и еще {len(classrooms_list) - 100} кабинетов"

        await message.answer(classrooms_text)

    except Exception as e:
        logging.error(f"Ошибка при получении списка кабинетов для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось получить список аудиторий. Попробуй позже.")


@router.callback_query(F.data == "m:sched:room")
async def cb_sched_room(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_classroom)
    await callback_query.message.answer(
        "🚪 Введи номер аудитории (например: 401):",
        reply_markup=cancel_kb(),
    )


@router.message(UIStates.ask_classroom)
async def fsm_ask_classroom(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_classroom_timetable(message, override=(message.text or "").strip())
