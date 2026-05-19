"""Обработчики расписания: общие для всех поддоменов.

Поддомен пакета handlers.schedule (разрез A.2b из единого handlers/schedule.py).
Перезагрузка кэша расписания всех групп — команда /reload_timetable и пункт
меню «обновить расписание». Поведение хэндлеров не менялось — только перенос
в свой файл со своим Router().
"""

import logging

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from timetable_service import get_all_groups_timetable

router = Router()


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
