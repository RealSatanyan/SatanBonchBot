"""Сервис авторизации пользователей в ЛК (задача 4.1, шаг 12d).

Извлечён из main.py без изменения поведения. Содержит:
- `parse_login_credentials` — парсинг команды `/login email password`;
- `perform_login` — вход в ЛК и сохранение учётных данных в БД при успехе;
- `auto_login_user` — автоматическая авторизация пользователя из БД;
- `auto_start_lesson` — автозапуск автокликалки при старте бота;
- константы валидации логина (`LOGIN_CMD_RE`, `EMAIL_RE`, `MAX_EMAIL_LEN`,
  `MAX_PASSWORD_LEN`).

`perform_login` и `auto_login_user` создают `DebuggableBonchAPI` и
`LessonController`, регистрируя их в реестрах `lk_client.apis` и
`lesson_controller.controllers`. Доступ к реестрам — строго
модуль-квалифицированный (`import lk_client; lk_client.apis`,
`import lesson_controller; lesson_controller.controllers`), иначе
`from import`-копия зафиксирует устаревшую ссылку. Доступ к БД — через
`db.cursor` / `db.conn`.

Стартовая оркестрация автологина всех пользователей (`auto_login_all_users`)
остаётся в main.py и вызывает `auto_login_user` / `auto_start_lesson` через
реэкспорт.

Направление зависимостей: login_service -> lesson_controller -> lk_client,
а также -> botcore, security, db (вниз по слоям). Модуль НЕ импортирует main
на уровне модуля — цикла зависимостей нет.
"""
import asyncio
import logging
import re

from botcore import bot
import lk_client
import lesson_controller
from lesson_controller import LessonController
import db
from db import get_autoclick_enabled
from security import decrypt_password, encrypt_password

LOGIN_CMD_RE = re.compile(r"^/login(?:@\w+)?\s+(\S+)\s+(\S+)\s*$")
MAX_EMAIL_LEN = 254
MAX_PASSWORD_LEN = 256
# Email должен быть похож на email: это отсекает мусор и попытки инъекций
# (email подставляется в URL запроса авторизации в ЛК).
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def parse_login_credentials(message_text: str) -> tuple[str, str] | None:
    """
    Извлекает email и password только из ожидаемого формата /login.
    Возвращает None при невалидном формате.
    """
    if not message_text:
        return None

    match = LOGIN_CMD_RE.match(message_text.strip())
    if not match:
        return None

    email, password = match.group(1), match.group(2)
    if len(email) > MAX_EMAIL_LEN or len(password) > MAX_PASSWORD_LEN:
        return None

    if not EMAIL_RE.match(email):
        return None

    return email, password


async def auto_login_user(user_id):
    """
    Автоматически авторизует пользователя, если он есть в базе данных.
    Возвращает True, если авторизация успешна, False в противном случае.
    """
    db.cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
    result = db.cursor.fetchone()
    if not result:
        logging.info(f"Пользователь {user_id} не найден в базе данных.")
        return False

    email, password = result
    password = decrypt_password(password)
    logging.info(f"Попытка автоматической авторизации для пользователя {user_id} (email: {email})")
    try:
        lk_client.apis[user_id] = lk_client.DebuggableBonchAPI()
        ok = await lk_client.apis[user_id].login(email, password)
        if not ok:
            raise ValueError("auto_login_failed")

        lesson_controller.controllers[user_id] = LessonController(lk_client.apis[user_id], bot, user_id)  # Передаем bot и user_id

        logging.info("✅ Пользователь %s успешно автоматически авторизован.", user_id)
        return True
    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ Ошибка автоматической авторизации для пользователя {user_id} (email: {email}): {error_msg}", exc_info=True)
        # Удаляем частично созданные объекты при ошибке
        if user_id in lk_client.apis:
            del lk_client.apis[user_id]
        if user_id in lesson_controller.controllers:
            del lesson_controller.controllers[user_id]
        return False

async def auto_start_lesson(user_id):
    """
    Автоматически запускает автокликалку для пользователя при старте бота —
    но только если пользователь не выключил её вручную (autoclick_enabled).
    """
    if not get_autoclick_enabled(user_id):
        logging.info(
            "Автокликалка для пользователя %s выключена вручную — не запускаем при старте бота.",
            user_id,
        )
        return
    if user_id in lesson_controller.controllers:  # Проверяем, есть ли контроллер для пользователя
        controller = lesson_controller.controllers[user_id]
        if not controller.is_running:  # Если автокликалка не запущена, запускаем её
            controller.task = asyncio.create_task(controller.start_lesson())
            logging.info(f"Автокликалка автоматически запущена для пользователя {user_id}.")


async def perform_login(user_id: int, email: str, password: str) -> bool:
    """Входит в ЛК и сохраняет данные в БД только при успешном входе."""
    try:
        api = lk_client.DebuggableBonchAPI()
        ok = await api.login(email, password)
        if not ok:
            return False
        lk_client.apis[user_id] = api
        lesson_controller.controllers[user_id] = LessonController(api, bot, user_id)
        db.cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        existing = db.cursor.fetchone()
        encrypted_password = encrypt_password(password)
        with db.conn:
            if existing:
                db.cursor.execute('UPDATE users SET email = ?, password = ? WHERE user_id = ?',
                                  (email, encrypted_password, user_id))
            else:
                db.cursor.execute('INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)',
                                  (user_id, email, encrypted_password))
        return True
    except Exception as e:
        logging.error("perform_login: ошибка для %s: %s", user_id, e, exc_info=True)
        return False


__all__ = [
    "LOGIN_CMD_RE",
    "EMAIL_RE",
    "MAX_EMAIL_LEN",
    "MAX_PASSWORD_LEN",
    "parse_login_credentials",
    "auto_login_user",
    "auto_start_lesson",
    "perform_login",
]
