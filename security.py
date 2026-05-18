"""Безопасность бота: шифрование паролей ЛК (Fernet) и rate-limit на вход.

Извлечён из main.py (задача 4.1, шаг 3) — чистая декомпозиция без изменения
поведения. Side-effects при импорте СОХРАНЕНЫ намеренно: чтение ENCRYPTION_KEY
из окружения и создание _fernet происходят на верхнем уровне, как было в
main.py. Модуль импортирует только stdlib + cryptography; проектные модули
он не импортирует.

ВАЖНО: модуль читает ENCRYPTION_KEY из окружения при импорте, поэтому
load_dotenv() (выполняется в config.py) должен отработать раньше — то есть
в main.py `import security` идёт после `import config`.
"""
import logging
import os
import time as time_module

from cryptography.fernet import Fernet, InvalidToken


# --- Шифрование паролей в users.db -------------------------------------------
# Пароли от ЛК хранятся в БД зашифрованными (Fernet, симметричный AES).
# Ключ ENCRYPTION_KEY лежит в .env. Потеря ключа = пароли не восстановить
# (пользователям придётся войти заново). Храни копию ключа отдельно и надёжно.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
try:
    _fernet = Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None
except (ValueError, TypeError):
    logging.error("ENCRYPTION_KEY задан, но невалиден — шифрование паролей ОТКЛЮЧЕНО!")
    _fernet = None
if _fernet is None:
    logging.warning(
        "ENCRYPTION_KEY не задан в .env — пароли в users.db хранятся БЕЗ шифрования. "
        "Сгенерируйте ключ: "
        "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )


def encrypt_password(password: str) -> str:
    """Шифрует пароль для хранения в БД. Без ключа возвращает значение как есть."""
    if _fernet is None:
        return password
    return _fernet.encrypt(password.encode()).decode()


def decrypt_password(stored: str) -> str:
    """
    Расшифровывает пароль из БД. Обратно совместимо со старыми plaintext-записями:
    если значение не является валидным Fernet-токеном, возвращает его как есть.
    """
    if _fernet is None or not stored:
        return stored
    try:
        return _fernet.decrypt(stored.encode()).decode()
    except InvalidToken:
        return stored


# --- Rate-limit на попытки входа (защита от перебора паролей) -----------------
# In-memory троттл: не более LOGIN_RATE_LIMIT попыток входа на user_id
# за окно LOGIN_RATE_WINDOW_SEC секунд. Настраивается через .env.
LOGIN_RATE_LIMIT = max(1, int(os.getenv("LOGIN_RATE_LIMIT", "5")))
LOGIN_RATE_WINDOW_SEC = max(1, int(os.getenv("LOGIN_RATE_WINDOW_SEC", "300")))
_login_attempts: dict[int, list[float]] = {}


def check_login_rate_limit(user_id: int) -> int:
    """
    Регистрирует попытку входа и проверяет лимит.
    Возвращает 0, если вход разрешён, либо число секунд до следующей попытки.
    """
    now = time_module.monotonic()
    window_start = now - LOGIN_RATE_WINDOW_SEC
    attempts = [t for t in _login_attempts.get(user_id, ()) if t > window_start]
    if len(attempts) >= LOGIN_RATE_LIMIT:
        _login_attempts[user_id] = attempts
        return int(attempts[0] + LOGIN_RATE_WINDOW_SEC - now) + 1
    attempts.append(now)
    _login_attempts[user_id] = attempts
    return 0


def format_retry_after(seconds: int) -> str:
    """Человекочитаемое время ожидания для сообщений пользователю."""
    if seconds >= 60:
        minutes = (seconds + 59) // 60
        return f"{minutes} мин"
    return f"{seconds} сек"
