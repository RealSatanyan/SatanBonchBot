# Архитектура SatanBonchBot

Карта модулей, слоёв и правил проекта. Документ описывает структуру **после
декомпозиции** (задача 4.1 — разбор `main.py`, и задача A.2b — разрез
`handlers/schedule.py`). Назначение каждого модуля кратко продублировано в
его docstring.

## Слои

Зависимости направлены строго **сверху вниз**: модуль слоя N импортирует
только слои < N (исключения — поздние импорты, см. ниже). `main.py` собирает
всё вместе.

```
L6  main.py                              ← точка входа, оркестратор
        │
L5  handlers/  (common, schedule/, autoclick, messages, profile)
        │      ← обработчики aiogram (Router per file)
L4  timetable_service · messages_service · login_service
        │      ← сервисы: оркестрация прикладной логики
L3  public_timetable · lk_client · lesson_controller
        │      ← клиенты внешних сервисов sut.ru (сеть)
L2  parsers · formatting · rendering · keyboards · timetable_cache · monitoring
        │      ← чистая логика: без сети, почти без состояния
L1  db · security                        ← хранилище и безопасность
        │
L0  config · botcore · states            ← конфигурация и инфраструктура
```

| Слой | Модули | Роль |
|------|--------|------|
| **L0 — Инфраструктура** | `config.py` | Чтение окружения, логирование, прокси, константы. Импортируется первым — его import-side-effects (`load_dotenv`) должны отработать до всего. |
| | `botcore.py` | Экземпляры aiogram `Bot` и `Dispatcher`. Листовой модуль, чтобы хэндлеры/сервисы не зависели от `main.py`. |
| | `states.py` | FSM-состояния диалогов (`UIStates`). |
| **L1 — Хранилище и безопасность** | `db.py` | SQLite (`users.db`): соединение, схема, миграции, DB-хелперы. Side-effect при импорте — `connect` + `CREATE TABLE`. |
| | `security.py` | Шифрование паролей ЛК (Fernet), rate-limit на вход. Читает `ENCRYPTION_KEY` при импорте. |
| **L2 — Чистая логика** | `parsers.py` | Чистые парсеры HTML/текста с `lk.sut.ru` / `cabinet.sut.ru` (без сети). |
| | `formatting.py` | Текстовое форматирование расписания, фильтры по дате, пресеты дня. |
| | `rendering.py` | Генерация PNG расписания через PIL. |
| | `keyboards.py` | Сборщики UI-клавиатур, `BTN_*`-константы, нав-кнопки. |
| | `timetable_cache.py` | TTL-хелперы кэша расписания (метаданные снимка). |
| | `monitoring.py` | `ParserFailureMonitor` — алерты админам о поломке парсера ЛК. |
| **L3 — Клиенты sut.ru** | `public_timetable.py` | Публичное расписание `cabinet.sut.ru` без логина: список групп, расписание, фильтры. |
| | `lk_client.py` | Авторизованный клиент ЛК (`DebuggableBonchAPI`), реестр `apis`, синглтон `timetable_api`, `lk_*`-операции. |
| | `lesson_controller.py` | `LessonController` — автоотметка занятий, реестр `controllers`. |
| **L4 — Сервисы** | `timetable_service.py` | Загрузка расписания всех групп (кэш в памяти, TTL, фоновое обновление, прогресс). |
| | `messages_service.py` | Список сообщений ЛК (постраничный кэш, состояние просмотра). |
| | `login_service.py` | Авторизация пользователей в ЛК (`perform_login`, `auto_login_user`). |
| **L5 — Обработчики** | `handlers/common.py` | Старт/онбординг, `/login`, `/help`, `/cancel`, fallback, пункты reply-меню. |
| | `handlers/schedule/` | Пакет: расписание (`personal`, `group`, `teacher`, `room`, `common`). |
| | `handlers/autoclick.py` | Автоотметка занятий (команды `LessonController` + меню). |
| | `handlers/messages.py` | Сообщения ЛК (чтение списка + отправка). |
| | `handlers/profile.py` | Профиль: настройки уведомлений, повторный вход, выход. |
| **L6 — Вход** | `main.py` | Регистрация роутеров, `on_startup/shutdown`, фоновые задачи, polling. |

**Вне слоёв:** `healthcheck.py` — отдельный процесс для Docker-healthcheck
(проверяет свежесть heartbeat-файла); `scripts/` — разовые скрипты
обслуживания; `parsers.py` ↔ `public_timetable.py` — `public_timetable`
использует `parsers` (парсеры — чистый L2).

## Правила

### 1. `main.py` — фасад-реэкспорт

`main.py` реэкспортит публичные имена перенесённых модулей
(`from config import *`, `from db import ...` и т.п.), чтобы исторические
обращения `main.X` и тесты продолжали работать без правок. Поэтому в `main.py`
много «неиспользуемых» импортов — это **намеренно**; в `ruff.toml` `main.py`
исключён из правила F401.

### 2. Разделяемое изменяемое состояние — только модуль-квалифицированно

Состояние, которое **переприсваивается** на уровне модуля, читается и пишется
только через `import module; module.name` — **никогда** `from module import name`
(иначе тестовые фикстуры и переприсваивания не видны):

| Модуль | Состояние |
|--------|-----------|
| `db` | `conn`, `cursor` |
| `security` | `_login_attempts` |
| `lk_client` | `apis`, `timetable_api` |
| `lesson_controller` | `controllers` |
| `timetable_service` | `all_groups_timetable_cache`, `timetable_loading`, `timetable_progress*` |
| `messages_service` | `message_states`, `pending_lk_messages` |

### 3. Import-side-effects и порядок импортов

Модули сохраняют свои import-side-effects (декомпозиция 4.1 их **не убирала**).
Важен порядок: `config` импортируется раньше `security`/`db`, т.к.
`load_dotenv()` в `config` должен отработать до чтения `ENCRYPTION_KEY` и
`connect(users.db)`. `main.py` соблюдает этот порядок в начале файла.

### 4. Поздние импорты для разрыва циклов

Где зависимость двунаправленная, импорт делается внутри функции:
- `lk_client` ↔ `lesson_controller` (автологин создаёт контроллер);
- `monitoring._alert_admins_parser_broken` использует `bot` — через поздний импорт.

### 5. Router per file

Каждый модуль-обработчик заводит свой `aiogram.Router()`. `main.register_routers`
включает их через `include_router` в фиксированном порядке: `schedule`,
`autoclick`, `messages`, `profile`, **`common` — последним** (в нём
fallback-хэндлер `@router.message()` без фильтра, он обязан проверяться после
всех остальных). `handlers/schedule/` — под-пакет: его `__init__.py` агрегирует
саброутеры поддоменов в один `schedule.router`, поэтому `main.py` про разрез
ничего не знает.

## Тесты и линтер

- `pytest` — тесты в `tests/`, конфигурация в `pytest.ini`, фикстуры в
  `conftest.py`. Фикстуры подменяют модуль-квалифицированное состояние
  (`db.conn/db.cursor`, `security._login_attempts`).
- `ruff check .` — линтер (правила pyflakes), конфигурация в `ruff.toml`.

Перед пушем прогоняйте `ruff check .` и `pytest` — деплой через Coolify их
не запускает.
