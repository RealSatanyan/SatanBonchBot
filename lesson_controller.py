"""Контроллер автоотметки занятий (задача 4.1, шаг 11).

Извлечён из main.py без изменения поведения. Содержит:
- `LessonController` — управляет фоновым циклом автоотметки и напоминаниями;
- `controllers` — реестр контроллеров {user_id: LessonController}.

Направление зависимостей: lesson_controller -> lk_client / config / db /
security (вниз по слоям). Разделяемое изменяемое состояние lk_client.apis
и db.conn/db.cursor доступно через `import module; module.name`, чтобы подмена
в тестовых фикстурах была видна. Модуль НЕ импортирует main на уровне модуля —
цикла зависимостей нет.
"""
import asyncio
import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import pytz

import db
import lk_client
from config import LESSON_INTERVALS
from db import get_notify_settings
from lk_client import save_debug_dump
from security import decrypt_password

# Реестр контроллеров автоотметки. Читается/пишется хэндлерами в main.py
# (auto_login_user, perform_login, меню автокликалки, on_shutdown) через
# модуль-квалифицированный доступ lesson_controller.controllers.
controllers = {}  # Словарь для хранения контроллеров


class LessonController:
    def __init__(self, api, bot, user_id):
        self.api = api
        self.bot = bot
        self.user_id = user_id
        self.is_running = False
        self.task = None
        self.notified = False  # Флаг для отслеживания отправки уведомления
        self._last_success_lesson_key: Optional[str] = None
        self._last_upcoming_lesson_key: Optional[str] = None

        # Интервалы пар (начало и конец)
        self.lesson_intervals = LESSON_INTERVALS

    def _current_lesson_interval_index(self, now_time: time) -> Optional[int]:
        """
        Возвращает индекс интервала пары (0..6), если сейчас идёт пара, иначе None.
        """
        for i, (start_time, end_time) in enumerate(self.lesson_intervals):
            if self.is_time_between(start_time, end_time, now_time):
                return i
        return None

    def _upcoming_lesson_interval_index(
        self,
        now_dt: datetime,
        min_minutes_before_start: int = 9,
        max_minutes_before_start: int = 10,
    ) -> Optional[int]:
        """
        Возвращает индекс пары, если до её начала осталось от min до max минут.
        Нужен для стабильного напоминания "за 10 минут" с учётом минутного тика.
        """
        for i, (start_time, _end_time) in enumerate(self.lesson_intervals):
            # Используем дату now_dt и тот же TZ
            start_dt = datetime.combine(now_dt.date(), start_time, tzinfo=now_dt.tzinfo)
            delta_min = (start_dt - now_dt).total_seconds() / 60.0
            if min_minutes_before_start <= delta_min <= max_minutes_before_start:
                return i
        return None

    def is_time_between(self, start_time, end_time, now_time):
        """Проверка, находится ли текущее время в заданном интервале."""
        if start_time <= end_time:
            return start_time <= now_time <= end_time
        else:  # Интервал переходит через полночь
            return start_time <= now_time or now_time <= end_time

    def is_lesson_time(self, now_time):
        """Проверка, находится ли текущее время в интервале любой из пар."""
        for start_time, end_time in self.lesson_intervals:
            if self.is_time_between(start_time, end_time, now_time):
                return True
        return False

    async def start_lesson(self):
        if self.is_running:
            return "Автокликалка уже запущена."

        self.is_running = True
        moscow_tz = pytz.timezone('Europe/Moscow')

        while self.is_running:
            try:
                now_dt = datetime.now(moscow_tz)
                now = now_dt.time()

                # Напоминание о начале пары (один раз на пару). Включение и
                # «за сколько минут» настраиваются пользователем в разделе «Профиль».
                # Диапазон (N-1)..N нужен из-за периодической проверки раз в минуту.
                notify_enabled, notify_minutes = get_notify_settings(self.user_id)
                upcoming_idx = self._upcoming_lesson_interval_index(
                    now_dt,
                    min_minutes_before_start=max(1, notify_minutes - 1),
                    max_minutes_before_start=notify_minutes,
                ) if notify_enabled else None
                if upcoming_idx is not None:
                    lesson_key = f"{now_dt.strftime('%Y-%m-%d')}_upcoming_{upcoming_idx}"
                    if self._last_upcoming_lesson_key != lesson_key:
                        try:
                            start_time, _end_time = self.lesson_intervals[upcoming_idx]
                            start_dt = datetime.combine(now_dt.date(), start_time, tzinfo=now_dt.tzinfo)
                            minutes_left = max(0, int((start_dt - now_dt).total_seconds() // 60))
                            human_idx = upcoming_idx + 1

                            details = await self.api.get_upcoming_start_lesson_details(
                                now_dt=now_dt,
                                target_pair_index=upcoming_idx,
                                window_minutes=notify_minutes,
                            )
                            # Интервалы пар (LESSON_INTERVALS) — это просто сетка времени.
                            # Уведомляем ТОЛЬКО если эта пара реально есть в расписании
                            # на сегодня (details найдены). Нет пары -> молчим, ключ не
                            # фиксируем, чтобы при сбое загрузки расписания был ретрай.
                            if not details:
                                logging.info(
                                    "Пара %s в %s не отправлена: нет в расписании на сегодня (user_id=%s)",
                                    human_idx,
                                    now_dt.strftime("%H:%M"),
                                    self.user_id,
                                )
                            else:
                                room = details.get("room") or "—"
                                subject = details.get("subject") or ""
                                teacher = details.get("teacher") or ""
                                subj_part = f"\n📚 {subject}" if subject else ""
                                room_part = f"\n🚪 Аудитория: {room}" if room and room != "—" else f"\n🚪 Аудитория: —"
                                teacher_part = f"\n👨‍🏫 {teacher}" if teacher else ""
                                msg = (
                                    f"🔔 Через {minutes_left} мин начнётся {human_idx}-я пара."
                                    f"{subj_part}{room_part}{teacher_part}"
                                )

                                await self.bot.send_message(self.user_id, msg)
                                self._last_upcoming_lesson_key = lesson_key
                                logging.info(
                                    "Отправлено напоминание о паре: user_id=%s, pair=%s, minutes_left=%s",
                                    self.user_id,
                                    human_idx,
                                    minutes_left,
                                )
                        except Exception as notify_error:
                            logging.warning(
                                "Не удалось отправить напоминание о паре для user_id=%s: %s",
                                self.user_id,
                                notify_error,
                                exc_info=True,
                            )

                if self.is_lesson_time(now):
                    # Если уведомление еще не отправлено, отправляем его
                    if not self.notified:
                        self.notified = True  # Устанавливаем флаг, что уведомление отправлено

                    # Пытаемся выполнить клик
                    logging.debug("Попытка кликнуть занятие для пользователя %s", self.user_id)
                    clicked = await self.api.click_start_lesson(self.user_id)
                    if clicked > 0:
                        logging.info("Клик выполнен. Отправлено запросов: %s", clicked)
                        # Оповещение в TG: ровно одно сообщение на одну пару
                        now_dt = datetime.now(moscow_tz)
                        interval_idx = self._current_lesson_interval_index(now_dt.time())
                        # Ключ пары: дата + номер интервала (если по какой-то причине idx=None,
                        # то fallback на дату+час, чтобы не спамить)
                        if interval_idx is None:
                            lesson_key = now_dt.strftime("%Y-%m-%d_%H")
                        else:
                            lesson_key = f"{now_dt.strftime('%Y-%m-%d')}_lesson_{interval_idx}"

                        if self._last_success_lesson_key != lesson_key:
                            try:
                                await self.bot.send_message(
                                    self.user_id,
                                    "✅ Автоотметка: отметка выполнена.",
                                )
                                self._last_success_lesson_key = lesson_key
                            except Exception as mark_notify_error:
                                logging.warning(
                                    "Не удалось отправить сообщение об автоотметке для user_id=%s: %s",
                                    self.user_id,
                                    mark_notify_error,
                                    exc_info=True,
                                )
                    else:
                        logging.warning("Клик не выполнен: кандидатов для клика не найдено.")
                else:
                    # Если время пар закончилось, сбрасываем флаг уведомления
                    self.notified = False
                    logging.info("Сейчас не время пар. Клик не выполнен.")
                await asyncio.sleep(60)  # Минутный тик для точного напоминания перед парой
            except ValueError as e:
                # Обрабатываем ошибку истекшей сессии
                if "Session expired" in str(e) or "login=no" in str(e):
                    logging.warning(f"Сессия истекла для пользователя {self.user_id}. Попытка переавторизации...")
                    try:
                        # Пытаемся переавторизоваться
                        await self.reauthenticate()
                        logging.info(f"Переавторизация успешна для пользователя {self.user_id}")
                    except Exception as reauth_error:
                        logging.error(f"Ошибка переавторизации для пользователя {self.user_id}: {reauth_error}")
                        await self.bot.send_message(self.user_id, "⚠️ Ваша сессия истекла. Пожалуйста, выполните /login для повторной авторизации.")
                        self.is_running = False
                        break
                else:
                    if "LK group not defined" in str(e):
                        logging.error("В ЛК не назначена группа для пользователя %s — автоклик невозможен.", self.user_id)
                        try:
                            await self.bot.send_message(
                                self.user_id,
                                "⚠️ В вашем ЛК не назначена учебная группа (страница расписания пишет: «Ваша группа не определена»).\n"
                                "Автоотметка не сможет работать, пока деканат не проведёт приказ о распределении в группу.\n\n"
                                "После назначения группы — выполните /login ещё раз и запустите /start_lesson.",
                            )
                        except Exception:
                            pass
                        self.is_running = False
                        break
                    logging.error(f"Ошибка при выполнении клика: {e}", exc_info=True)
                    await self.capture_debug_artifacts(e)
                await asyncio.sleep(60)  # Пауза перед повторной попыткой (1 минута)
                continue  # Продолжаем цикл для повторной попытки
            except Exception as e:
                logging.error(f"Ошибка при выполнении клика: {e}", exc_info=True)
                await self.capture_debug_artifacts(e)
                await asyncio.sleep(60)  # Пауза перед повторной попыткой (1 минута)
                continue  # Продолжаем цикл для повторной попытки

        return "Автокликалка запущена."

    async def stop_lesson(self, user_id: int):
        if not self.is_running:
            return "Автокликалка уже остановлена."

        self.is_running = False
        if self.task:
            self.task.cancel()
            logging.info(f'Пользователь {user_id} остановил автокликалку.')
        return "Автокликалка остановлена."

    async def get_status(self):
        return "Автокликалка запущена." if self.is_running else "Автокликалка остановлена."

    async def reauthenticate(self):
        """
        Переавторизует пользователя при истечении сессии.
        """
        db.cursor.execute('SELECT email, password FROM users WHERE user_id = ?', (self.user_id,))
        result = db.cursor.fetchone()
        if not result:
            raise ValueError(f"Не найдены данные для переавторизации пользователя {self.user_id}")

        email, password = result
        password = decrypt_password(password)
        # Создаем новый экземпляр API и авторизуемся
        lk_client.apis[self.user_id] = lk_client.DebuggableBonchAPI()
        await lk_client.apis[self.user_id].login(email, password)
        # Обновляем ссылку на API в контроллере
        self.api = lk_client.apis[self.user_id]

    async def dump_timetable_snapshot(self, reason: str) -> Optional[Path]:
        """
        Сохраняет HTML расписания для дальнейшего анализа.
        """
        try:
            raw_html = await self.api.get_raw_timetable()

            # Проверяем, не является ли ответ редиректом на login=no (истекшая сессия)
            if raw_html and ("login=no" in raw_html or "index.php?login=no" in raw_html):
                logging.warning("Обнаружен редирект на login=no - сессия истекла. Не сохраняем снимок. (%s)", reason)
                raise ValueError("Session expired - redirect to login=no")

            file_path = save_debug_dump(str(self.user_id), raw_html)
            if file_path:
                logging.error("Снимок расписания сохранен в %s (%s)", file_path, reason)
            return file_path
        except ValueError:
            # Не логируем ValueError как ошибку - это ожидаемая ситуация с истекшей сессией
            raise
        except Exception as dump_error:
            logging.error("Не удалось сохранить снимок расписания: %s", dump_error, exc_info=True)
            return None

    async def capture_debug_artifacts(self, error: Exception):
        """
        Снимает дополнительную отладочную информацию для проблемных сценариев.
        """
        # Не сохраняем снимки при истекшей сессии
        if isinstance(error, ValueError) and ("Session expired" in str(error) or "login=no" in str(error)):
            logging.warning("Пропускаем сохранение снимка из-за истекшей сессии")
            return

        if isinstance(error, AttributeError) and "NoneType" in str(error):
            try:
                await self.dump_timetable_snapshot("parser_get_week_failed")
            except ValueError:
                # Игнорируем ошибки истекшей сессии при сохранении снимка
                pass
