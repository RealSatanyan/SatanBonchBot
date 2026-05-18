"""Тесты безопасности: шифрование паролей, разбор /login, rate-limit входа."""
import time as _time

from cryptography.fernet import Fernet

import main
import security


# --- encrypt_password / decrypt_password -------------------------------------

def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setattr(security, "_fernet", Fernet(Fernet.generate_key()))
    secret = "MyS3cret!пароль"
    encrypted = security.encrypt_password(secret)
    assert encrypted != secret
    assert security.decrypt_password(encrypted) == secret


def test_encrypted_value_is_not_plaintext(monkeypatch):
    monkeypatch.setattr(security, "_fernet", Fernet(Fernet.generate_key()))
    assert "password123" not in security.encrypt_password("password123")


def test_decrypt_legacy_plaintext_passthrough(monkeypatch):
    # Старые незашифрованные записи (до миграции) возвращаются как есть.
    monkeypatch.setattr(security, "_fernet", Fernet(Fernet.generate_key()))
    assert security.decrypt_password("old_plaintext_password") == "old_plaintext_password"


def test_decrypt_empty_value(monkeypatch):
    monkeypatch.setattr(security, "_fernet", Fernet(Fernet.generate_key()))
    assert security.decrypt_password("") == ""


def test_encryption_disabled_is_identity(monkeypatch):
    # Без ключа (_fernet=None) функции работают как тождественные.
    monkeypatch.setattr(security, "_fernet", None)
    assert security.encrypt_password("abc") == "abc"
    assert security.decrypt_password("abc") == "abc"


def test_real_encryption_key_is_valid():
    # Регрессия: ENCRYPTION_KEY из .env должен быть валидным Fernet-ключом
    # (иначе шифрование молча отключается, пароли лежат в открытом виде).
    assert security._fernet is not None, "ENCRYPTION_KEY невалиден — шифрование отключено"


# --- parse_login_credentials -------------------------------------------------

def test_parse_login_credentials_valid():
    assert main.parse_login_credentials("/login user@sut.ru secret") == ("user@sut.ru", "secret")


def test_parse_login_credentials_with_bot_mention():
    assert main.parse_login_credentials("/login@SatanBonchBot user@sut.ru secret") == (
        "user@sut.ru",
        "secret",
    )


def test_parse_login_credentials_rejects_missing_args():
    assert main.parse_login_credentials("/login") is None
    assert main.parse_login_credentials("/login onlyone") is None


def test_parse_login_credentials_rejects_non_command():
    assert main.parse_login_credentials("просто текст") is None
    assert main.parse_login_credentials("") is None
    assert main.parse_login_credentials(None) is None


def test_parse_login_credentials_rejects_bad_email():
    assert main.parse_login_credentials("/login notanemail secret") is None


def test_parse_login_credentials_rejects_overlong_values():
    long_email = "a" * 250 + "@sut.ru"
    long_password = "p" * 300
    assert main.parse_login_credentials(f"/login {long_email} ok") is None
    assert main.parse_login_credentials(f"/login user@sut.ru {long_password}") is None


# --- check_login_rate_limit --------------------------------------------------

def test_rate_limit_allows_attempts_up_to_limit(reset_rate_limit):
    user_id = 1001
    results = [security.check_login_rate_limit(user_id) for _ in range(security.LOGIN_RATE_LIMIT)]
    assert all(r == 0 for r in results)


def test_rate_limit_blocks_after_limit(reset_rate_limit):
    user_id = 1002
    for _ in range(security.LOGIN_RATE_LIMIT):
        security.check_login_rate_limit(user_id)
    retry_after = security.check_login_rate_limit(user_id)
    assert retry_after > 0


def test_rate_limit_is_per_user(reset_rate_limit):
    for _ in range(security.LOGIN_RATE_LIMIT):
        security.check_login_rate_limit(7001)
    # Другой пользователь не затронут лимитом первого.
    assert security.check_login_rate_limit(7002) == 0


def test_rate_limit_ignores_attempts_outside_window(reset_rate_limit):
    user_id = 1003
    stale = _time.monotonic() - security.LOGIN_RATE_WINDOW_SEC - 60
    security._login_attempts[user_id] = [stale] * security.LOGIN_RATE_LIMIT
    # Старые попытки за пределами окна не считаются — вход снова разрешён.
    assert security.check_login_rate_limit(user_id) == 0


# --- format_retry_after ------------------------------------------------------

def test_format_retry_after_seconds():
    assert security.format_retry_after(30) == "30 сек"


def test_format_retry_after_minutes_rounds_up():
    assert security.format_retry_after(60) == "1 мин"
    assert security.format_retry_after(61) == "2 мин"
    assert security.format_retry_after(300) == "5 мин"
