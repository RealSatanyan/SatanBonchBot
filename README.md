# SatanBonchBot

Telegram-бот для студентов СПбГУТ («Бонч»): расписание, автоотметка на парах
через личный кабинет, чтение и отправка сообщений ЛК, напоминания о парах.

## Возможности

- **📅 Расписание** — групп, преподавателей и аудиторий. Доступно без входа в ЛК.
  После входа добавляется личное расписание. Текстом и картинкой.
- **✅ Автоотметка** — бот сам нажимает «Начать занятие» в ЛК, пока идёт пара.
  Состояние (вкл/выкл) сохраняется и переживает перезапуск бота.
- **✉️ Сообщения** — чтение входящих и отправка сообщений через ЛК.
- **🔔 Уведомления** — напоминание о начале пары. Настраивается: вкл/выкл и
  за сколько минут (5/10/15/30).
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
python main.py
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
pytest                # тесты
ruff check .          # линтер (конфигурация — ruff.toml)
```

Тесты в `tests/` покрывают чистую логику: парсеры HTML, шифрование, rate-limit,
настройки, форматирование расписания, логику интервалов пар. Сетевые функции
и обработчики Telegram покрыты частично. Прогоняйте `pytest` и `ruff check .`
перед пушем — деплой через Coolify их не запускает.

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/login <email> <пароль>` | Вход в личный кабинет |
| `/help` | Справка по разделам |
| `/cancel` | Отменить текущее действие |

Основная навигация — кнопками меню снизу.

## Структура проекта

Проект разбит на модули по слоям (конфигурация → хранилище → чистая логика →
клиенты sut.ru → сервисы → обработчики → `main.py`). Полная карта модулей,
слоёв и правил — в [`ARCHITECTURE.md`](ARCHITECTURE.md).

| Путь | Назначение |
|------|------------|
| `main.py` | Точка входа: регистрация роутеров, старт/остановка, polling. |
| `config.py`, `botcore.py`, `states.py` | Конфигурация, экземпляры `Bot`/`Dispatcher`, FSM-состояния. |
| `db.py`, `security.py` | SQLite (`users.db`) и шифрование паролей / rate-limit. |
| `parsers.py`, `formatting.py`, `rendering.py`, `keyboards.py` | Чистая логика: парсеры, форматирование, PNG, клавиатуры. |
| `TImetabels.py`, `lk_client.py`, `lesson_controller.py` | Клиенты `sut.ru`: публичное расписание, ЛК, автоотметка. |
| `*_service.py` | Сервисы: загрузка расписания, сообщения ЛК, авторизация. |
| `handlers/` | Обработчики aiogram (`schedule/` — под-пакет по поддоменам). |
| `tests/`, `conftest.py`, `pytest.ini` | Тесты и их конфигурация. |
| `ruff.toml` | Конфигурация линтера. |
| `scripts/` | Разовые скрипты обслуживания (`migrate_passwords.py` — уже выполнен). |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | Сборка и запуск в Docker. |
| `*.ttf`, `*.otf` | Шрифты для рендеринга картинки расписания. |
| `plans/` | Планы развития проекта. |

Не коммитятся (см. `.gitignore`): `.env`, `users.db`, `timetable.json`,
`timetable.png`, `debug_dumps/`.
