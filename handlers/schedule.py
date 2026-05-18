"""Обработчики aiogram: расписание (личное/группа/преподаватель/аудитория).

Извлечено из main.py (задача 4.1, шаг 12e). Команды расписания и навигационные
коллбэки, а также FSM-хэндлеры пошаговых диалогов ввода группы/преподавателя/
аудитории. Поведение хэндлеров не менялось — только декоратор @dp.* → @router.*
и импорты из извлечённых модулей.

Разделяемое состояние сервиса расписания (all_groups_timetable_cache,
timetable_loading) читается модуль-квалифицированно через timetable_service.*,
т.к. оно переприсваивается на уровне модуля сервиса.
"""

import os
import logging
from datetime import timedelta

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile

from states import UIStates
from keyboards import (
    login_prompt_kb,
    cancel_kb,
    get_week_navigation_buttons,
    get_teacher_week_navigation_buttons,
    get_classroom_week_navigation_buttons,
    get_group_week_navigation_buttons,
)
from db import is_registered
import lk_client
from lk_client import TimetableBonchAPI, get_timetable_api
import timetable_service
from timetable_service import get_all_groups_timetable
from formatting import (
    format_timetable,
    format_timetable_dict,
    filter_group_lessons_by_date,
    filter_personal_lessons_by_date,
    _week_offset_for_date,
    _moscow_today,
)
from rendering import generate_timetable_image, generate_timetable_image_from_dict
from login_service import auto_login_user

router = Router()


# --- Пресеты расписания «Сегодня» / «Завтра» ---------------------------------

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
        classroom_timetable = TimetableBonchAPI.classroom_timetable(timetable_service.all_groups_timetable_cache, classroom_number)

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

@router.callback_query(F.data.startswith("prev_group_week_") | F.data.startswith("next_group_week_") | F.data.startswith("image_group_week_"))
async def process_group_week_navigation(callback_query: CallbackQuery):
    """
    Обработчик переключения недель для расписания группы и генерации изображений.
    """
    callback_data = callback_query.data

    try:
        import base64

        if callback_data.startswith("prev_group_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            group_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("next_group_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            group_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        elif callback_data.startswith("image_group_week_"):
            parts = callback_data.split("_", 3)
            encoded_name = parts[3].rsplit("_", 1)[0]
            week_number = int(parts[3].rsplit("_", 1)[1])
            group_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')
        else:
            await callback_query.answer("Неизвестная команда", show_alert=True)
            return

        # Получаем расписание всех групп
        if timetable_service.all_groups_timetable_cache is None:
            await callback_query.answer("Расписание еще не загружено. Используйте команду /group_timetable", show_alert=True)
            return

        # Получаем расписание группы
        if group_name not in timetable_service.all_groups_timetable_cache:
            await callback_query.answer(f"Группа '{group_name}' не найдена в расписании", show_alert=True)
            return

        timetable = timetable_service.all_groups_timetable_cache[group_name]

        if not timetable or isinstance(timetable, str):
            await callback_query.answer(f"Расписание для группы '{group_name}' недоступно", show_alert=True)
            return

        # Если это запрос на генерацию изображения
        if callback_data.startswith("image_group_week_"):
            await callback_query.answer("⏳ Генерирую изображение...")
            try:
                # Генерируем изображение для текущей недели
                image_path = generate_timetable_image_from_dict(
                    timetable,
                    f"Расписание группы {group_name}",
                    week_number=week_number,
                    group_name=group_name
                )

                # Проверяем, что файл существует
                if os.path.exists(image_path):
                    photo = FSInputFile(image_path)
                    await callback_query.message.answer_photo(
                        photo,
                        caption=f"📅 Расписание группы {group_name} (Неделя №{week_number})"
                    )
                    # Удаляем временный файл после отправки
                    try:
                        os.remove(image_path)
                    except Exception as e:
                        logging.warning(f"Не удалось удалить временный файл {image_path}: {e}")
                    await callback_query.answer("✅ Изображение отправлено")
                else:
                    logging.error(f"Изображение не было создано: {image_path}")
                    await callback_query.answer("❌ Ошибка при генерации изображения", show_alert=True)
            except Exception as e:
                logging.error(f"Ошибка при генерации изображения: {e}", exc_info=True)
                await callback_query.answer("⚠️ Что-то пошло не так. Попробуй позже.", show_alert=True)
            return

        # Форматируем расписание
        formatted_timetable = format_timetable_dict(timetable, f"Расписание группы {group_name}", week_number=week_number)

        # Проверяем длину сообщения (лимит Telegram - 4096 символов)
        max_length = 4000  # Оставляем запас
        reply_markup = get_group_week_navigation_buttons(group_name, week_number)

        # Если сообщение слишком длинное, обрезаем
        if len(formatted_timetable) > max_length:
            formatted_timetable = formatted_timetable[:max_length] + "\n\n... (сообщение обрезано, используйте навигацию по неделям)"

        # Редактируем сообщение
        await callback_query.message.edit_text(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        await callback_query.answer()

    except Exception as e:
        logging.error(f"Ошибка при переключении недели группы: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось выполнить действие. Попробуй позже.", show_alert=True)

@router.callback_query(F.data.startswith("group_day_"))
async def process_group_day(callback_query: CallbackQuery):
    """Пресет «Сегодня» / «Завтра» для расписания группы (offset 0 / 1)."""
    try:
        import base64
        rest = callback_query.data[len("group_day_"):]
        encoded_name, offset_str = rest.rsplit("_", 1)
        offset = int(offset_str)
        group_name = base64.b64decode(encoded_name.encode('utf-8')).decode('utf-8')

        if timetable_service.all_groups_timetable_cache is None or group_name not in timetable_service.all_groups_timetable_cache:
            await callback_query.answer("Расписание группы недоступно.", show_alert=True)
            return

        timetable = timetable_service.all_groups_timetable_cache[group_name]
        if not timetable or isinstance(timetable, str):
            await callback_query.answer(f"Расписание для группы '{group_name}' недоступно", show_alert=True)
            return

        target = _moscow_today() + timedelta(days=offset)
        day_lessons = filter_group_lessons_by_date(timetable, target.strftime("%Y.%m.%d"))
        label = "Сегодня" if offset == 0 else "Завтра"
        title = f"Группа {group_name} — {label} ({target.strftime('%d.%m')})"

        if not day_lessons:
            text = f"📅 {title}\n\nЗанятий не найдено 🎉"
        else:
            text = format_timetable_dict(day_lessons, title)
            if len(text) > 4000:
                text = text[:4000] + "\n\n... (сообщение обрезано)"

        await callback_query.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=get_group_week_navigation_buttons(group_name, 0),
        )
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Ошибка пресета расписания группы: {e}", exc_info=True)
        await callback_query.answer("⚠️ Не удалось показать расписание. Попробуй позже.", show_alert=True)

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
        formatted_timetable = format_timetable(timetable)

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
            format_timetable(day_lessons, title=title),
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
        formatted_timetable = format_timetable(timetable)

        # Добавляем инлайн-кнопки
        reply_markup = get_week_navigation_buttons(week_offset=0)

        await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logging.error("Ошибка при получении расписания: %s", e, exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")

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
        classroom_timetable = TimetableBonchAPI.classroom_timetable(all_timetable, classroom_number)

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

@router.message(Command("groups"))
async def cmd_groups(message: types.Message):
    """
    Команда для получения списка групп.
    """
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил список групп (/groups)")

    try:
        api = await get_timetable_api()

        if not hasattr(api, 'groups_id') or not api.groups_id:
            logging.error(f"Не удалось получить список групп для пользователя {user_id}")
            await message.answer("❌ Не удалось получить список групп. Попробуйте позже.")
            return

        logging.info(f"Формирование списка групп для пользователя {user_id}. Всего групп: {len(api.groups_id)}")
        groups_list = f"👥 Список групп ({len(api.groups_id)}):\n\n"
        for group_id, group_name in list(api.groups_id.items())[:100]:  # Показываем первые 100
            groups_list += f"• {group_name} (ID: {group_id})\n"

        if len(api.groups_id) > 100:
            groups_list += f"\n... и еще {len(api.groups_id) - 100} групп"

        groups_list += "\n\n💡 Используйте /group_timetable <название или ID> для получения расписания"

        await message.answer(groups_list)
        logging.info(f"Список групп успешно отправлен пользователю {user_id}")

    except Exception as e:
        logging.error(f"Ошибка при получении списка групп для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось получить список групп. Попробуй позже.")

@router.message(Command("group_timetable"))
async def cmd_group_timetable(message: types.Message, override: str = None):
    """
    Команда для получения расписания группы.
    Использование: /group_timetable <ID_группы или название группы>
    Использует расписание из загруженного кэша всех групп.
    """
    if override is not None:
        group_input = override
    else:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Используйте: /group_timetable <ID_группы или название группы>\n\n"
                "Пример: /group_timetable ИКПИ-22\n"
                "Или: /group_timetable 12345\n\n"
                "Для получения списка групп используйте: /groups"
            )
            return
        group_input = args[1]
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил расписание группы: {group_input}")

    try:
        # Проверяем наличие кэша расписания всех групп
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

        api = await get_timetable_api()

        # Пытаемся найти группу по ID или названию
        group_id = None
        group_name = None

        # Сначала проверяем, является ли ввод ID
        if group_input.isdigit():
            if hasattr(api, 'groups_id') and group_input in api.groups_id:
                group_id = group_input
                group_name = api.groups_id[group_id]
        else:
            # Ищем по названию группы
            if hasattr(api, 'groups_id'):
                for gid, gname in api.groups_id.items():
                    if group_input.lower() in gname.lower():
                        group_id = gid
                        group_name = gname
                        break

        if not group_id or not group_name:
            await message.answer(f"❌ Группа '{group_input}' не найдена. Используйте /groups для просмотра списка групп.")
            return

        # Получаем расписание группы из загруженного кэша
        if group_name not in all_timetable:
            await message.answer(f"❌ Расписание для группы '{group_name}' не найдено в загруженных данных.")
            return

        timetable = all_timetable[group_name]

        if isinstance(timetable, str):
            logging.warning(f"Ошибка при получении расписания группы {group_id} для пользователя {user_id}: {timetable}")
            await message.answer(f"❌ {timetable}")
            return

        if not timetable:
            await message.answer(f"❌ Расписание для группы '{group_name}' пусто.")
            return

        # Определяем текущую неделю (первая неделя с занятиями или текущая)
        weeks = sorted(set(lesson.get('Номер недели', 0) for lesson in timetable))
        current_week = weeks[0] if weeks else None

        logging.info(f"Расписание группы {group_id} ({group_name}) успешно получено для пользователя {user_id}. Занятий: {len(timetable)}")

        # Форматируем расписание для текущей недели
        formatted_timetable = format_timetable_dict(timetable, f"Расписание группы {group_name}", week_number=current_week)

        # Проверяем длину сообщения (лимит Telegram - 4096 символов)
        max_length = 4000  # Оставляем запас для форматирования
        reply_markup = get_group_week_navigation_buttons(group_name, current_week)

        # Если сообщение слишком длинное, разбиваем на части
        if len(formatted_timetable) > max_length:
            # Пытаемся разбить по дням
            parts = formatted_timetable.split("----------------------")
            if len(parts) > 1:
                current_part = parts[0]  # Заголовок
                for part in parts[1:]:
                    if len(current_part + "----------------------" + part) > max_length:
                        # Отправляем текущую часть
                        await message.answer(current_part, parse_mode="Markdown", reply_markup=reply_markup if current_part == parts[0] else None)
                        current_part = "----------------------" + part
                    else:
                        current_part += "----------------------" + part
                # Отправляем последнюю часть
                if current_part:
                    await message.answer(current_part, parse_mode="Markdown")
            else:
                # Если не удалось разбить, просто обрезаем
                formatted_timetable = formatted_timetable[:max_length] + "\n\n... (сообщение обрезано, используйте навигацию по неделям)"
                await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await message.answer(formatted_timetable, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logging.error(f"Ошибка при получении расписания группы {group_input} для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("⚠️ Не удалось загрузить расписание. Попробуй позже.")

@router.message(Command("reload_timetable"))
async def cmd_reload_timetable(message: types.Message):
    """
    Команда для перезагрузки расписания всех групп.
    """
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил перезагрузку расписания")

    try:
        status_msg = await message.answer("⏳ Перезагружаю расписание всех групп... Это может занять некоторое время.")
        all_timetable = await get_all_groups_timetable(force_reload=True, user_id=user_id, progress_message=status_msg)
        # Финальное сообщение уже отправлено в get_all_groups_timetable, но обновим его
        try:
            await status_msg.edit_text(f"✅ Расписание успешно перезагружено! Загружено {len(all_timetable)} групп.")
        except:
            await message.answer(f"✅ Расписание успешно перезагружено! Загружено {len(all_timetable)} групп.")
    except Exception as e:
        logging.error(f"Ошибка при перезагрузке расписания для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("❌ Не удалось перезагрузить расписание. Попробуй позже.")


# --- Расписание (меню) ---

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


@router.callback_query(F.data == "m:sched:group")
async def cb_sched_group(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_group)
    await callback_query.message.answer(
        "👥 Введи название или ID группы (например: ИКВТ-21):",
        reply_markup=cancel_kb(),
    )


@router.callback_query(F.data == "m:sched:teacher")
async def cb_sched_teacher(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_teacher)
    await callback_query.message.answer(
        "🧑‍🏫 Введи фамилию преподавателя (например: Иванов):",
        reply_markup=cancel_kb(),
    )


@router.callback_query(F.data == "m:sched:room")
async def cb_sched_room(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_classroom)
    await callback_query.message.answer(
        "🚪 Введи номер аудитории (например: 401):",
        reply_markup=cancel_kb(),
    )


@router.callback_query(F.data == "m:sched:reload")
async def cb_sched_reload(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    await callback_query.answer("Обновляю расписание...")
    logging.info(f"Пользователь {user_id} запросил обновление расписания групп (меню)")
    status_msg = await callback_query.message.answer(
        "⏳ Обновляю расписание всех групп… Это может занять некоторое время."
    )
    try:
        all_timetable = await get_all_groups_timetable(
            force_reload=True, user_id=user_id, progress_message=status_msg
        )
        await status_msg.edit_text(
            f"✅ Расписание обновлено! Загружено {len(all_timetable)} групп."
        )
    except Exception as e:
        logging.error(f"Ошибка обновления расписания (меню) для {user_id}: {e}", exc_info=True)
        try:
            await status_msg.edit_text("❌ Не удалось обновить расписание. Попробуй позже.")
        except Exception:
            await callback_query.message.answer("❌ Не удалось обновить расписание. Попробуй позже.")


@router.message(UIStates.ask_group)
async def fsm_ask_group(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_group_timetable(message, override=(message.text or "").strip())


@router.message(UIStates.ask_teacher)
async def fsm_ask_teacher(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_teacher_timetable(message, override=(message.text or "").strip())


@router.message(UIStates.ask_classroom)
async def fsm_ask_classroom(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_classroom_timetable(message, override=(message.text or "").strip())
