"""Работа с SQLite (users.db): соединение, схема, миграции, DB-хелперы.

Извлечён из main.py (задача 4.1, шаг 4) — чистая декомпозиция без изменения
поведения. Side-effects при импорте СОХРАНЕНЫ намеренно: при импорте модуля
открывается соединение sqlite3 с users.db и выполняется CREATE TABLE / миграция
колонок — ровно как было в main.py. Отдельная функция init_db() намеренно НЕ
вводится.

ВАЖНО про conn/cursor: это изменяемые модульные глобали, которые тестовая
фикстура temp_db подменяет. Снаружи к ним обращаются ТОЛЬКО как db.conn /
db.cursor (через `import db`), НИКОГДА `from db import conn`, иначе подмена
в фикстуре перестаёт работать. DB-функции внутри этого модуля используют свои
модульные глобали conn/cursor напрямую (они в одном модуле — это корректно).
"""
import sqlite3
from contextlib import closing


# Рабочее соединение с users.db (относительный путь от CWD).
# check_same_thread=False обязателен: бот многопоточный (aiogram).
conn = sqlite3.connect('users.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблицы + идемпотентная миграция колонок настроек.
# Выполняется через отдельное короткоживущее соединение, как было в main.py.
with closing(sqlite3.connect('users.db')) as _ddl_db:
    _ddl_db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            notify_enabled INTEGER NOT NULL DEFAULT 1,
            notify_minutes INTEGER NOT NULL DEFAULT 10,
            autoclick_enabled INTEGER NOT NULL DEFAULT 1,
            group_name TEXT,
            last_seen_message_id TEXT
        )
    ''')
    # Миграция уже существующих БД: добавляем недостающие колонки настроек
    # (ALTER TABLE ADD COLUMN идемпотентным не является).
    _user_columns = {row[1] for row in _ddl_db.execute("PRAGMA table_info(users)")}
    if 'notify_enabled' not in _user_columns:
        _ddl_db.execute('ALTER TABLE users ADD COLUMN notify_enabled INTEGER NOT NULL DEFAULT 1')
    if 'notify_minutes' not in _user_columns:
        _ddl_db.execute('ALTER TABLE users ADD COLUMN notify_minutes INTEGER NOT NULL DEFAULT 10')
    if 'autoclick_enabled' not in _user_columns:
        _ddl_db.execute('ALTER TABLE users ADD COLUMN autoclick_enabled INTEGER NOT NULL DEFAULT 1')
    # group_name — учебная группа пользователя (задача C.0); нужна для
    # уведомлений об изменении расписания. Nullable: определяется из ЛК.
    if 'group_name' not in _user_columns:
        _ddl_db.execute('ALTER TABLE users ADD COLUMN group_name TEXT')
    # last_seen_message_id — id последнего виденного входящего ЛК (задача C.2);
    # по нему фоновый опрос отличает новые сообщения от уже показанных.
    if 'last_seen_message_id' not in _user_columns:
        _ddl_db.execute('ALTER TABLE users ADD COLUMN last_seen_message_id TEXT')
    _ddl_db.commit()


def is_registered(user_id: int) -> bool:
    cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
    return cursor.fetchone() is not None


# --- Настройки уведомлений о парах -------------------------------------------
NOTIFY_DEFAULT_MINUTES = 10


def get_notify_settings(user_id: int) -> tuple[bool, int]:
    """Возвращает (уведомления включены, за сколько минут предупреждать о паре)."""
    cursor.execute(
        'SELECT notify_enabled, notify_minutes FROM users WHERE user_id = ?',
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return True, NOTIFY_DEFAULT_MINUTES
    enabled_raw, minutes_raw = row
    enabled = True if enabled_raw is None else bool(enabled_raw)
    minutes = int(minutes_raw) if minutes_raw else NOTIFY_DEFAULT_MINUTES
    return enabled, minutes


def set_notify_enabled(user_id: int, enabled: bool) -> None:
    with conn:
        cursor.execute(
            'UPDATE users SET notify_enabled = ? WHERE user_id = ?',
            (1 if enabled else 0, user_id),
        )


def set_notify_minutes(user_id: int, minutes: int) -> None:
    with conn:
        cursor.execute(
            'UPDATE users SET notify_minutes = ? WHERE user_id = ?',
            (minutes, user_id),
        )


def get_autoclick_enabled(user_id: int) -> bool:
    """
    Включена ли автоотметка пользователем. Учитывается при автозапуске
    автокликалки на старте бота: выключил вручную — не запускаем снова.
    """
    cursor.execute('SELECT autoclick_enabled FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    if not row or row[0] is None:
        return True
    return bool(row[0])


def set_autoclick_enabled(user_id: int, enabled: bool) -> None:
    with conn:
        cursor.execute(
            'UPDATE users SET autoclick_enabled = ? WHERE user_id = ?',
            (1 if enabled else 0, user_id),
        )


# --- Учебная группа пользователя (задача C.0) --------------------------------

def get_user_group(user_id: int):
    """Название учебной группы пользователя или None, если не определена."""
    cursor.execute('SELECT group_name FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


def set_user_group(user_id: int, group_name: str) -> None:
    """Сохраняет определённую из ЛК учебную группу пользователя."""
    with conn:
        cursor.execute(
            'UPDATE users SET group_name = ? WHERE user_id = ?',
            (group_name, user_id),
        )


def get_users_by_group(group_name: str) -> list:
    """user_id всех пользователей указанной учебной группы (задача C.1)."""
    cursor.execute('SELECT user_id FROM users WHERE group_name = ?', (group_name,))
    return [row[0] for row in cursor.fetchall()]


# --- Последнее виденное сообщение ЛК (задача C.2) ----------------------------

def get_last_seen_message_id(user_id: int):
    """id последнего виденного входящего ЛК или None, если опроса ещё не было."""
    cursor.execute('SELECT last_seen_message_id FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


def set_last_seen_message_id(user_id: int, message_id: str) -> None:
    """Запоминает id последнего виденного входящего ЛК."""
    with conn:
        cursor.execute(
            'UPDATE users SET last_seen_message_id = ? WHERE user_id = ?',
            (message_id, user_id),
        )
