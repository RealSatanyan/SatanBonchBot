"""Домен сообщений ЛК lk.sut.ru (задача B.2 фазы 5).

Вынесено из lk_client.py: получение клиента для работы с сообщениями
(`get_message_api`), поиск получателей, загрузка файла-вложения и отправка
сообщения в ЛК. Методы `get_messages_page`/`get_message` остаются на
`DebuggableBonchAPI` — это методы самого клиента.

Направление зависимостей: lk_messages -> lk_client (вниз по слою L3).
lk_client домен сообщений НЕ импортирует — цикла нет. `apis` и `_lk_fetch`
берутся модуль-квалифицированно (`lk_client.apis` мутируется на месте).
"""

import logging
import os
import re
from typing import Optional

import aiohttp
import aiofiles

import db
import lk_client
import parsers
from config import BROWSER_HEADERS
from security import decrypt_password
from lk_client import DebuggableBonchAPI

__all__ = [
    "LK_MAX_FILE_SIZE_MB",
    "get_message_api",
    "lk_search_recipients",
    "lk_upload_file",
    "lk_send_message",
]

# Предел размера файла для вложения в сообщение ЛК. У ЛК свой лимит загрузки
# (точное значение не подтверждено документацией — оценка ~5 МБ), у Telegram
# bot-download — 20 МБ. Берём меньший: проверять размер ДО скачивания файла.
# TODO: уточнить реальный лимит ЛК опытным путём и поправить значение.
LK_MAX_FILE_SIZE_MB = 5


async def get_message_api(user_id: int) -> Optional[DebuggableBonchAPI]:
    """
    Возвращает авторизованный клиент ЛК пользователя для работы с сообщениями.

    После задачи A.1 это сам ``DebuggableBonchAPI`` пользователя (отдельный
    TimetableBonchAPI больше не создаётся — методы сообщений живут в нём же).
    При протухших куках выполняет переавторизацию по данным из БД.
    Возвращает None, если авторизоваться не удалось.
    """
    # Проверяем, есть ли уже авторизованный API для пользователя
    if user_id not in lk_client.apis:
        # Пытаемся автоматически авторизовать (auto_login_user остаётся в main).
        import main
        success = await main.auto_login_user(user_id)
        if not success or user_id not in lk_client.apis:
            logging.warning(f"Не удалось получить API для пользователя {user_id}")
            return None

    existing_api = lk_client.apis[user_id]
    if not hasattr(existing_api, 'cookies') or not existing_api.cookies:
        # Куки протухли — пробуем переавторизоваться по данным из БД.
        logging.warning(f"У пользователя {user_id} нет cookies в API — переавторизация")
        db.cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (user_id,))
        result = db.cursor.fetchone()
        if not result:
            return None
        email, password = result
        await existing_api.login(email, decrypt_password(password))
        if not hasattr(existing_api, 'cookies') or not existing_api.cookies:
            return None

    return existing_api


async def lk_search_recipients(message_api: DebuggableBonchAPI, query: str):
    """
    Поиск получателей в ЛК по ФИО через страницу поиска subconto.
    Возвращает список словарей вида {'id': int, 'label': 'ФИО И.О. (id=...)'}.
    """
    URL = "https://lk.sut.ru/cabinet/subconto/search.php"

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
            # Поиск идемпотентен — _lk_fetch повторит запрос при разовом сбое,
            # иначе пользователь видел бы ложное «получатель не найден».
            status, html_text = await lk_client._lk_fetch(
                session, "GET", URL, params={"value": query}, cookies=message_api.cookies)

        if status >= 400:
            logging.error("Поиск получателей %r: HTTP %s", query, status)
            return []

        # Парсим строки вида "ФИО (id=12345)"
        results = parsers.parse_recipients(html_text)

        if results:
            logging.info("Найдено %s получателей по запросу %r", len(results), query)
        else:
            logging.warning(
                "Поиск получателей %r: 0 совпадений (HTTP %s, длина %s). Фрагмент ответа: %s",
                query, status, len(html_text or ""),
                (html_text or "")[:400].replace("\n", " "),
            )
        return results
    except Exception as e:
        logging.error(f"Ошибка при поиске получателей в ЛК: {type(e).__name__} {e}", exc_info=True)
        return []


async def lk_upload_file(message_api: DebuggableBonchAPI, filename: str, id: int = 0) -> int:
    """
    Загрузка файла в ЛК с использованием cookies уже авторизованного API.
    Возвращает idinfo (>0) — идентификатор вложения для lk_send_message,
    либо 0 при ошибке.

    Намеренно НЕ через _lk_fetch (A.1): тело идёт multipart/form-data через
    aiohttp.FormData — объект одноразовый, а слепой повтор плодил бы дубль-файлы
    в ЛК. При сбое — честная ошибка (0), пользователь повторяет осознанно.
    """
    URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message_create_stud.php'

    try:
        async with aiofiles.open(filename, 'rb') as f:
            file = await f.read()

        data = aiohttp.FormData()
        data.add_field("id", str(id))
        data.add_field("upload", "")
        data.add_field('userfile', file, filename=os.path.basename(filename))

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
            async with session.post(URL, cookies=message_api.cookies, data=data, proxy=None) as response:
                response.raise_for_status()
                text = await response.text()
                match = re.search(r'data\.idinfo = "(\d+)"', text)
                if not match:
                    logging.error("Не удалось извлечь idinfo из ответа при загрузке файла")
                    return 0
                idinfo = match.group(1)
                logging.info('Файл успешно загружен в ЛК, idinfo=%s', idinfo)
                return int(idinfo)
    except Exception as e:
        logging.error(f'Ошибка при загрузке файла в ЛК: {type(e).__name__} {e}', exc_info=True)
        return 0


async def lk_send_message(
    message_api: DebuggableBonchAPI,
    recipient_id: int,
    title: str,
    message_text: str,
    idinfo: int = 0,
) -> bool:
    """
    Отправка сообщения в ЛК с использованием cookies уже авторизованного API.
    idinfo>0 — к сообщению прикрепляется ранее загруженный файл (lk_upload_file).
    """
    URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message.php'

    data = {
        "idinfo": str(idinfo),
        "item": '0',
        "title": title,
        "mes_otvet": message_text,
        "adresat": str(recipient_id),
        "saveotv": ''
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
            # idempotent=False: отправка не идемпотентна — слепой повтор после
            # успешного POST создаст дубль. _lk_fetch ретраит только заведомо
            # «доотправочные» сбои (соединение не установлено); неоднозначный
            # сбой после отправки пробрасывается сюда → честная ошибка, без дубля.
            status, text = await lk_client._lk_fetch(
                session, "POST", URL, cookies=message_api.cookies, data=data,
                idempotent=False)

        if status >= 400:
            logging.error('HTTP %s при отправке сообщения в ЛК (adresat=%s)', status, recipient_id)
            return False
        # Успех — пустой ответ; ЛК часто отдаёт его как пробелы/перевод строки.
        if text.strip() == '':
            logging.info('Сообщение в ЛК успешно отправлено (adresat=%s)', recipient_id)
            return True
        # Сервер иногда возвращает ошибку про link_url, но сообщение всё равно отправляется
        # Проверяем, является ли это только ошибкой про link_url
        if 'link_url' in text.lower() and 'undefined index' in text.lower():
            logging.warning('Сервер вернул предупреждение про link_url, но сообщение должно быть отправлено (adresat=%s)', recipient_id)
            return True
        logging.error('Ошибка при отправке сообщения в ЛК, ответ сервера: %r', text)
        return False
    except Exception as e:
        logging.error(f'Ошибка при отправке сообщения в ЛК: {type(e).__name__} {e}', exc_info=True)
        return False
