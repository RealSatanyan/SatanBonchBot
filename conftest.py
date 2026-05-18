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
        autoclick_enabled INTEGER NOT NULL DEFAULT 1
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
