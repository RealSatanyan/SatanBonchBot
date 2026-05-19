"""Общая настройка pytest и фикстуры для тестов бота.

parsers.py лежит в корне проекта — добавляем корень в sys.path.
main.py при импорте читает .env и users.db по относительным путям,
поэтому фиксируем рабочую директорию на корне проекта.
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# main.py читает .env / users.db относительно CWD — выполняется ДО сбора тестов.
os.chdir(ROOT)

FIXTURES_DIR = ROOT / "tests" / "fixtures"

# Полная схема таблицы users (с колонками настроек) — для временной тестовой БД.
USERS_SCHEMA = """
    CREATE TABLE users (
        user_id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        password TEXT NOT NULL,
        notify_enabled INTEGER NOT NULL DEFAULT 1,
        notify_minutes INTEGER NOT NULL DEFAULT 10,
        autoclick_enabled INTEGER NOT NULL DEFAULT 1,
        group_name TEXT,
        last_seen_message_id TEXT
    )
"""


@pytest.fixture
def load_fixture():
    """Функция чтения HTML-фикстуры из tests/fixtures/ по имени файла."""
    def _load(name: str) -> str:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")
    return _load


@pytest.fixture
def temp_db():
    """
    Временная in-memory БД users; подменяет db.conn / db.cursor,
    чтобы тесты DB-хелперов не трогали настоящий users.db.

    conn/cursor живут в db.py (задача 4.1, шаг 4). DB-функции и весь inline-SQL
    в main.py обращаются к ним как db.conn / db.cursor, поэтому подмена этих
    модульных глобалей видна и в db.py, и в main.py.
    """
    import db

    test_conn = sqlite3.connect(":memory:")
    test_conn.execute(USERS_SCHEMA)
    test_conn.commit()

    original_conn, original_cursor = db.conn, db.cursor
    db.conn = test_conn
    db.cursor = test_conn.cursor()
    try:
        yield test_conn
    finally:
        db.conn, db.cursor = original_conn, original_cursor
        test_conn.close()


@pytest.fixture
def reset_rate_limit():
    """Очищает in-memory счётчик попыток входа до и после теста."""
    import security

    security._login_attempts.clear()
    yield
    security._login_attempts.clear()


@pytest.fixture
def reset_registries():
    """
    Изолирует реестры lk_client.apis и lesson_controller.controllers.

    perform_login / auto_login_user регистрируют в них API-инстансы и
    контроллеры; фикстура восстанавливает исходное содержимое после теста.
    """
    import lk_client
    import lesson_controller

    apis_backup = dict(lk_client.apis)
    controllers_backup = dict(lesson_controller.controllers)
    lk_client.apis.clear()
    lesson_controller.controllers.clear()
    try:
        yield
    finally:
        lk_client.apis.clear()
        lk_client.apis.update(apis_backup)
        lesson_controller.controllers.clear()
        lesson_controller.controllers.update(controllers_backup)


@pytest.fixture
def reset_timetable_service():
    """
    Сохраняет и восстанавливает изменяемое состояние timetable_service.

    Состояние (all_groups_timetable_cache, timetable_loading,
    timetable_progress_users, timetable_progress) переприсваивается функциями
    сервиса — без изоляции тесты влияли бы друг на друга.
    """
    import timetable_service as ts

    backup = (
        ts.all_groups_timetable_cache,
        ts.timetable_loading,
        dict(ts.timetable_progress_users),
        dict(ts.timetable_progress),
    )
    ts.all_groups_timetable_cache = None
    ts.timetable_loading = False
    ts.timetable_progress_users.clear()
    ts.timetable_progress = {'current': 0, 'total': 0, 'start_time': None}
    try:
        yield ts
    finally:
        ts.all_groups_timetable_cache = backup[0]
        ts.timetable_loading = backup[1]
        ts.timetable_progress_users.clear()
        ts.timetable_progress_users.update(backup[2])
        ts.timetable_progress = backup[3]


@pytest.fixture
def reset_message_states():
    """Изолирует messages_service.message_states между тестами."""
    import messages_service

    backup = dict(messages_service.message_states)
    messages_service.message_states.clear()
    try:
        yield messages_service.message_states
    finally:
        messages_service.message_states.clear()
        messages_service.message_states.update(backup)
