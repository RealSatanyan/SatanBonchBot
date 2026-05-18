#!/usr/bin/env python3
"""Docker healthcheck: проверяет свежесть heartbeat-файла бота.

Пока событийный цикл бота жив, `heartbeat_loop` в main.py периодически
обновляет mtime heartbeat-файла. Если файл отсутствует или устарел —
цикл, скорее всего, завис или умер.

Запускается как отдельный процесс из docker-compose healthcheck:
    python -u healthcheck.py
Выход 0 — здоров, 1 — нездоров. Импортировать модуль безопасно:
никаких побочных эффектов при импорте (в отличие от main.py).
"""
import os
import sys
import time
from pathlib import Path

HEARTBEAT_FILE = Path(os.getenv("HEARTBEAT_FILE", "/tmp/satanbot_heartbeat"))
HEARTBEAT_MAX_AGE_SEC = float(os.getenv("HEARTBEAT_MAX_AGE_SEC", "120"))


def check(path: Path, max_age_sec: float) -> tuple[bool, str]:
    """Возвращает (здоров?, человекочитаемое сообщение)."""
    if not path.exists():
        return False, f"heartbeat-файл отсутствует: {path}"
    age = time.time() - path.stat().st_mtime
    if age > max_age_sec:
        return False, f"heartbeat устарел: {age:.0f}s > {max_age_sec:.0f}s"
    return True, f"ok, heartbeat обновлён {age:.0f}s назад"


def main() -> int:
    ok, message = check(HEARTBEAT_FILE, HEARTBEAT_MAX_AGE_SEC)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
