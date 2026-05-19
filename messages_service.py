"""Сервис списка сообщений ЛК (задача 4.1, шаг 12c).

Извлечён из main.py без изменения поведения. Содержит:
- `show_message_list` — отображение сообщения из списка с навигацией;
- `format_message_count` — счётчик сообщений (точное число либо оценка «≈N»);
- `_messages_cache_fresh` — проверка свежести тёплого кэша списка;
- `_build_message_state` — сборка записи `message_states` после первой страницы;
- `_invalidate_messages_cache` — пометка кэша устаревшим.

Тёплый кэш списка сообщений: повторный заход в /messages в пределах
`MESSAGES_CACHE_TTL_SEC` показывает уже загруженное, не перезапрашивая
первую страницу из ЛК.

Внутреннее изменяемое состояние сервиса (`message_states`,
`pending_lk_messages`) читается и пишется хэндлерами в main.py. Внешний
доступ — строго через `import messages_service; messages_service.message_states`
(модуль-квалифицированный), иначе `from import`-копия зафиксирует устаревшую
ссылку. Поэтому эти словари исключены из `__all__`.

Направление зависимостей: messages_service -> botcore (вниз по слоям).
Модуль НЕ импортирует main на уровне модуля — цикла зависимостей нет.
"""
import asyncio
import logging
import time as time_module

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from botcore import bot
from config import LK_MESSAGE_POLL_MIN
import db
import lk_client
import lk_messages

__all__ = [
    'MESSAGES_CACHE_TTL_SEC',
    'show_message_list',
    'format_message_count',
    'check_new_messages_for_user',
    'message_poll_loop',
    '_messages_cache_fresh',
    '_build_message_state',
    '_invalidate_messages_cache',
]

# Состояния навигации по сообщениям
message_states = {}  # Словарь {user_id: {'messages': [], 'current_index': 0}} для навигации по сообщениям
# Тёплый кэш списка сообщений: повторный заход в /messages в пределах TTL
# показывает уже загруженное, не перезапрашивая первую страницу из ЛК.
MESSAGES_CACHE_TTL_SEC = 300

# Словарь {(user_id, recipient_id): {'text': str, 'title': str, 'label': str}}
pending_lk_messages = {}


def format_message_count(loaded: int, total_pages: int, per_page: int, has_more: bool) -> str:
    """Счётчик сообщений: точное число либо оценка «≈N» (страниц × размер)."""
    if not has_more:
        return str(loaded)
    if total_pages > 1 and per_page > 0:
        return f"≈{total_pages * per_page}"
    return f"{loaded}+"


def _messages_cache_fresh(state, now_ts: float, ttl_sec: float) -> bool:
    """True — кэш списка сообщений ещё свежий, можно показать без перезапроса."""
    if not state or not state.get('messages'):
        return False
    fetched_at = state.get('fetched_at')
    if fetched_at is None:
        return False
    return (now_ts - fetched_at) < ttl_sec


def _build_message_state(api, first_page: dict) -> dict:
    """Собирает запись message_states после загрузки первой страницы сообщений."""
    messages = first_page['messages']
    return {
        'api': api,
        'messages': messages,
        'total_pages': first_page['total_pages'],
        'loaded_pages': 1,
        'current_index': 0,
        'per_page': len(messages),
        'fetched_at': time_module.time(),
    }


def _invalidate_messages_cache(user_id) -> None:
    """Помечает кэш сообщений устаревшим — следующий /messages перезапросит ЛК."""
    state = message_states.get(user_id)
    if state:
        state['fetched_at'] = None


async def show_message_list(user_id: int, chat_id: int, index: int):
    """
    Отображает список сообщений с навигацией.
    """
    if user_id not in message_states:
        return

    state = message_states[user_id]
    messages = state['messages']
    if not messages or index < 0 or index >= len(messages):
        return

    # Есть ли ещё не загруженные страницы (для счётчика и кнопки «Вперёд»).
    has_more_pages = state.get('loaded_pages', 1) < state.get('total_pages', 1)

    msg = messages[index]

    # Формируем текст сообщения
    unread_marker = "🔴" if msg.get('is_unread', False) else ""
    files_marker = "📎" if msg.get('has_files', False) else ""
    date = msg.get('date', '')[:10] if msg.get('date') else ''
    sender = msg.get('sender', 'Неизвестно')
    if sender and '(' in sender:
        sender = sender.split('(')[0].strip()

    title = msg.get('title', 'Без названия')
    if len(title) > 100:
        title = title[:97] + '...'

    count_display = format_message_count(
        len(messages), state.get('total_pages', 1), state.get('per_page', 0), has_more_pages
    )
    text = f"{unread_marker} *Сообщение {index + 1} из {count_display}*\n\n"
    text += f"📅 *Дата:* {date}\n"
    text += f"👤 *Отправитель:* {sender}\n"
    text += f"📋 *Тема:* {title}\n"
    if files_marker:
        text += f"{files_marker} *Есть файлы*\n"

    # Создаем клавиатуру для навигации
    keyboard = []
    row = []

    if index > 0:
        row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"msg_prev_{index}"))
    if index < len(messages) - 1 or has_more_pages:
        row.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"msg_next_{index}"))

    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton(text="📖 Открыть сообщение", callback_data=f"msg_open_{msg['id']}")])
    keyboard.append([InlineKeyboardButton(text="🔄 Обновить список", callback_data="msg_refresh")])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Ошибка при отправке списка сообщений: {e}")


# --- Уведомления о новых сообщениях ЛК (задача C.2) --------------------------

async def _notify_new_messages(user_id: int, new_messages: list) -> None:
    """Шлёт пользователю уведомление о новых входящих ЛК (до 5 в списке)."""
    count = len(new_messages)
    lines = []
    for msg in new_messages[:5]:
        sender = msg.get('sender', 'Неизвестно')
        if sender and '(' in sender:
            sender = sender.split('(')[0].strip()
        title = msg.get('title', 'Без названия')
        if len(title) > 80:
            title = title[:77] + '...'
        lines.append(f"👤 {sender}\n📋 {title}")

    text = f"📨 Новых сообщений в ЛК: {count}\n\n" + "\n\n".join(lines)
    if count > 5:
        text += f"\n\n…и ещё {count - 5}"
    text += "\n\nОткрой «✉️ Сообщения», чтобы прочитать."

    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        logging.warning("Не удалось отправить уведомление о сообщениях %s: %s", user_id, e)


async def check_new_messages_for_user(user_id: int) -> int:
    """
    Опрашивает 1-ю страницу входящих ЛК пользователя и уведомляет о новых
    сообщениях. Возвращает число сообщений, о которых уведомили.

    Первый опрос только фиксирует точку отсчёта (last_seen_message_id) —
    без уведомлений, чтобы не спамить уже накопившимися сообщениями.

    Уважает тумблер B.1: если пользователь отключил уведомления о сообщениях,
    ЛК не опрашиваем вовсе — ни пуша, ни лишнего запроса (антибот).
    """
    if not db.get_notify_messages_enabled(user_id):
        return 0

    message_api = await lk_messages.get_message_api(user_id)
    if not message_api:
        return 0

    first_page = await message_api.get_messages_page(1)
    messages = first_page.get('messages', [])
    if not messages:
        return 0

    # Входящие на странице 1 идут сверху вниз — новые первыми.
    newest_id = messages[0]['id']
    last_seen = db.get_last_seen_message_id(user_id)

    if last_seen is None:
        db.set_last_seen_message_id(user_id, newest_id)
        return 0
    if last_seen == newest_id:
        return 0

    new_messages = []
    for msg in messages:
        if msg['id'] == last_seen:
            break
        new_messages.append(msg)
    # last_seen не найден на странице (новых больше, чем влезло) — всё новое.
    if not new_messages:
        new_messages = messages

    db.set_last_seen_message_id(user_id, newest_id)
    await _notify_new_messages(user_id, new_messages)
    return len(new_messages)


async def message_poll_loop() -> None:
    """
    Фоновый цикл уведомлений о новых сообщениях ЛК (задача C.2).

    Каждые LK_MESSAGE_POLL_MIN минут опрашивает входящие каждого
    авторизованного пользователя. Запросы стагерятся, чтобы не нагружать ЛК.
    """
    interval = LK_MESSAGE_POLL_MIN * 60
    logging.info("📨 Фоновый опрос сообщений ЛК запущен (раз в %s мин)", LK_MESSAGE_POLL_MIN)
    while True:
        await asyncio.sleep(interval)
        for user_id in list(lk_client.apis.keys()):
            try:
                notified = await check_new_messages_for_user(user_id)
                if notified:
                    logging.info("Уведомил пользователя %s о %s новых сообщениях ЛК", user_id, notified)
            except Exception:
                logging.warning("Ошибка опроса сообщений ЛК для %s", user_id, exc_info=True)
            await asyncio.sleep(2)
