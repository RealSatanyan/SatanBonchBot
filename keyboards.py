"""Сборщики UI-клавиатур и UI-константы.

Извлечено из main.py (задача 4.1, шаг 5). Модуль содержит только чистые
функции-сборщики разметки и UI-константы: без side-effects, без БД, без сети.
keyboards.py НЕ импортирует main — это нижний слой относительно хэндлеров.
"""

import base64

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


# --- Тексты кнопок главного меню ---

BTN_SCHEDULE = "📅 Расписание"
BTN_AUTOCLICK = "✅ Автоотметка"
BTN_MESSAGES = "✉️ Сообщения"
BTN_PROFILE = "👤 Профиль"
BTN_HELP = "❓ Помощь"


# --- Навигация по неделям ---

def get_week_navigation_buttons(week_offset: int = 0) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с кнопками для навигации по неделям.
    :param week_offset: Текущее смещение недели.
    :return: InlineKeyboardMarkup с кнопками.
    """
    buttons = [
        [
            InlineKeyboardButton(text="📍 Сегодня", callback_data="my_day_0"),
            InlineKeyboardButton(text="Завтра 📍", callback_data="my_day_1"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Предыдущая неделя", callback_data=f"prev_week_{week_offset - 1}"),
            InlineKeyboardButton(text="Следующая неделя ➡️", callback_data=f"next_week_{week_offset + 1}"),
        ],
        [
            InlineKeyboardButton(text="Эта неделя", callback_data="current_week_0"),
        ],
        [
            InlineKeyboardButton(text="🖼️ Показать картинкой", callback_data=f"image_week_{week_offset}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_teacher_week_navigation_buttons(teacher_name: str, week_number: int = None) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с кнопками для навигации по неделям расписания преподавателя.
    :param teacher_name: Имя преподавателя.
    :param week_number: Текущий номер недели.
    :return: InlineKeyboardMarkup с кнопками.
    """
    if week_number is None:
        week_number = 0

    # Кодируем имя преподавателя для безопасной передачи в callback_data
    import base64
    encoded_name = base64.b64encode(teacher_name.encode('utf-8')).decode('utf-8')

    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Предыдущая неделя", callback_data=f"prev_teacher_week_{encoded_name}_{week_number - 1}"),
            InlineKeyboardButton(text="Следующая неделя ➡️", callback_data=f"next_teacher_week_{encoded_name}_{week_number + 1}"),
        ],
        [
            InlineKeyboardButton(text="Все недели", callback_data=f"all_teacher_weeks_{encoded_name}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_classroom_week_navigation_buttons(classroom_number: str, week_number: int = None) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с кнопками для навигации по неделям расписания кабинета.
    :param classroom_number: Номер кабинета.
    :param week_number: Текущий номер недели.
    :return: InlineKeyboardMarkup с кнопками.
    """
    if week_number is None:
        week_number = 0

    # Кодируем номер кабинета для безопасной передачи в callback_data
    import base64
    encoded_number = base64.b64encode(classroom_number.encode('utf-8')).decode('utf-8')

    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Предыдущая неделя", callback_data=f"prev_classroom_week_{encoded_number}_{week_number - 1}"),
            InlineKeyboardButton(text="Следующая неделя ➡️", callback_data=f"next_classroom_week_{encoded_number}_{week_number + 1}"),
        ],
        [
            InlineKeyboardButton(text="Все недели", callback_data=f"all_classroom_weeks_{encoded_number}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_group_week_navigation_buttons(group_name: str, week_number: int = None) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру с кнопками для навигации по неделям расписания группы.
    :param group_name: Название группы.
    :param week_number: Текущий номер недели.
    :return: InlineKeyboardMarkup с кнопками.
    """
    if week_number is None:
        week_number = 0

    # Кодируем название группы для безопасной передачи в callback_data
    import base64
    encoded_name = base64.b64encode(group_name.encode('utf-8')).decode('utf-8')

    buttons = [
        [
            InlineKeyboardButton(text="📍 Сегодня", callback_data=f"group_day_{encoded_name}_0"),
            InlineKeyboardButton(text="Завтра 📍", callback_data=f"group_day_{encoded_name}_1"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Предыдущая неделя", callback_data=f"prev_group_week_{encoded_name}_{week_number - 1}"),
            InlineKeyboardButton(text="Следующая неделя ➡️", callback_data=f"next_group_week_{encoded_name}_{week_number + 1}"),
        ],
        [
            InlineKeyboardButton(text="🖼️ Картинка", callback_data=f"image_group_week_{encoded_name}_{week_number}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- Главное меню (reply-клавиатура) ---

def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SCHEDULE), KeyboardButton(text=BTN_AUTOCLICK)],
            [KeyboardButton(text=BTN_MESSAGES), KeyboardButton(text=BTN_PROFILE)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери раздел в меню снизу",
    )


HELP_TEXT = (
    "❓ <b>Помощь — SatanBonchBot</b>\n\n"
    "Я помощник студента СПбГУТ. Разделы — кнопками в меню снизу.\n\n"
    "📅 <b>Расписание</b>\n"
    "Расписание групп, преподавателей и аудиторий. Доступно без входа в ЛК. "
    "После входа добавляется «Моё расписание».\n\n"
    "✅ <b>Автоотметка</b>\n"
    "Бот сам отмечает тебя на парах в личном кабинете, пока идёт занятие. "
    "Нужен вход в ЛК. Включается и выключается кнопкой.\n\n"
    "✉️ <b>Сообщения</b>\n"
    "Чтение входящих и отправка сообщений через ЛК. Нужен вход в ЛК.\n\n"
    "👤 <b>Профиль</b>\n"
    "Твой email, статус входа в ЛК, настройки уведомлений, "
    "повторный вход и выход.\n\n"
    "🔑 <b>Вход в личный кабинет</b>\n"
    "Кнопка «🔑 Войти в ЛК» либо команда одной строкой:\n"
    "<code>/login email пароль</code>\n"
    "Пароль хранится в зашифрованном виде.\n\n"
    "🔔 <b>Уведомления</b>\n"
    "Бот предупреждает о начале пар. Включение и время напоминания "
    "настраиваются в разделе «👤 Профиль» → «🔔 Уведомления».\n\n"
    "<b>Команды:</b>\n"
    "/start — главное меню\n"
    "/login — войти в ЛК\n"
    "/help — эта справка\n"
    "/cancel — отменить текущее действие"
)


# --- Inline-клавиатуры меню и диалогов ---

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="m:cancel")]]
    )


def login_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔑 Войти в ЛК", callback_data="m:login")]]
    )


def schedule_menu_kb(logged_in: bool) -> InlineKeyboardMarkup:
    rows = []
    if logged_in:
        rows.append([InlineKeyboardButton(text="🎓 Моё расписание", callback_data="m:sched:my")])
    rows.append([InlineKeyboardButton(text="👥 Группа", callback_data="m:sched:group")])
    rows.append([InlineKeyboardButton(text="🧑‍🏫 Преподаватель", callback_data="m:sched:teacher")])
    rows.append([InlineKeyboardButton(text="🚪 Аудитория", callback_data="m:sched:room")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить расписание групп", callback_data="m:sched:reload")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def autoclick_menu_kb(is_running: bool) -> InlineKeyboardMarkup:
    toggle = (
        InlineKeyboardButton(text="⏹ Выключить", callback_data="m:auto:stop")
        if is_running
        else InlineKeyboardButton(text="▶️ Включить", callback_data="m:auto:start")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [toggle],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="m:auto:refresh")],
        [InlineKeyboardButton(text="🔔 Проверить уведомления", callback_data="m:auto:notify")],
    ])


def messages_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Входящие", callback_data="m:msg:inbox")],
        [InlineKeyboardButton(text="✏️ Написать", callback_data="m:msg:write")],
    ])


def profile_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="m:profile:notify")],
        [InlineKeyboardButton(text="🔄 Войти заново", callback_data="m:profile:relogin")],
        [InlineKeyboardButton(text="🚪 Выйти", callback_data="m:profile:logout")],
    ])


# --- Настройки уведомлений ---
# NOTIFY_MINUTE_OPTIONS — чисто UI-константа: используется только notify_settings_kb.
# (NOTIFY_DEFAULT_MINUTES относится к слою настроек/БД и живёт в db.py.)
NOTIFY_MINUTE_OPTIONS = (5, 10, 15, 30)


def notify_settings_text(enabled: bool, minutes: int) -> str:
    if enabled:
        return (
            "🔔 Настройки уведомлений\n\n"
            f"Предупреждаю о начале пары за {minutes} мин.\n"
            "Выбери, за сколько минут предупреждать, или выключи уведомления."
        )
    return (
        "🔕 Настройки уведомлений\n\n"
        "Уведомления о начале пар выключены."
    )


def notify_settings_kb(enabled: bool, minutes: int) -> InlineKeyboardMarkup:
    toggle_text = "🔔 Уведомления включены" if enabled else "🔕 Уведомления выключены"
    rows = [[InlineKeyboardButton(text=toggle_text, callback_data="m:notify:toggle")]]
    if enabled:
        rows.append([
            InlineKeyboardButton(
                text=f"{opt} мин" + (" ✅" if opt == minutes else ""),
                callback_data=f"m:notify:min:{opt}",
            )
            for opt in NOTIFY_MINUTE_OPTIONS
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- Написание сообщения: тема и выбор получателя ---

def title_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Без темы", callback_data="mw:notitle")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="m:cancel")],
    ])


RECIPIENTS_PER_PAGE = 8


def recipients_page_kb(results: list, page: int) -> InlineKeyboardMarkup:
    total = len(results)
    pages = max(1, (total + RECIPIENTS_PER_PAGE - 1) // RECIPIENTS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    start = page * RECIPIENTS_PER_PAGE
    rows = [
        [InlineKeyboardButton(text=results[i]["label"], callback_data=f"mw:pick:{i}")]
        for i in range(start, min(start + RECIPIENTS_PER_PAGE, total))
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"mw:page:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="mw:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"mw:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="m:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
