# SatanBonchBot

Telegram-бот для работы с личным кабинетом СПбГУТ (Бонч): просмотр расписания, уведомления по парам и сценарии автоотметки/кликов по занятиям.

## Что умеет

- Авторизация в ЛК и работа через `bonchapi`.
- Получение и форматирование расписания.
- Генерация изображения расписания (Pillow + emoji/fonts).
- Хранение пользовательских данных в SQLite (`users.db`).
- Запуск локально или в Docker.

## Требования

- Python 3.9+
- Telegram Bot Token
- Доступ к ЛК СПбГУТ

## Быстрый старт (локально)

1. Перейдите в каталог проекта:
   - `cd SatanBonchBot`
2. Создайте и активируйте окружение:
   - `python -m venv .venv`
   - `source .venv/bin/activate`
3. Установите зависимости:
   - `pip install -r requirements.txt`
4. Создайте файл `.env`:
   - `BOT_TOKEN=<ваш_токен_бота>`
   - `ALL_PROXY=<опционально, proxy URL для Telegram>`
5. Запустите бота:
   - `python main.py`

## Запуск в Docker

- Сборка и запуск:
  - `docker compose up --build -d`
- Логи:
  - `docker compose logs -f`

## Примечания по файлам

- `main.py` — основной код Telegram-бота.
- `TImetabels.py` — работа с расписанием/парсером.
- `timetable.json` — временный кэш расписания (не коммитится).
- `__pycache__/` и `*.pyc` — служебные файлы Python (не коммитятся).

## Переменные окружения

- `BOT_TOKEN` — токен Telegram-бота (обязательно).
- `ALL_PROXY` — прокси для Telegram-сессии (опционально).
- `LK_CONCURRENCY`, `LK_LOGIN_DELAY_SEC`, `LK_LOGIN_JITTER_SEC` — параметры ограничения запросов в ЛК (опционально, заданы дефолты в коде).
