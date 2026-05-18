"""Мониторинг сбоёв парсера ЛК (задача 4.1, шаг 6).

Извлечено из main.py без изменения поведения. Если у lk.sut.ru меняется
вёрстка, автоотметка перестаёт извлекать week_param со страницы расписания.
Считаем такие сбои по разным пользователям в окне; при всплеске — одно
предупреждение админам (ADMIN_IDS), без спама.

Зависимость от aiogram-объекта `bot`: он создаётся в main.py, поэтому прямой
`from main import bot` на уровне модуля дал бы цикл импортов. Решение —
отложенный импорт `import main` внутри `_alert_admins_parser_broken`: к моменту
вызова (runtime, при сбое парсера) модуль main полностью загружен.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import pytz

from config import ADMIN_IDS


class ParserFailureMonitor:
    """Считает сбои парсера по разным пользователям в скользящем окне.
    should_alert() даёт True ровно один раз на всплеск — дальше cooldown."""

    def __init__(self, window_minutes: float = 30, threshold_users: int = 3,
                 cooldown_minutes: float = 60):
        self.window = timedelta(minutes=window_minutes)
        self.threshold_users = threshold_users
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self._events: list = []          # [(user_key, datetime)]
        self._last_alert: Optional[datetime] = None

    def _prune(self, now: datetime) -> None:
        self._events = [(u, t) for (u, t) in self._events if now - t <= self.window]

    def record_failure(self, user_key, now: datetime) -> None:
        self._events.append((user_key, now))
        self._prune(now)

    def distinct_users(self, now: datetime) -> int:
        self._prune(now)
        return len({u for (u, _t) in self._events})

    def should_alert(self, now: datetime) -> bool:
        if self.distinct_users(now) < self.threshold_users:
            return False
        if self._last_alert is not None and now - self._last_alert < self.cooldown:
            return False
        self._last_alert = now
        return True


try:
    _parser_failure_monitor = ParserFailureMonitor(
        window_minutes=float(os.getenv("PARSER_ALERT_WINDOW_MIN", "30")),
        threshold_users=int(os.getenv("PARSER_ALERT_THRESHOLD", "3")),
        cooldown_minutes=float(os.getenv("PARSER_ALERT_COOLDOWN_MIN", "60")),
    )
except ValueError:
    logging.warning("Некорректные PARSER_ALERT_* в .env — беру значения по умолчанию")
    _parser_failure_monitor = ParserFailureMonitor()


async def _alert_admins_parser_broken(distinct_users: int, window_minutes: float) -> None:
    """Одно предупреждение админам о вероятной поломке парсера ЛК."""
    if not ADMIN_IDS:
        logging.warning("Всплеск сбоёв парсера, но ADMIN_IDS не задан — алерт не отправлен")
        return
    # Отложенный импорт: main импортирует monitoring, поэтому module-level
    # `from main import bot` создал бы цикл. К моменту вызова main загружен.
    import main
    text = (
        "⚠️ <b>Похоже, сломался парсер ЛК.</b>\n\n"
        f"За последние {int(window_minutes)} мин у {distinct_users} пользователей "
        "автоотметка не смогла разобрать страницу расписания "
        "(не извлекается <code>week_param</code>).\n\n"
        "Вероятно, изменилась вёрстка lk.sut.ru. Включи <code>DEBUG_DUMPS=1</code> "
        "и посмотри HTML в <code>debug_dumps/</code>."
    )
    for admin_id in ADMIN_IDS:
        try:
            await main.bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            logging.warning("Не удалось отправить алерт админу %s", admin_id, exc_info=True)


async def _note_parser_failure(user_id) -> None:
    """Регистрирует сбой парсера; при всплеске шлёт одно предупреждение админам."""
    now = datetime.now(pytz.timezone("Europe/Moscow"))
    _parser_failure_monitor.record_failure(user_id if user_id is not None else "unknown", now)
    if _parser_failure_monitor.should_alert(now):
        distinct = _parser_failure_monitor.distinct_users(now)
        window_min = _parser_failure_monitor.window.total_seconds() / 60
        logging.warning("Всплеск сбоёв парсера ЛК: %s пользователей — шлю алерт админам", distinct)
        await _alert_admins_parser_broken(distinct, window_min)
