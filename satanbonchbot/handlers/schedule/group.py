"""Обработчики расписания: расписание учебной группы.

Поддомен пакета handlers.schedule (разрез A.2b из единого handlers/schedule.py).
Команды /groups и /group_timetable, навигация по неделям, генерация картинки,
пресеты «Сегодня/Завтра», пункт меню и FSM-диалог ввода группы. Поведение
хэндлеров не менялось — только перенос в свой файл со своим Router().

Разделяемое состояние сервиса расписания (all_groups_timetable_cache,
timetable_loading) читается модуль-квалифицированно через timetable_service.*,
т.к. оно переприсваивается на уровне модуля сервиса.
"""

import asyncio
import os
import logging
from datetime import timedelta

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile

from satanbonchbot.states import  UIStates
from satanbonchbot.keyboards import  cancel_kb, get_group_week_navigation_buttons
from satanbonchbot.lk_client import  get_timetable_api
from satanbonchbot import timetable_service
from satanbonchbot.timetable_service import  get_all_groups_timetable
from satanbonchbot.formatting import  (
    format_timetable_dict,
    filter_group_lessons_by_date,
    _moscow_today,
)
from satanbonchbot.rendering import  generate_timetable_image_from_dict

router = Router()


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
                # Генерируем изображение для текущей недели. Рендер синхронный
                # (PIL) — выносим в поток, чтобы не морозить event loop (B.1).
                image_path = await asyncio.to_thread(
                    generate_timetable_image_from_dict,
                    timetable,
                    f"Расписание группы {group_name}",
                    week_number=week_number,
                    group_name=group_name,
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


@router.callback_query(F.data == "m:sched:group")
async def cb_sched_group(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await state.set_state(UIStates.ask_group)
    await callback_query.message.answer(
        "👥 Введи название или ID группы (например: ИКВТ-21):",
        reply_markup=cancel_kb(),
    )


@router.message(UIStates.ask_group)
async def fsm_ask_group(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_group_timetable(message, override=(message.text or "").strip())
