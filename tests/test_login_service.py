"""Тесты login_service: парсинг /login, вход в ЛК, автологин.

Сеть замокана — DebuggableBonchAPI подменяется фейком, чьи .login не ходят
в ЛК. БД — временная in-memory (фикстура temp_db). Реестры lk_client.apis /
lesson_controller.controllers изолируются фикстурой reset_registries.
"""
import asyncio

from satanbonchbot import lk_client
from satanbonchbot import lesson_controller
from satanbonchbot.login_service import  parse_login_credentials, perform_login, auto_login_user
from satanbonchbot.security import  encrypt_password


class _FakeAPI:
    """Фейк DebuggableBonchAPI: .login не ходит в сеть, возвращает login_result."""

    login_result = True

    def __init__(self, *args, **kwargs):
        self.login_calls = []

    async def login(self, email, password):
        self.login_calls.append((email, password))
        return type(self).login_result


def _patch_api(monkeypatch, login_result=True):
    cls = type("_FakeAPIVariant", (_FakeAPI,), {"login_result": login_result})
    monkeypatch.setattr(lk_client, "DebuggableBonchAPI", cls)
    return cls


# --- parse_login_credentials -------------------------------------------------

def test_parse_login_credentials_valid():
    assert parse_login_credentials("/login user@sut.ru secret123") == ("user@sut.ru", "secret123")


def test_parse_login_credentials_accepts_bot_mention():
    assert parse_login_credentials("/login@SatanBonchBot user@sut.ru pass") == ("user@sut.ru", "pass")


def test_parse_login_credentials_strips_surrounding_whitespace():
    assert parse_login_credentials("  /login user@sut.ru pass  ") == ("user@sut.ru", "pass")


def test_parse_login_credentials_rejects_missing_password():
    assert parse_login_credentials("/login user@sut.ru") is None


def test_parse_login_credentials_rejects_empty_and_none():
    assert parse_login_credentials("") is None
    assert parse_login_credentials(None) is None


def test_parse_login_credentials_rejects_non_email():
    assert parse_login_credentials("/login notanemail secret") is None


def test_parse_login_credentials_rejects_overlong_password():
    long_password = "x" * 300
    assert parse_login_credentials(f"/login user@sut.ru {long_password}") is None


# --- perform_login -----------------------------------------------------------

def test_perform_login_success_registers_and_persists(monkeypatch, temp_db, reset_registries):
    _patch_api(monkeypatch, login_result=True)

    ok = asyncio.run(perform_login(555, "user@sut.ru", "secret"))

    assert ok is True
    assert 555 in lk_client.apis
    assert 555 in lesson_controller.controllers
    row = temp_db.execute("SELECT email FROM users WHERE user_id = 555").fetchone()
    assert row[0] == "user@sut.ru"


def test_perform_login_stores_password_encrypted(monkeypatch, temp_db, reset_registries):
    _patch_api(monkeypatch, login_result=True)

    asyncio.run(perform_login(556, "user@sut.ru", "plaintext-pass"))

    stored = temp_db.execute("SELECT password FROM users WHERE user_id = 556").fetchone()[0]
    assert stored != "plaintext-pass"


def test_perform_login_failed_login_returns_false(monkeypatch, temp_db, reset_registries):
    _patch_api(monkeypatch, login_result=False)

    ok = asyncio.run(perform_login(557, "user@sut.ru", "secret"))

    assert ok is False
    assert 557 not in lk_client.apis
    assert temp_db.execute("SELECT * FROM users WHERE user_id = 557").fetchone() is None


def test_perform_login_updates_existing_user(monkeypatch, temp_db, reset_registries):
    _patch_api(monkeypatch, login_result=True)
    temp_db.execute(
        "INSERT INTO users (user_id, email, password) VALUES (558, 'old@sut.ru', 'x')"
    )
    temp_db.commit()

    asyncio.run(perform_login(558, "new@sut.ru", "secret"))

    rows = temp_db.execute("SELECT email FROM users WHERE user_id = 558").fetchall()
    assert rows == [("new@sut.ru",)]


# --- auto_login_user ---------------------------------------------------------

def test_auto_login_user_unknown_user_returns_false(temp_db, reset_registries):
    assert asyncio.run(auto_login_user(999)) is False


def test_auto_login_user_success(monkeypatch, temp_db, reset_registries):
    _patch_api(monkeypatch, login_result=True)
    temp_db.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)",
        (560, "user@sut.ru", encrypt_password("secret")),
    )
    temp_db.commit()

    ok = asyncio.run(auto_login_user(560))

    assert ok is True
    assert 560 in lk_client.apis
    assert 560 in lesson_controller.controllers


def test_auto_login_user_failed_login_cleans_up(monkeypatch, temp_db, reset_registries):
    _patch_api(monkeypatch, login_result=False)
    temp_db.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)",
        (561, "user@sut.ru", encrypt_password("secret")),
    )
    temp_db.commit()

    ok = asyncio.run(auto_login_user(561))

    assert ok is False
    assert 561 not in lk_client.apis
    assert 561 not in lesson_controller.controllers
