# SatanBonchBot

[![CI](https://github.com/RealSatanyan/SatanBonchBot/actions/workflows/ci.yml/badge.svg)](https://github.com/RealSatanyan/SatanBonchBot/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/lint-ruff-46aef7.svg)](https://github.com/astral-sh/ruff)

Telegram-бот для студентов СПбГУТ («Бонч»): расписание, автоотметка на парах
через личный кабинет, чтение и отправка сообщений ЛК, напоминания о парах.

## Возможности

- **📅 Расписание** — групп, преподавателей и аудиторий. Доступно без входа в ЛК.
  После входа добавляется личное расписание. Текстом и картинкой.
- **✅ Автоотметка** — бот сам нажимает «Начать занятие» в ЛК, пока идёт пара.
  Состояние (вкл/выкл) сохраняется и переживает перезапуск бота.
- **✉️ Сообщения** — чтение входящих и отправка сообщений через ЛК,
  в т.ч. с вложением файла.
- **🔔 Уведомления** — напоминание о начале пары (настраивается: вкл/выкл и
  за сколько минут — 5/10/15/30), а также уведомления о новых сообщениях в ЛК
  и об изменениях в расписании группы.
- **👤 Профиль** — email, статус входа, настройки, повторный вход и выход.

Пароли от ЛК хранятся в `users.db` в зашифрованном виде (Fernet).

## Требования

- Python 3.10+ (в Docker-образе — 3.12)
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- Учётная запись в ЛК СПбГУТ

## Быстрый старт (локально)

```bash
cd SatanBonchBot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # затем заполните BOT_TOKEN и ENCRYPTION_KEY
python -m satanbonchbot
```

Сгенерировать `ENCRYPTION_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Запуск в Docker

```bash
docker compose up --build -d      # сборка и запуск
docker compose logs -f            # логи
docker compose ps                 # статус, в т.ч. healthy/unhealthy
```

`.env` и `users.db` пробрасываются в контейнер через volume-монтирование
(см. `docker-compose.yml`), в образ не запекаются.

Контейнер имеет **healthcheck**: бот обновляет heartbeat-файл, `healthcheck.py`
проверяет его свежесть — `docker compose ps` покажет `unhealthy`, если событийный
цикл завис. При остановке/рестарте бот корректно гасит автокликалки и фоновые
задачи (graceful shutdown).

## Переменные окружения

Полный список с комментариями — в [`.env.example`](.env.example).

| Переменная | Обяз. | Назначение |
|------------|:-----:|------------|
| `BOT_TOKEN` | да | Токен Telegram-бота. |
| `ENCRYPTION_KEY` | да | Ключ Fernet для шифрования паролей в `users.db`. Потеря = пароли не восстановить. |
| `ALL_PROXY` | нет | Прокси для запросов в ЛК (`lk.sut.ru`, `cabinet.sut.ru`). Telegram ходит напрямую. |
| `LOG_LEVEL` | нет | Уровень логов (`DEBUG`/`INFO`/`WARNING`/`ERROR`). По умолчанию `INFO`; `DEBUG` — только для отладки. |
| `TIMETABLE_TTL_HOURS` | нет | TTL кэша расписания групп в часах (по умолчанию `6`). Старше — фоновое обновление при запросе. |
| `LK_MESSAGE_POLL_MIN` | нет | Период опроса входящих ЛК для уведомлений о новых сообщениях (минуты, по умолчанию `15`). |
| `ADMIN_IDS` | нет | Telegram ID админов через запятую — кому слать алерт о поломке парсера ЛК. Пусто — алерты выключены. |
| `PARSER_ALERT_*` | нет | Окно/порог/cooldown алерта парсера (`WINDOW_MIN=30`, `THRESHOLD=3`, `COOLDOWN_MIN=60`). |
| `DEBUG_DUMPS`, `DEBUG_DUMPS_KEEP` | нет | HTML-дампы страниц ЛК для отладки парсеров. По умолчанию **выкл**; `DEBUG_DUMPS=1` включает, хранится 30 последних (см. `debug_dumps/`). |
| `LOGIN_RATE_LIMIT`, `LOGIN_RATE_WINDOW_SEC` | нет | Лимит попыток входа (по умолчанию 5 за 5 минут). |
| `LK_CONCURRENCY`, `LK_LOGIN_DELAY_SEC`, `LK_LOGIN_JITTER_SEC` | нет | Ограничение частоты запросов в ЛК. |

## Шифрование паролей

Пароли в `users.db` шифруются ключом `ENCRYPTION_KEY`. Если база уже содержит
пароли в открытом виде (со старой версии), один раз выполните миграцию:

```bash
python scripts/migrate_passwords.py            # локально
docker compose exec bot python scripts/migrate_passwords.py   # в Docker
```

Скрипт делает резервную копию `users.db` и идемпотентен (повторный запуск
безопасен). Код обратно совместим: незашифрованные записи читаются как есть.

## Тесты и линтер

```bash
pip install -r requirements-dev.txt
pytest                # тесты (конфиг — pyproject.toml)
ruff check .          # линтер (конфиг — pyproject.toml)
```

Тесты в `tests/` покрывают чистую логику: парсеры HTML, шифрование, rate-limit,
настройки, форматирование расписания, логику интервалов пар. Сетевые функции
и обработчики Telegram покрыты частично. Прогоняйте `pytest` и `ruff check .`
перед пушем — деплой через Coolify их не запускает.

На каждый push/PR в `main` GitHub Actions поднимают `ruff check .` и `pytest`
(см. бейдж `CI` вверху и [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
CI — независимая проверка качества; деплой по-прежнему делает Coolify.

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/login <email> <пароль>` | Вход в личный кабинет |
| `/help` | Справка по разделам |
| `/cancel` | Отменить текущее действие |

Основная навигация — кнопками меню снизу.

## Структура проекта

Прикладной код — в пакете `satanbonchbot/`; запуск через
`python -m satanbonchbot`. Модули разложены по слоям (конфигурация →
хранилище → чистая логика → клиенты `sut.ru` → сервисы → обработчики →
`main.py`-оркестратор). Полная карта модулей, слоёв и правил —
в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```
.
├── satanbonchbot/         # пакет: вся прикладная логика
│   ├── __main__.py        # точка входа (python -m satanbonchbot)
│   ├── main.py            # оркестратор: регистрация роутеров, polling, shutdown
│   ├── config.py · botcore.py · states.py
│   ├── db.py · security.py
│   ├── parsers.py · formatting.py · rendering.py · keyboards.py
│   ├── public_timetable.py · lk_client.py · lesson_controller.py
│   ├── *_service.py       # timetable_service, messages_service, login_service
│   └── handlers/          # aiogram-роутеры (schedule/, messages/ — поддомены)
├── tests/                 # pytest, 450+ тестов
├── docs/                  # ARCHITECTURE.md, plans/
├── scripts/               # разовые скрипты обслуживания
├── assets/fonts/          # шрифты для рендера PNG расписания
├── conftest.py            # pytest-фикстуры
├── healthcheck.py         # Docker healthcheck
├── pyproject.toml         # метаданные + конфиг ruff/pytest
├── requirements.txt · requirements-dev.txt
├── Dockerfile · docker-compose.yml · .dockerignore
├── LICENSE · README.md · .env.example
└── .github/workflows/ci.yml
```

Не коммитятся (см. `.gitignore`): `.env`, `users.db`, `timetable.json`,
`timetable*.png`, `debug_dumps/`.
