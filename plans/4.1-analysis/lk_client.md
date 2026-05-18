# Анализ модуля `lk_client.py` (задача 4.1)

> Источник: `/root/Satanyan/SatanBonchBot/main.py` (5091 строк).
> Цель: нулевое изменение поведения, только перенос кода.
> Зона `lk_client.py` — всё, что работает с личным кабинетом lk.sut.ru: расширенный
> API-клиент, отметка занятий, отправка сообщений в ЛК, мониторинг сбоёв парсера,
> debug-дампы HTML-страниц ЛК.

---

## 1. Сводка: что переезжает в `lk_client.py`

| Символ | Тип | Строки в `main.py` | Назначение |
|---|---|---|---|
| `LK_CONCURRENCY` | const (int) | 47 | лимит параллелизма запросов в ЛК |
| `LK_LOGIN_DELAY_SEC` | const (float) | 48 | задержка между логинами |
| `LK_LOGIN_JITTER_SEC` | const (float) | 49 | джиттер задержки логина |
| `_LK_SEMAPHORE_BY_LOOP` | глобаль (dict) | 50 | семафоры по event loop |
| `LESSON_INTERVALS` | const (list) | 53–61 | сетка времён пар 1..7 |
| `get_lk_semaphore` | функция | 64–75 | ленивый семафор для текущего loop |
| `DebuggableBonchAPI` | класс | 78–471 | расширенный API-клиент ЛК |
| `_parse_admin_ids` | функция | 610–619 | парсинг `ADMIN_IDS` из `.env` |
| `ParserFailureMonitor` | класс | 622–651 | скользящее окно сбоёв парсера |
| `ADMIN_IDS` | глобаль (list) | 654 | id админов для алертов |
| `_parser_failure_monitor` | глобаль (инстанс) | 655–663 | синглтон монитора |
| `_alert_admins_parser_broken` | async-функция | 666–683 | алерт админам (использует `bot`) |
| `_note_parser_failure` | async-функция | 686–694 | регистрация сбоя + триггер алерта |
| `apis` | глобаль (dict) | 585 | `{user_id: DebuggableBonchAPI}` |
| `timetable_api` | глобаль (None/инстанс) | 696 | синглтон `TimetableBonchAPI` |
| `_prune_debug_dumps` | функция | 809–824 | ротация файлов `debug_dumps/` |
| `save_debug_dump` | функция | 827–847 | запись HTML-дампа ЛК |
| `get_timetable_api` | async-функция | 2687–2703 | ленивый `TimetableBonchAPI` без авторизации |
| `auto_login_user` | async-функция | 3919–3951 | автологин по данным из БД |
| `get_message_api` | async-функция | 3970–4007 | `TimetableBonchAPI` с куками юзера для сообщений |
| `lk_search_recipients` | async-функция | 4010–4038 | поиск получателей в ЛК |
| `lk_upload_file` | async-функция | 4041–4070 | загрузка файла в ЛК |
| `lk_send_message` | async-функция | 4073–4114 | отправка сообщения в ЛК |

**Примерный размер модуля:** ~470 строк (`DebuggableBonchAPI` + хелперы) + ~60 строк
(монитор) + ~40 строк (debug-дампы) + ~150 строк (`lk_*` / `get_*_api` / `auto_login_user`)
≈ **720–750 строк** с docstring'ами и комментариями.

> ⚠️ **`auto_login_user` — спорный кандидат.** Жёстко завязан на `LessonController`
> (создаёт `controllers[user_id] = LessonController(...)`) и на словарь `controllers`,
> которые в `lesson_controller.py` (другая зона). Возможны два варианта — см. §6.

---

## 2. Класс `DebuggableBonchAPI` (строки 78–471)

Наследует `bonchapi.BonchAPI`. Все методы переезжают целиком.

### Конструктор и состояние
- `__init__(self, *args, **kwargs)` (83–91) — вызывает `super().__init__`, создаёт
  `self.cookie_jar = aiohttp.CookieJar(unsafe=True)`, `self.cookies = None`,
  кэш `_raw_timetable_cache_html` / `_raw_timetable_cache_ts`.
  ⚠️ `CookieJar()` требует работающий event loop → инстанс нельзя создавать на уровне
  модуля; тесты обходят через `asyncio.run`.
- `_refresh_cookies_view(self)` (93–99) — наполняет `self.cookies` из `cookie_jar`
  фильтром по `YarlURL("https://lk.sut.ru/")`.

### Делегаты в `parsers.py` (чистые)
- `_get_week_safe(html) -> int` (101–103) → `parsers.parse_week_number`
- `_get_week_param_safe(html) -> int` (105–107) → `parsers.parse_week_param`
- `_extract_start_lesson_ids(html) -> tuple[str,...]` (109–111) → `parsers.extract_start_lesson_ids`
- `_extract_lesson_ids_fallback(html) -> tuple[str,...]` (367–369) → `parsers.extract_lesson_ids_fallback`

### Сетевые методы
- `async login(self, email: str, password: str) -> bool` (113–187) — авторизация
  по HTTPS, `aiohttp.ClientSession` с `self.cookie_jar`, под `get_lk_semaphore()`.
- `async get_raw_timetable(self, week_number: int = False) -> str` (189–237) —
  HTML страницы `raspisanie.php`; 30-секундный кэш для текущей недели.
- `async click_start_lesson(self, user_id=None) -> int` (371–471) — извлекает
  кнопки «Начать занятие», POST'ит клики, возвращает число успешных.
  **Покрыт тестами** (см. §5). Вызывает `_note_parser_failure` и `save_debug_dump`.

### Парсинг деталей пары
- `_parse_today_start_lesson_details(self, html, today_date_str, target_pair_number) -> Optional[dict]`
  (239–332) — парсит `simple-little-table` через `BeautifulSoup`, ищет пару по номеру.
- `async get_upcoming_start_lesson_details(self, now_dt, target_pair_index, window_minutes=15) -> Optional[dict]`
  (334–353) — проверка временного окна + парсинг. **Покрыт тестами.**
- `async get_current_lesson_details(self, now_dt, target_pair_index) -> Optional[dict]`
  (355–365) — то же без проверки времени.

### Зависимости класса
- `aiohttp` (ClientSession, CookieJar, TCPConnector, ClientTimeout)
- `parsers` (4 чистые функции)
- `bonchapi`: `BonchAPI` (база), `parser.get_lesson_id` (в `click_start_lesson`, стр. 403)
- `BeautifulSoup` (bs4)
- `YarlURL` (yarl)
- `logging`, `re`, `time_module` (`import time as time_module`), `datetime`
- модульные: `get_lk_semaphore`, `LESSON_INTERVALS`, `_note_parser_failure`,
  `save_debug_dump` — **все переезжают в тот же модуль** → внутримодульные.
- `Optional` (typing)

---

## 3. Мониторинг сбоёв парсера (строки 605–694)

- **`_parse_admin_ids(raw: Optional[str]) -> list`** (610–619) — чистая, без зависимостей.
  **Покрыта тестами** `tests/test_parser_monitor.py` (`main._parse_admin_ids`).
- **`ParserFailureMonitor`** (622–651) — чистый класс, скользящее окно, методы
  `record_failure`, `distinct_users`, `should_alert`, `_prune`. Зависит только от
  `datetime`/`timedelta`. **Покрыт тестами** (`main.ParserFailureMonitor`).
- **`ADMIN_IDS`** (654) — `_parse_admin_ids(os.getenv("ADMIN_IDS"))`, модульная глобаль.
- **`_parser_failure_monitor`** (655–663) — синглтон `ParserFailureMonitor`, конфигурируется
  через `PARSER_ALERT_WINDOW_MIN/THRESHOLD/COOLDOWN_MIN`.
- **`async _alert_admins_parser_broken(distinct_users, window_minutes)`** (666–683) —
  ⚠️ **использует глобальный `bot`** (`await bot.send_message(...)`). Межмодульная
  зависимость — см. §7.
- **`async _note_parser_failure(user_id)`** (686–694) — вызывает
  `_parser_failure_monitor.record_failure` и `_alert_admins_parser_broken`.
  Зависит от `pytz`, `datetime`.

**Внешний вызов:** `_note_parser_failure` вызывается только из
`DebuggableBonchAPI.click_start_lesson` (стр. 396) — внутри той же зоны.

---

## 4. Debug-дампы (строки 809–847)

- **`_prune_debug_dumps(dump_dir: Path) -> None`** (809–824) — оставляет N последних
  `*.html` (`DEBUG_DUMPS_KEEP`, дефолт 30). Зависит: `os`, `Path`, `logging`.
- **`save_debug_dump(prefix: str, content: str) -> Optional[Path]`** (827–847) —
  пишет дамп в `debug_dumps/`, управляется `DEBUG_DUMPS` (дефолт `0`, opt-in).
  Зависит: `os`, `Path`, `datetime`, `pytz`, `logging`, `_prune_debug_dumps`.
  **Покрыт тестами** `tests/test_main_lesson_logic.py` (`main.save_debug_dump`).

**Внешние вызовы `save_debug_dump`:**
- `DebuggableBonchAPI.click_start_lesson` стр. 418, 427 (своя зона)
- `LessonController.dump_timetable_snapshot` стр. 1101 (**другая зона** — `lesson_controller.py`)

---

## 5. Глобали `apis` / `timetable_api` и `lk_*` функции

### `apis` (dict, стр. 585)
Словарь `{user_id: DebuggableBonchAPI}`. **Активно используется снаружи зоны** —
самая высокая связность во всём модуле. Места чтения/записи:

| Строка | Контекст | Зона |
|---|---|---|
| 585 | объявление | lk_client |
| 1084–1087 | `LessonController.reauthenticate` | lesson_controller |
| 2217, 2223 | `process_image_week` | handlers |
| 2611, 2615 | `process_week_navigation` | handlers |
| 2637, 2646 | `process_my_day` | handlers |
| 2664–2673 | `cmd_timetable` | handlers |
| 3304, 3413, 3687, 3828, 3890 | сообщения / `get_message_api` | handlers / lk_client |
| 3934–3948 | `auto_login_user` | lk_client |
| 3976–3984 | `get_message_api` | lk_client |
| 4322–4326 | `perform_login` | handlers/auth |
| 4347, 4416 | `send_autoclick_panel`, `cmd_my_account` | handlers |
| 4618 | `cb_autoclick` | handlers |
| 4952 | `cb_logout` (`apis.pop`) | handlers |
| 4995 | `auto_login_all_users` | startup |

→ `apis` должен жить в `lk_client.py` и **импортироваться** многими модулями.
Так как это мутируемый словарь, импорт `from lk_client import apis` безопасен (ссылка
на один объект). Не реассайнивать `apis` в других модулях.

### `timetable_api` (стр. 696)
Синглтон `TimetableBonchAPI`, инициализируется лениво в `get_timetable_api`.
Объявлен `None`, мутируется через `global timetable_api`. Читается только внутри
`get_timetable_api`. Переезжает вместе с функцией. Внешние модули должны вызывать
`get_timetable_api()`, а не импортировать переменную (она реассайнится).

### `get_timetable_api()` (2687–2703)
Создаёт `TimetableBonchAPI(first_day=...)`, грузит `get_schet()` / `get_groups()`.
Зависит: `os` (`FIRST_DAY`), `TimetableBonchAPI`, `logging`.
Внешние вызовы: `all_groups_timetable_with_progress` (2951), `cmd_classrooms` (3502),
`cmd_groups` (3568) — все в зоне timetable/handlers.

### `auto_login_user(user_id)` (3919–3951)
Автологин по данным из БД. Зависимости:
- `cursor` (db) — `SELECT email, password`
- `decrypt_password` (security)
- `DebuggableBonchAPI`, `apis` (своя зона)
- ⚠️ `LessonController`, `controllers`, `bot` — **другая зона** (lesson_controller).
Внешние вызовы: 8 мест (`cmd_*`, `send_autoclick_panel`, `cb_autoclick`,
`get_message_api`, `auto_login_all_users`). См. §6 о размещении.

### `get_message_api(user_id) -> Optional[TimetableBonchAPI]` (3970–4007)
Берёт куки из `apis[user_id]`, при необходимости вызывает `auto_login_user`.
Зависит: `apis`, `auto_login_user`, `cursor`, `decrypt_password`, `TimetableBonchAPI`,
`os` (`FIRST_DAY`), `logging`.

### `lk_search_recipients` / `lk_upload_file` / `lk_send_message` (4010–4114)
Чистые сетевые функции поверх `message_api.cookies`. Зависят от:
`aiohttp`, `aiofiles` (только upload), `BROWSER_HEADERS` (из `TImetabels`),
`parsers.parse_recipients` (только search), `re`, `os`, `logging`.
Не трогают `apis`/`bot`/`db` напрямую. Внешние вызовы — обработчики сообщений
(`cmd_send_lk`, `handle_lk_send_callback`, `start_recipient_pick`, `fsm_write_text`).

---

## 6. Размещение `auto_login_user` — развилка

`auto_login_user` стоит на стыке трёх зон: `apis`+`DebuggableBonchAPI` (lk_client),
`controllers`+`LessonController` (lesson_controller), `bot` (telegram).

**Вариант A (рекомендуемый для шага 4.1).** Оставить `auto_login_user` в `lk_client.py`,
а `LessonController`/`controllers`/`bot` протянуть как параметры или через
позднее связывание (late import внутри функции, `from lesson_controller import ...`).
Минус: циклический импорт `lk_client ↔ lesson_controller`, лечится late import.

**Вариант B.** Перенести `auto_login_user` в `lesson_controller.py` (или отдельный
`auth.py`), а `lk_client.py` экспортирует только `DebuggableBonchAPI` + `apis`.
Чище по слоям, но это уже за рамками «только перенос» — функция меняет модуль-владельца.

Для нулевого изменения поведения на шаге 4.1 проще **Вариант A** с late import
`LessonController`. Финальное решение — за владельцем декомпозиции.

---

## 7. Межмодульная зависимость от `bot` (aiogram Bot)

`_alert_admins_parser_broken` (стр. 681) напрямую обращается к глобальному
`bot = Bot(token=BOT_TOKEN, session=tg_session)` (стр. 581). `bot` создаётся в
«ядре» `main.py` и нужен почти всем модулям.

Цепочка вызова: `click_start_lesson` → `_note_parser_failure` →
`_alert_admins_parser_broken` → `bot.send_message`. То есть `bot` нужен глубоко
внутри `lk_client.py`, в коде, который вызывается из `LessonController`.

**Варианты протяжки `bot` (от наименее к наиболее инвазивному):**

1. **Late import (минимальная правка, рекомендуется для 4.1).**
   Внутри `_alert_admins_parser_broken` сделать `from bot_core import bot`
   (или из того модуля, где останется создание `Bot`). `bot` объявлен один раз и
   не реассайнится → импорт ссылки безопасен. Поведение не меняется.
   Минус: скрытая зависимость, но это самый дешёвый «только перенос».

2. **Инъекция через сеттер.** `lk_client.py` держит модульную переменную
   `_bot = None` + `set_bot(bot)`; `main.py`/startup вызывает `set_bot(bot)` при
   инициализации. `_alert_admins_parser_broken` использует `_bot`.
   Чище, тестируемее (тест может подменить `_bot`), но добавляет шаг инициализации.

3. **Передача `bot` параметром по всей цепочке.** `click_start_lesson(user_id, bot=...)`
   → `_note_parser_failure(user_id, bot)` → `_alert_admins_parser_broken(..., bot)`.
   Чисто архитектурно, но меняет публичную сигнатуру `click_start_lesson` →
   ⚠️ **ломает тесты** `test_click_lesson.py` (вызывают `click_start_lesson(user_id=1)`)
   и требует прокинуть `bot` через `LessonController`. Не «только перенос».

4. **Отдельный модуль алертов.** Вынести `_alert_admins_parser_broken` + `ADMIN_IDS`
   в `admin_alerts.py`/`notifications.py`, который импортирует `bot`. `lk_client.py`
   тогда зависит от него, а не от `bot` напрямую. Логично, но плодит модуль.

**Рекомендация для шага 4.1:** Вариант 1 (late import `bot`) либо Вариант 2 (сеттер).
Оба сохраняют сигнатуры и поведение. Вариант 2 предпочтительнее с точки зрения
тестируемости (легко замокать `bot` без monkeypatch модульного импорта).

---

## 8. Влияние на тесты

### 8.1 `tests/test_click_lesson.py` — КРИТИЧНО

Тест мокает по таргетам `main.*`:
- `monkeypatch.setattr(main.aiohttp, "ClientSession", session_cls)` (`_patch_network`)
- `monkeypatch.setattr(main.aiohttp, "TCPConnector", lambda **kw: object())`
- `api = main.DebuggableBonchAPI()` (`_api_with_timetable`)

После переноса `DebuggableBonchAPI` в `lk_client.py`:
- `import main` → `import lk_client` (или добавить `import lk_client`).
- `main.DebuggableBonchAPI()` → `lk_client.DebuggableBonchAPI()`.
- ⚠️ **Мок-таргет `aiohttp` должен указывать на модуль, где используется имя.**
  `aiohttp` импортируется в `lk_client.py` как `import aiohttp`, и
  `click_start_lesson`/`login`/`get_raw_timetable` обращаются к `aiohttp.ClientSession`
  через атрибут модуля `aiohttp`. Поскольку `aiohttp` — один и тот же объект-модуль,
  технически `monkeypatch.setattr(main.aiohttp, ...)` и
  `monkeypatch.setattr(lk_client.aiohttp, ...)` патчат **один объект** (сам модуль
  `aiohttp`), поэтому патч сработает независимо от таргета.
  Тем не менее для ясности следует переключить на `lk_client.aiohttp`.
- `monkeypatch.setattr` на `TCPConnector` — аналогично, патчит атрибут модуля `aiohttp`,
  работает из любого таргета. Менять на `lk_client.aiohttp` для консистентности.

**Итог по `test_click_lesson.py`:** обязательно — заменить `main.DebuggableBonchAPI`
на `lk_client.DebuggableBonchAPI`; желательно — `main.aiohttp` → `lk_client.aiohttp`.
Сами тестовые сценарии и фикстуры (`raspisanie_*.html`) не меняются. Поведение
`click_start_lesson` / `get_upcoming_start_lesson_details` идентично.

> Тонкость: `click_start_lesson` вызывает `_note_parser_failure` и `save_debug_dump`.
> В тестах `_note_parser_failure` отрабатывает (HTML фикстур валиден → `week_param`
> найден, ветка не триггерится), `save_debug_dump` возвращает `None` при
> `DEBUG_DUMPS` по умолчанию `0`. После переноса эти функции — внутримодульные
> в `lk_client.py`, тесты остаются зелёными без правок логики.

### 8.2 `tests/test_parser_monitor.py`
Использует `main.ParserFailureMonitor` и `main._parse_admin_ids`.
После переноса → `lk_client.ParserFailureMonitor`, `lk_client._parse_admin_ids`.
Логика чистая, тесты зелёные после смены импортов.

### 8.3 `tests/test_main_lesson_logic.py`
Использует `main.DebuggableBonchAPI` (фикстура, стр. 14) и `main.save_debug_dump`
(стр. 155–182). После переноса → `lk_client.*`. Тесты `save_debug_dump` патчат
`DEBUG_DUMPS`/`DEBUG_DUMPS_KEEP` через env и `Path` — переезжают вместе с функцией.

### 8.4 `conftest.py` / `tests/conftest.py`
- Корневой `conftest.py`: фикстура `temp_db` подменяет `main.conn`/`main.cursor`.
  `auto_login_user`/`get_message_api` читают `cursor`. Если они переедут в
  `lk_client.py` и будут импортировать `cursor` из db-модуля, фикстуру нужно
  обновить под новый модуль (вне зоны 4.1 lk_client, но отметить для согласования).
- `tests/conftest.py` — фикстура `load_fixture`, не зависит от размещения. Без правок.

---

## 9. Зависимости от других будущих модулей

| Внешний символ | Откуда | Где используется в зоне lk_client |
|---|---|---|
| `BonchAPI` | `bonchapi` (внешний пакет) | база `DebuggableBonchAPI` |
| `parser.get_lesson_id` | `bonchapi` (внешний) | `click_start_lesson` стр. 403 |
| `parsers.*` | `parsers.py` (уже вынесен) | делегаты в `DebuggableBonchAPI` |
| `TimetableBonchAPI`, `BROWSER_HEADERS` | `TImetabels.py` | `get_timetable_api`, `get_message_api`, `lk_*` |
| `bot` | telegram/ядро (`main.py`) | `_alert_admins_parser_broken` — см. §7 |
| `LessonController`, `controllers` | будущий `lesson_controller.py` | `auto_login_user` — см. §6 |
| `cursor` / `conn` | будущий `db.py` | `auto_login_user`, `get_message_api` (SELECT) |
| `decrypt_password` / `encrypt_password` | будущий `security.py` | `auto_login_user`, `get_message_api` |
| `BOT_TOKEN`, env-константы | будущий `config.py` | косвенно (через `os.getenv` в коде модуля) |

### Зависимость от `config` (константы)
`lk_client.py` читает `os.getenv` напрямую для: `LK_CONCURRENCY`, `LK_LOGIN_DELAY_SEC`,
`LK_LOGIN_JITTER_SEC`, `ADMIN_IDS`, `PARSER_ALERT_WINDOW_MIN/THRESHOLD/COOLDOWN_MIN`,
`DEBUG_DUMPS`, `DEBUG_DUMPS_KEEP`, `FIRST_DAY`. Если появится `config.py` — эти
`os.getenv` заменяются импортом констант; на шаге 4.1 можно оставить `os.getenv`
на месте (нулевое изменение поведения), модуль самодостаточен.

### Зависимость от `security`
`auto_login_user` и `get_message_api` вызывают `decrypt_password`. `perform_login`
(зона auth/handlers) вызывает `encrypt_password`. Если `auto_login_user`/`get_message_api`
остаются в `lk_client.py` — нужен импорт `decrypt_password` из будущего `security.py`.

### Зависимость от `db`
`auto_login_user` и `get_message_api` обращаются к `cursor` (`SELECT email, password
FROM users`). При выносе `db.py` — импорт `cursor`. ⚠️ Фикстура `temp_db` подменяет
`main.conn`/`main.cursor`; после декомпозиции она должна подменять атрибут в
db-модуле, а `lk_client.py` должен обращаться к `db.cursor` (а не импортировать
`cursor` как имя — иначе подмена фикстуры не подхватится). Это важный нюанс для
сохранения тестируемости.

---

## 10. Риски

| Риск | Серьёзность | Митигация |
|---|---|---|
| `bot` нужен глубоко в `lk_client` (`_alert_admins_parser_broken`) | **высокая** | late import или сеттер (§7) — НЕ менять сигнатуру `click_start_lesson` |
| Циклический импорт `lk_client ↔ lesson_controller` (`auto_login_user`↔`LessonController`) | **высокая** | late import внутри функции, либо вынести `auto_login_user` (§6) |
| `apis` импортируется ~10 модулями; реассайн в чужом модуле порвёт ссылку | средняя | импортировать как `from lk_client import apis`, мутировать, НЕ реассайнить |
| `cursor` через прямой импорт имени → фикстура `temp_db` перестанет подменять | средняя | обращаться `db.cursor`, обновить `temp_db` |
| Мок-таргет `main.aiohttp`/`main.DebuggableBonchAPI` в тестах | средняя | переключить на `lk_client.*`; `aiohttp` патчится корректно (один объект-модуль) |
| `timetable_api` — реассайнимая глобаль | низкая | внешние модули вызывают `get_timetable_api()`, не импортируют переменную |
| `DebuggableBonchAPI()` требует event loop (CookieJar) | низкая | поведение не меняется, тесты уже используют `asyncio.run` |
| Side-effect при импорте: `ADMIN_IDS`/`_parser_failure_monitor` инициализируются на уровне модуля | низкая | сохранить порядок; импорт `lk_client` потянет `os.getenv` — как и сейчас в `main.py` |

---

## 11. Порядок переноса (предложение)

1. Создать `lk_client.py`, перенести «чистое ядро»: `LESSON_INTERVALS`, семафор,
   `DebuggableBonchAPI`, `ParserFailureMonitor`, `_parse_admin_ids`, debug-дампы.
2. Решить вопрос `bot` (сеттер `set_bot` или late import) для `_alert_admins_parser_broken`.
3. Перенести `apis`, `timetable_api`, `get_timetable_api`, `lk_*`.
4. Решить судьбу `auto_login_user`/`get_message_api` (§6) — late import `LessonController`.
5. В `main.py` оставить `from lk_client import (...)` для обратной совместимости
   обработчиков, либо точечно поправить импорты.
6. Обновить тесты: `test_click_lesson.py`, `test_parser_monitor.py`,
   `test_main_lesson_logic.py` — сменить `main.*` → `lk_client.*`.
7. Прогнать `pytest` — все 135 тестов должны остаться зелёными.
