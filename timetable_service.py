"""Сервис загрузки расписания всех групп (задача 4.1, шаг 12b).

Извлечён из main.py без изменения поведения. Содержит:
- `all_groups_timetable_with_progress` — загрузка расписания всех групп с tqdm-прогрессом;
- `send_progress_update` — отправка прогресса конкретному пользователю;
- `progress_updater` — фоновый цикл рассылки прогресса;
- `get_all_groups_timetable` — главная точка: кэш в памяти + JSON + TTL-рефреш;
- `_refresh_timetable_quietly` — фоновое обновление расписания без сообщений;
- `preload_timetable` — фоновая предзагрузка расписания при старте бота.

Внутреннее изменяемое состояние сервиса (`all_groups_timetable_cache`,
`timetable_loading`, `timetable_progress`, `timetable_progress_users`)
переприсваивается на уровне модуля. Внешний доступ — строго через
`import timetable_service; timetable_service.all_groups_timetable_cache`
(модуль-квалифицированный), иначе `from import`-копия зафиксирует
устаревшую ссылку/значение.

Направление зависимостей: timetable_service -> lk_client / timetable_cache /
botcore / TImetabels (вниз по слоям). Модуль НЕ импортирует main на уровне
модуля — цикла зависимостей нет.
"""
import asyncio
import logging
from datetime import datetime

import pytz

from lk_client import get_timetable_api
from timetable_cache import (
    _write_timetable_meta,
    _is_timetable_stale,
    _timetable_cache_age_now,
    TIMETABLE_TTL_HOURS,
)

# Импорт для работы с расписанием без авторизации.
try:
    from TImetabels import BonchAPI as TimetableBonchAPI, BROWSER_HEADERS
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from TImetabels import BonchAPI as TimetableBonchAPI, BROWSER_HEADERS

# --- Внутреннее изменяемое состояние сервиса --------------------------------
# Все четыре переприсваиваются функциями ниже; внешний доступ — только
# модуль-квалифицированный (timetable_service.<имя>).
all_groups_timetable_cache = None  # Кэш расписания всех групп
timetable_loading = False  # Флаг загрузки расписания
timetable_progress_users = {}  # Словарь {user_id: message} для отправки прогресса
timetable_progress = {'current': 0, 'total': 0, 'start_time': None}  # Прогресс загрузки


async def all_groups_timetable_with_progress(api):
    """
    Загружает расписание всех групп с отслеживанием прогресса.
    Использует оригинальный метод API, но отслеживает прогресс через глобальную переменную.
    """
    from datetime import datetime
    import aiohttp
    from tqdm.asyncio import tqdm_asyncio

    start = datetime.now()

    await api.get_schet()
    await api.get_groups()

    timetable = {}
    group_items = list(api.groups_id.items())
    total_groups = len(group_items)

    # Инициализируем прогресс
    global timetable_progress
    timetable_progress['total'] = total_groups
    timetable_progress['current'] = 0
    timetable_progress['start_time'] = start

    connector = aiohttp.TCPConnector(limit=api.limit)
    async with aiohttp.ClientSession(connector=connector, trust_env=True, headers=BROWSER_HEADERS) as session:
        completed = 0
        async def track_progress(coro):
            nonlocal completed
            result = await coro
            completed += 1
            timetable_progress['current'] = min(completed, total_groups)
            return result

        # cabinet.sut.ru нестабилен под нагрузкой: за один проход часть групп
        # отдаётся с ошибкой. Делаем несколько проходов, дозабирая только неудачные.
        pending = list(group_items)
        for pass_num in range(3):
            if not pending:
                break
            tracked_tasks = [track_progress(api.get_timetable(session, '1', group_id))
                             for group_id, _ in pending]
            results = await tqdm_asyncio.gather(*tracked_tasks,
                desc=f'Загрузка групп (проход {pass_num + 1})', unit=' Групп',
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} | {rate_fmt}{postfix}')

            still_failed = []
            for (group_id, group_name), result in zip(pending, results):
                if isinstance(result, str):
                    still_failed.append((group_id, group_name))
                else:
                    timetable[group_name] = result
            pending = still_failed

            if pending and pass_num < 2:
                logging.info('Проход %s завершён, не удалось %s групп — повтор через 20с',
                             pass_num + 1, len(pending))
                await asyncio.sleep(20)

        if pending:
            logging.warning('После всех проходов не загрузилось групп: %s', len(pending))

    # Сохраняем в JSON (как в оригинальном методе)
    TimetableBonchAPI.save_to_json(timetable, 'timetable.json')

    end = datetime.now()
    logging.info(f'Всего групп получено: {len(timetable)}')
    logging.info(f'Потрачено времени: {(end - start).total_seconds()} секунд')

    return timetable

async def send_progress_update(user_id: int, current: int, total: int, start_time):
    """
    Отправляет обновление прогресса пользователю.
    """
    global timetable_progress_users
    if user_id not in timetable_progress_users:
        return

    from datetime import datetime
    elapsed = (datetime.now() - start_time).total_seconds()
    percent = (current / total * 100) if total > 0 else 0
    rate = current / elapsed if elapsed > 0 else 0
    remaining = (total - current) / rate if rate > 0 else 0

    progress_bar_length = 20
    filled = int(progress_bar_length * current / total) if total > 0 else 0
    bar = '█' * filled + '░' * (progress_bar_length - filled)

    message_text = (
        f"📥 Загрузка расписания...\n\n"
        f"Прогресс: {bar}\n"
        f"{current}/{total} групп ({percent:.1f}%)\n"
        f"Скорость: {rate:.1f} групп/сек\n"
        f"Осталось: ~{remaining:.0f} сек"
    )

    try:
        msg = timetable_progress_users[user_id]
        await msg.edit_text(message_text)
    except Exception as e:
        logging.error(f"Ошибка при отправке прогресса пользователю {user_id}: {e}")

async def progress_updater():
    """
    Фоновая задача для отправки обновлений прогресса каждые 10 секунд.
    """
    global timetable_progress, timetable_progress_users, timetable_loading

    while timetable_loading:
        await asyncio.sleep(10)  # Обновляем каждые 10 секунд

        if not timetable_loading:
            break

        current = timetable_progress.get('current', 0)
        total = timetable_progress.get('total', 0)
        start_time = timetable_progress.get('start_time')

        if start_time and total > 0 and timetable_progress_users:
            # Отправляем обновления всем пользователям, которые ждут
            for user_id in list(timetable_progress_users.keys()):
                try:
                    await send_progress_update(user_id, current, total, start_time)
                except Exception as e:
                    logging.error(f"Ошибка при отправке прогресса пользователю {user_id}: {e}")

# --- TTL-кэш расписания групп ------------------------------------------------
# TTL-хелперы вынесены в timetable_cache.py (задача 4.1, шаг 7).


async def _refresh_timetable_quietly() -> None:
    """Фоновое обновление расписания групп без прогресс-сообщений."""
    try:
        await get_all_groups_timetable(force_reload=True)
        logging.info("Фоновое обновление расписания групп завершено")
    except Exception:
        logging.warning("Фоновое обновление расписания не удалось", exc_info=True)


async def get_all_groups_timetable(force_reload: bool = False, user_id: int = None, progress_message=None):
    """
    Получает расписание всех групп с кэшированием и отслеживанием прогресса.
    Сначала пытается загрузить из JSON файла, если он существует и не требуется принудительная перезагрузка.
    """
    global all_groups_timetable_cache, timetable_loading, timetable_progress_users

    if all_groups_timetable_cache is None or force_reload:
        # Если не требуется принудительная перезагрузка, пытаемся загрузить из JSON
        if not force_reload:
            try:
                timetable_from_json = TimetableBonchAPI.load_from_json('timetable.json')
                if timetable_from_json:
                    all_groups_timetable_cache = timetable_from_json
                    logging.info(f"Расписание загружено из JSON файла: {len(all_groups_timetable_cache)} групп")
                    return all_groups_timetable_cache
            except Exception as e:
                logging.warning(f"Не удалось загрузить расписание из JSON: {e}. Загружаю с сервера...")

        if timetable_loading:
            # Если уже идет загрузка, добавляем пользователя в список ожидающих
            if user_id and progress_message:
                timetable_progress_users[user_id] = progress_message
            # Ждем завершения загрузки
            while timetable_loading:
                await asyncio.sleep(1)
            # Удаляем пользователя из списка после завершения
            if user_id and user_id in timetable_progress_users:
                del timetable_progress_users[user_id]
            return all_groups_timetable_cache

        timetable_loading = True

        # Добавляем пользователя в список для получения прогресса
        if user_id and progress_message:
            timetable_progress_users[user_id] = progress_message

        # Запускаем задачу для отправки прогресса
        progress_task = None
        if timetable_progress_users:
            progress_task = asyncio.create_task(progress_updater())

        try:
            api = await get_timetable_api()
            logging.info("Загрузка расписания всех групп с сервера...")

            all_groups_timetable_cache = await all_groups_timetable_with_progress(api)
            logging.info(f"Расписание всех групп загружено: {len(all_groups_timetable_cache)} групп")
            # Сохранение в JSON уже выполняется в all_groups_timetable_with_progress.
            # Метку времени пишем в sidecar — для TTL и текста «обновлено N назад».
            _write_timetable_meta(datetime.now(pytz.timezone("Europe/Moscow")))

            # Отправляем финальное сообщение всем пользователям
            for user_id in list(timetable_progress_users.keys()):
                try:
                    msg = timetable_progress_users[user_id]
                    await msg.edit_text(f"✅ Расписание успешно загружено! Загружено {len(all_groups_timetable_cache)} групп.")
                except Exception as e:
                    logging.error(f"Ошибка при отправке финального сообщения пользователю {user_id}: {e}")

        except Exception as e:
            logging.error(f"Ошибка при загрузке расписания: {e}", exc_info=True)
            # Отправляем сообщение об ошибке всем пользователям
            for user_id in list(timetable_progress_users.keys()):
                try:
                    msg = timetable_progress_users[user_id]
                    await msg.edit_text("❌ Не удалось загрузить расписание. Попробуй позже.")
                except Exception as err:
                    logging.error(f"Ошибка при отправке сообщения об ошибке пользователю {user_id}: {err}")
            raise
        finally:
            timetable_loading = False
            # Останавливаем задачу прогресса
            if progress_task:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            # Очищаем список пользователей
            timetable_progress_users.clear()
            timetable_progress = {'current': 0, 'total': 0, 'start_time': None}

    # TTL: если кэш в памяти устарел и фоновая загрузка не идёт — обновляем в
    # фоне; пользователю сразу отдаём текущие (возможно устаревшие) данные.
    if (
        all_groups_timetable_cache is not None
        and not force_reload
        and not timetable_loading
        and _is_timetable_stale(_timetable_cache_age_now(), TIMETABLE_TTL_HOURS)
    ):
        logging.info("Кэш расписания устарел — запускаю фоновое обновление")
        asyncio.create_task(_refresh_timetable_quietly())

    return all_groups_timetable_cache


async def preload_timetable():
    """
    Предзагружает расписание всех групп в фоновом режиме при старте бота.
    Сначала пытается загрузить из JSON, если не получается - загружает с сервера.
    """
    try:
        logging.info("📅 Начало предзагрузки расписания всех групп...")
        # Пытаемся загрузить из JSON (не принудительно)
        await get_all_groups_timetable(force_reload=False, user_id=None, progress_message=None)
        logging.info("✅ Расписание всех групп предзагружено")
    except Exception as e:
        logging.error(f"❌ Ошибка при предзагрузке расписания: {e}", exc_info=True)


__all__ = [
    "all_groups_timetable_with_progress",
    "send_progress_update",
    "progress_updater",
    "get_all_groups_timetable",
    "_refresh_timetable_quietly",
    "preload_timetable",
]
