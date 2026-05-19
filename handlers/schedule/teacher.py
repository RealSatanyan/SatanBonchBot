"""Обработчики расписания: расписание преподавателя.

Поддомен пакета handlers.schedule (разрез A.2b из единого handlers/schedule.py).
Команды /teacher_timetable и /teachers, навигация по неделям, пункт меню и
FSM-диалог ввода фамилии преподавателя. Поведение хэндлеров не менялось —
только перенос в свой файл со своим Router().

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
from keyboards import cancel_kb, get_teacher_week_navigation_buttons
from lk_client import TimetableBonchAPI
import timetable_service
from timetable_service import get_all_groups_timetable
from formatting import format_timetable_dict

router = Router()


@router.callback_query(F.data.startswith("prev_teacher_week_") | F.data.startswith("next_teacher_week_") | F.data.startswith("all_teacher_weeks_"))
async def process_teacher_week_navigation(callback_query: CallbackQuery):
    """
    Обработчик переключения недель для расписания преподавателя.
    """
    callback_data = callback_query.data

    try:
        import base64

        if callback_data.startswith("prev_teacher_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            teacher_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("next_teacher_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            teacher_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("all_teacher_weeks_"):
            encoded_name = callback_data.replace("all_teacher_weeks_", "")
            teacher_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
            week_number = None
        else:
            await callback_query.answer("Неизвестная команда", show_alert=True)
            return

        # Получаем расписание всех групп
        if timetable_service.all_groups_timetable_cache is None:
            await callback_query.answer("Расписание еще не загружено. Используйте команду /teacher_timetable", show_alert=True)
            return

        # Фильтруем по преподавателю
        teacher_timetable = TimetableBonchAPI.teacher_timetable(timetable_service.all_groups_timetable_cache, teacher_name)

        if not teacher_timetable:
            await callback_query.answer(f"Не найдено занятий для преподавателя: {teacher_name}", show_alert=True)
            return

        # Форматируем расписание
        formatted_timetable = format_timetable_dict(teacher_timetable, f"Расписание преподавателя: {teacher_name}", week_number=week_number)

        # Обновляем кнопки
        reply_markup = get_teacher_week_navigation_buttons(teacher_name, week_number)

        # Редактируем сообщение
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        await callback_query.answer()

    except Exception as e:
        logging.error(f"Ошибка при переключении недели преподавателя: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось выполнить действие. Попробуй позже.", show_alert=True)


@router.message(Command("teacher_timetable"))
async def cmd_teacher_timetable(message: types.Message, override: str = None):
    """
    Команда для получения расписания преподавателя.
    Использование: /teacher_timetable <Фамилия преподавателя>
    """
    if override is not None:
        teacher_name = override
    else:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Используйте: /teacher_timetable <Фамилия преподавателя>\n\n"
                "Пример: /teacher_timetable Иванов"
            )
            return
        teacher_name = args[1]
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил расписание преподавателя: {teacher_name}")

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

        # Используем статический метод для фильтрации по преподавателю
        teacher_timetable = TimetableBonchAPI.teacher_timetable(all_timetable, teacher_name)

        if not teacher_timetable:
            await message.answer(f"❌ Не найдено занятий для преподавателя: {teacher_name}")
            return

        # Определяем текущую неделю (первая неделя с занятиями или текущая)
        weeks = sorted(set(lesson.get('Номер недели', 0) for lesson in teacher_timetable))
        current_week = weeks[0] if weeks else None

        formatted_timetable = format_timetable_dict(teacher_timetable, f"Расписание преподавателя: {teacher_name}", week_number=current_week)
        reply_markup = get_teacher_week_navigation_buttons(teacher_name, current_week)
        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logging.error(f"Ошибка при получении расписания преподавателя: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")


@router.message(Command("teachers"))
async def cmd_teachers(message: types.Message):
    """
    Команда для получения списка преподавателей из расписания.
    """
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил список преподавателей (/teachers)")

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

        # Извлекаем уникальных преподавателей из расписания
        teachers_set = set()
        for group_name, lessons in all_timetable.items():
            for lesson in lessons:
                teacher = lesson.get('ФИО преподавателя')
                if teacher:
                    # Разделяем преподавателей по точке с запятой, если их несколько
                    for t in teacher.split(';'):
                        teachers_set.add(t.strip())

        if not teachers_set:
            await message.answer("❌ Не найдено преподавателей в расписании")
            return

        teachers_list = sorted(list(teachers_set))
        logging.info(f"Найдено {len(teachers_list)} преподавателей")

        # Формируем список для отправки
        teachers_text = f"👤 Список преподавателей ({len(teachers_list)}):\n\n"
        for teacher in teachers_list[:100]:  # Показываем первые 100
            teachers_text += f"• {teacher}\n"

        if len(teachers_list) > 100:
            teachers_text += f"\n... и еще {len(teachers_list) - 100} преподавателей"

        await message.answer(teachers_text)

    except Exception as e:
        logging.error(f"Ошибка при получении списка преподавателей для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось получить список преподавателей. Попробуй позже.")


@router.callback_query(F.data == "m:sched:teacher")
async def cb_sched_teacher(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_teacher)
    await callback_query.message.answer(
        "🧑‍🏫 Введи фамилию преподавателя (например: Иванов):",
        reply_markup=cancel_kb(),
    )


@router.message(UIStates.ask_teacher)
async def fsm_ask_teacher(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_teacher_timetable(message, override=(message.text or "").strip())
