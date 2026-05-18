"""Конфигурация бота: константы, чтение окружения, логирование, прокси.

Извлечён из main.py (задача 4.1, шаг 1) — чистая декомпозиция без изменения
поведения. Side-effects при импорте СОХРАНЕНЫ намеренно: load_dotenv(),
logging.basicConfig(...), мутация os.environ под прокси. Это лист графа
зависимостей — модуль импортирует только stdlib + dotenv + yarl.
"""
import asyncio
import logging
import os
import sys
from datetime import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from yarl import URL as YarlURL


# КРИТИЧНО — порядок: в исходном main.py LK_CONCURRENCY/LK_LOGIN_DELAY_SEC/
# LK_LOGIN_JITTER_SEC читались из env ДО вызова load_dotenv(). Порядок сохранён
# намеренно (нулевое изменение поведения). Это вероятный скрытый баг исходника
# (значения берутся только из реального окружения процесса, не из .env) —
# чинить не в рамках этой задачи.
# Ограничиваем частоту/параллелизм запросов к lk.sut.ru, чтобы не ловить антибот/ERR_MSG/403
LK_CONCURRENCY = max(1, int(os.getenv("LK_CONCURRENCY", "1")))
LK_LOGIN_DELAY_SEC = float(os.getenv("LK_LOGIN_DELAY_SEC", "1.5"))
LK_LOGIN_JITTER_SEC = float(os.getenv("LK_LOGIN_JITTER_SEC", "1.0"))
_LK_SEMAPHORE_BY_LOOP = {}

# Единые интервалы пар для напоминаний/автоотметки.
LESSON_INTERVALS = [
    (time(9, 0), time(10, 35)),    # 1 пара
    (time(10, 45), time(12, 20)),  # 2 пара
    (time(13, 0), time(14, 35)),   # 3 пара
    (time(14, 45), time(16, 20)),  # 4 пара
    (time(16, 30), time(18, 5)),   # 5 пара
    (time(18, 15), time(19, 50)),  # 6 пара
    (time(20, 0), time(21, 35)),   # 7 пара
]


def get_lk_semaphore() -> asyncio.Semaphore:
    """
    В Python 3.9 asyncio.Semaphore привязывается к event loop при создании.
    Поэтому создаем семафор лениво для текущего loop (иначе получаем
    'Future attached to a different loop').
    """
    loop = asyncio.get_running_loop()
    sem = _LK_SEMAPHORE_BY_LOOP.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(LK_CONCURRENCY)
        _LK_SEMAPHORE_BY_LOOP[loop] = sem
    return sem


def _resolve_log_level(name: Optional[str]) -> int:
    """LOG_LEVEL из .env → числовой уровень logging. Неизвестное значение → INFO."""
    level = getattr(logging, (name or "").strip().upper(), None)
    return level if isinstance(level, int) else logging.INFO


load_dotenv()

# Уровень логирования берётся из .env (LOG_LEVEL), по умолчанию INFO.
# DEBUG в проде раздувает логи и может писать чувствительные данные.
_LOG_LEVEL = _resolve_log_level(os.getenv("LOG_LEVEL", "INFO"))
logging.basicConfig(
    level=_LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
# aiogram на DEBUG/INFO очень шумный — держим не ниже WARNING, если это не отладка.
logging.getLogger('aiogram').setLevel(
    logging.DEBUG if _LOG_LEVEL <= logging.DEBUG else logging.WARNING
)

BOT_TOKEN = os.getenv('BOT_TOKEN')

# Прокси нужен ТОЛЬКО для запросов в ЛК (lk.sut.ru).
# Напрямую, без прокси, ходят: Telegram (api.telegram.org) и публичное расписание
# (cabinet.sut.ru, www.sut.ru) — последнее через прокси отвечает таймаутом.
#
# Прокси прокидывается через стандартные переменные HTTP(S)_PROXY: aiohttp-сессии для
# sut.ru создаются с trust_env=True и подхватывают их автоматически. Хосты из NO_PROXY
# при этом исключаются и идут напрямую. Telegram-сессия (aiogram AiohttpSession) env не
# читает вовсе, так что для неё прокси не применяется в любом случае.
NO_PROXY_HOSTS = "api.telegram.org,cabinet.sut.ru,www.sut.ru"
LK_PROXY = os.getenv("ALL_PROXY")
if LK_PROXY:
    for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[_proxy_var] = LK_PROXY
    for _no_proxy_var in ("NO_PROXY", "no_proxy"):
        os.environ[_no_proxy_var] = NO_PROXY_HOSTS
    logging.info("Прокси для ЛК (lk.sut.ru) включён: %s", YarlURL(LK_PROXY).with_user(None))
    logging.info("Напрямую, без прокси: %s", NO_PROXY_HOSTS)
else:
    logging.info("ALL_PROXY не задан — запросы в ЛК идут напрямую")


# --- Heartbeat для Docker healthcheck ---------------------------------------
# Пока событийный цикл жив, heartbeat_loop периодически обновляет mtime файла.
# healthcheck.py (отдельный процесс) проверяет его свежесть. См. docker-compose.yml.
HEARTBEAT_FILE = Path(os.getenv("HEARTBEAT_FILE", "/tmp/satanbot_heartbeat"))
HEARTBEAT_INTERVAL_SEC = 30


def _write_heartbeat(path: Path) -> None:
    """Обновляет mtime heartbeat-файла — метка «событийный цикл жив»."""
    try:
        path.touch()
    except Exception:
        logging.warning("Не удалось обновить heartbeat-файл %s", path, exc_info=True)


def _parse_admin_ids(raw: Optional[str]) -> list:
    """Парсит ADMIN_IDS из .env: '123, 456' -> [123, 456]. Мусор пропускается."""
    if not raw:
        return []
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.append(int(part))
    return ids


ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS"))
