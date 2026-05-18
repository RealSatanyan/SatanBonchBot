"""
Разовая миграция: шифрует plaintext-пароли в users.db (задача 1.2 плана).

Запуск из каталога проекта:
    python migrate_passwords.py

Что делает:
  1. Читает ENCRYPTION_KEY из .env.
  2. Делает резервную копию users.db -> users.db.bak-<timestamp>.
  3. Для каждой записи: если пароль ещё не зашифрован — шифрует и перезаписывает.
     Уже зашифрованные записи пропускаются, поэтому скрипт идемпотентен
     (повторный запуск ничего не ломает).

ВАЖНО: запускать на той же машине, где лежит актуальный .env с ключом.
Без ENCRYPTION_KEY миграция невозможна.
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime

from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken

DB_PATH = "users.db"


def main() -> int:
    load_dotenv()
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        print("ОШИБКА: ENCRYPTION_KEY не задан в .env. Миграция невозможна.")
        return 1

    try:
        fernet = Fernet(key.encode())
    except (ValueError, TypeError):
        print("ОШИБКА: ENCRYPTION_KEY невалиден.")
        return 1

    if not os.path.exists(DB_PATH):
        print(f"ОШИБКА: {DB_PATH} не найден. Запускайте скрипт из каталога проекта.")
        return 1

    backup_path = f"{DB_PATH}.bak-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(DB_PATH, backup_path)
    print(f"Резервная копия создана: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("SELECT user_id, password FROM users").fetchall()
        encrypted_count = 0
        skipped_count = 0

        for user_id, password in rows:
            if password is None:
                skipped_count += 1
                continue
            try:
                fernet.decrypt(password.encode())
                # Уже зашифровано — пропускаем.
                skipped_count += 1
                continue
            except InvalidToken:
                pass  # plaintext — шифруем ниже

            new_value = fernet.encrypt(password.encode()).decode()
            conn.execute(
                "UPDATE users SET password = ? WHERE user_id = ?",
                (new_value, user_id),
            )
            encrypted_count += 1

        conn.commit()
    finally:
        conn.close()

    print(f"Зашифровано записей: {encrypted_count}")
    print(f"Пропущено (уже зашифровано / пусто): {skipped_count}")
    print("Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
