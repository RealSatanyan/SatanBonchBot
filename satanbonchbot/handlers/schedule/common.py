"""Обработчики расписания: общие для всех поддоменов.

Поддомен пакета handlers.schedule (разрез A.2b из единого handlers/schedule.py).
Перезагрузка кэша расписания всех групп — команда /reload_timetable и пункт
меню «обновить расписание». Также общие чистые хелперы, которыми пользуются
group.py/room.py/teacher.py: выбор «текущей недели по умолчанию» и обрезка/
разбивка слишком длинных сообщений под лимит Telegram (были продублированы
или отсутствовали в каждом из трёх файлов по отдельности).
"""

import logging

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from satanbonchbot.public_timetable import  BonchAPI
from satanbonchbot import timetable_cache
from satanbonchbot.timetable_service import  get_all_groups_timetable

router = Router()

# Лимит Telegram — 4096 символов на сообщение; берём с запасом под форматирование.
MAX_MESSAGE_LENGTH = 4000


def _current_semester_week() -> int:
    """Реальный порядковый номер текущей недели семестра (не из данных
    расписания, а из FIRST_DAY + datetime.now(), как BonchAPI.set_current_week).

    Не переиспользует module-singleton lk_client.timetable_api: тот вычисляет
    cur_week один раз при первой инициализации процесса и никогда не
    обновляет, так что через несколько недель работы бота отдавал бы неделю
    запуска, а не сегодняшнюю. BonchAPI(...) без сети (сеть — в отдельных
    async-методах get_groups/get_timetable), поэтому конструируем свежий
    инстанс только чтобы прочитать cur_week.
    """
    return BonchAPI(timetable_cache.get_first_day()).cur_week


def pick_current_week(lessons: list):
    """Неделя для показа по умолчанию у /group_timetable, /classroom_timetable,
    /teacher_timetable: реальная текущая неделя семестра, если для неё есть
    занятия в lessons, иначе — первая неделя, где занятия вообще есть.

    Раньше бралась буквально sorted(weeks)[0] — самая ранняя неделя в данных
    (начало семестра) — независимо от того, какая неделя сейчас, так что
    команды показывали расписание начала семестра почти весь семестр по
    умолчанию, пока пользователь не пролистает вперёд вручную.
    """
    weeks = sorted(set(lesson.get('Номер недели', 0) for lesson in lessons))
    if not weeks:
        return None
    real_week = _current_semester_week()
    return real_week if real_week in weeks else weeks[0]


def truncate_for_telegram(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> str:
    """Обрезает text под лимит Telegram, если он длиннее max_length —
    для мест, где сообщение можно только отредактировать целиком, а не
    отправить несколькими (process_*_week_navigation: message.edit_text)."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "\n\n... (сообщение обрезано, используйте навигацию по неделям)"


def split_for_telegram(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list:
    """Делит text на части под лимит Telegram для отправки несколькими
    сообщениями (message.answer). Режет по границам дней (разделитель
    "----------------------" из format_timetable_dict/format_timetable), чтобы
    не рвать день расписания пополам; если резать по дням некуда — обрезает
    целиком, как truncate_for_telegram."""
    if len(text) <= max_length:
        return [text]

    DAY_SEP = "----------------------"
    parts = text.split(DAY_SEP)
    if len(parts) <= 1:
        return [truncate_for_telegram(text, max_length)]

    chunks = []
    current = parts[0]
    for part in parts[1:]:
        candidate = current + DAY_SEP + part
        if len(candidate) > max_length:
            chunks.append(current)
            current = DAY_SEP + part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


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
