"""TTL-кэш расписания групп (задача 4.1, шаг 7).

Извлечено из main.py без изменения поведения. Чистые хелперы для работы с
sidecar-файлом метаданных расписания (timetable.meta.json): запись/чтение
метки времени, расчёт возраста кэша, проверка устаревания, человекочитаемое
форматирование возраста.

timetable.json (~58 МБ) формат не трогаем — метаданные пишем в sidecar-файл.
Если кэш старше TTL, при запросе группы он обновляется в фоне, а пользователю
сразу отдаются текущие (пусть и слегка устаревшие) данные.

Модуль почти лист графа: зависит только от stdlib и pytz, не импортирует
`main` или другие проектные модули. Сервис загрузки расписания
(`get_all_groups_timetable`, `_refresh_timetable_quietly`) остаётся в main.py.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz

# --- TTL-кэш расписания групп ------------------------------------------------
TIMETABLE_META_FILE = Path("timetable.meta.json")
try:
    TIMETABLE_TTL_HOURS = float(os.getenv("TIMETABLE_TTL_HOURS", "6"))
except ValueError:
    TIMETABLE_TTL_HOURS = 6.0


def _write_timetable_meta(fetched_at: datetime, path: Path = TIMETABLE_META_FILE) -> None:
    """Записывает метку времени загрузки расписания в sidecar-файл."""
    try:
        path.write_text(
            json.dumps({"fetched_at": fetched_at.isoformat()}),
            encoding="utf-8",
        )
    except Exception:
        logging.warning("Не удалось записать %s", path, exc_info=True)


def _read_timetable_meta(path: Path = TIMETABLE_META_FILE) -> Optional[dict]:
    """Читает sidecar с метаданными расписания. None — нет файла / битый."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _timetable_age_seconds(meta: Optional[dict], now: datetime) -> Optional[float]:
    """Возраст кэша расписания в секундах. None — нет валидной метки."""
    if not meta or "fetched_at" not in meta:
        return None
    try:
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        return (now - fetched_at).total_seconds()
    except (ValueError, TypeError):
        return None


def _is_timetable_stale(age_seconds: Optional[float], ttl_hours: float) -> bool:
    """Кэш устарел? Отсутствие метки (None) считаем устаревшим."""
    if age_seconds is None:
        return True
    return age_seconds > ttl_hours * 3600


def _format_cache_age(age_seconds: Optional[float]) -> str:
    """Человекочитаемый возраст кэша: «5 мин назад», «3 ч назад»."""
    if age_seconds is None:
        return "время неизвестно"
    if age_seconds < 60:
        return "только что"
    minutes = int(age_seconds // 60)
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = int(age_seconds // 3600)
    return f"{hours} ч назад"


def _timetable_cache_age_now() -> Optional[float]:
    """Возраст кэша расписания на текущий момент (по московскому времени)."""
    now = datetime.now(pytz.timezone("Europe/Moscow"))
    return _timetable_age_seconds(_read_timetable_meta(), now)
