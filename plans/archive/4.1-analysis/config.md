# Задача 4.1 — анализ модуля `config.py`

Анализ `main.py` (5091 строк) на предмет того, что должно переехать в `config.py`:
конфигурация, чтение окружения, константы, логирование. **Код не менялся — только анализ.**

Цель: НУЛЕВОЕ изменение поведения. План v2 требует, чтобы `import main` (и `import config`)
не имел side-effects — настройку окружения/логирования вынести в явную функцию инициализации.

---

## 1. Таблица символов — кандидаты в `config.py`

### 1.1 Чистые константы (можно вынести без side-effects)

| Имя | Тип | Строки | Зависит от | Кто ссылается снаружи |
|-----|-----|--------|-----------|------------------------|
| `LESSON_INTERVALS` | const (list[tuple[time,time]]) | 53–61 | `datetime.time` | `LessonController.__init__` (862, `self.lesson_intervals`), `_parse_today_start_lesson_details` (343), упоминание в коммент. 940. Сетка времени пар. |
| `NO_PROXY_HOSTS` | const (str) | 504 | — | блок настройки прокси 506–512 (тот же модуль) |
| `HEARTBEAT_INTERVAL_SEC` | const (int = 30) | 591 | — | `heartbeat_loop` (5026) |
| `MESSAGES_CACHE_TTL_SEC` | const (int = 300) | 705 | — | вызов `_messages_cache_fresh` (3681) |
| `MAX_EMAIL_LEN` | const (int = 254) | 748 | — | `parse_login_credentials` (768) |
| `MAX_PASSWORD_LEN` | const (int = 256) | 749 | — | `parse_login_credentials` (768) |
| `LOGIN_CMD_RE` | const (compiled regex) | 747 | `re` | `parse_login_credentials` (763) |
| `EMAIL_RE` | const (compiled regex) | 752 | `re` | `parse_login_credentials` (771), хэндлер логина (4479) |
| `TIMETABLE_META_FILE` | const (Path) | 2836 | `pathlib.Path` | `_write_timetable_meta`/`_read_timetable_meta` (default-аргумент, 2843/2854) |

Примечание: `LOGIN_CMD_RE`, `EMAIL_RE`, `MAX_EMAIL_LEN`, `MAX_PASSWORD_LEN` логически
ближе к валидации `/login` (модуль auth/validation), чем к config. Перенос в `config.py`
возможен, но лучше согласовать границу с зоной, отвечающей за парсинг логина.
`re.compile` — детерминированный, side-effect-free, поэтому остаётся «чистым» где угодно.

### 1.2 Значения из окружения (`os.getenv` на уровне модуля)

Все они выполняются **при импорте** — это side-effect (чтение env). Сами по себе env-read
безопасны и идемпотентны, но зависят от того, был ли уже вызван `load_dotenv()`.

| Имя | Тип | Строки | Env-переменная / default | Зависит от | Кто ссылается снаружи |
|-----|-----|--------|--------------------------|-----------|------------------------|
| `LK_CONCURRENCY` | const (int) | 47 | `LK_CONCURRENCY` / `"1"` | `os` | `get_lk_semaphore` (73) |
| `LK_LOGIN_DELAY_SEC` | const (float) | 48 | `LK_LOGIN_DELAY_SEC` / `"1.5"` | `os` | `auto_login_all_users` (5005) |
| `LK_LOGIN_JITTER_SEC` | const (float) | 49 | `LK_LOGIN_JITTER_SEC` / `"1.0"` | `os` | `auto_login_all_users` (5005) |
| `_LOG_LEVEL` | global (int) | 483 | `LOG_LEVEL` / `"INFO"` | `_resolve_log_level`, `load_dotenv()` | `logging.basicConfig` + aiogram level (484–492) |
| `BOT_TOKEN` | const (str\|None) | 494 | `BOT_TOKEN` | `os` | `Bot(token=BOT_TOKEN ...)` (581) |
| `LK_PROXY` | const (str\|None) | 505 | `ALL_PROXY` | `os` | блок прокси 506–514 |
| `ENCRYPTION_KEY` | const (str\|None) | 520 | `ENCRYPTION_KEY` | `os` | создание `_fernet` (522) |
| `HEARTBEAT_FILE` | const (Path) | 590 | `HEARTBEAT_FILE` / `/tmp/satanbot_heartbeat` | `os`, `Path` | `heartbeat_loop` (5025), `on_startup` (5035), `on_shutdown` (5076) |
| `ADMIN_IDS` | const (list[int]) | 654 | `ADMIN_IDS` | `_parse_admin_ids`, `os` | `_alert_admins_parser_broken` (668, 679) |
| `LOGIN_RATE_LIMIT` | const (int) | 780 | `LOGIN_RATE_LIMIT` / `"5"` | `os` | `check_login_rate_limit` (793) |
| `LOGIN_RATE_WINDOW_SEC` | const (int) | 781 | `LOGIN_RATE_WINDOW_SEC` / `"300"` | `os` | `check_login_rate_limit` (791, 795) |
| `TIMETABLE_TTL_HOURS` | const (float) | 2838–2840 | `TIMETABLE_TTL_HOURS` / `"6"` | `os` | вызов `_is_timetable_stale` (2997) |

Env-переменные, читаемые **внутри функций** (НЕ на уровне модуля — не side-effect при импорте,
оставить как есть либо завернуть в `config`-геттер по желанию):

| Где читается | Env | Строки | Default |
|--------------|-----|--------|---------|
| `_prune_debug_dumps` | `DEBUG_DUMPS_KEEP` | 812 | `"30"` |
| `save_debug_dump` | `DEBUG_DUMPS` | 835 | `"0"` |
| `setup_first_day` / хэндлер (дважды) | `FIRST_DAY` | 2697, 4000 | `'2026-02-03'` |
| `_parser_failure_monitor` init | `PARSER_ALERT_WINDOW_MIN`, `PARSER_ALERT_THRESHOLD`, `PARSER_ALERT_COOLDOWN_MIN` | 657–659 | `"30"`/`"3"`/`"60"` |

`PARSER_ALERT_*` читаются на уровне модуля внутри `try`-блока (655–663) при создании
`_parser_failure_monitor` — это уже не чистый config, а инстанс монитора (см. 1.5).

### 1.3 Функции config-уровня

| Имя | Тип | Строки | Зависит от | Кто ссылается снаружи |
|-----|-----|--------|-----------|------------------------|
| `_resolve_log_level` | func | 473–476 | `logging`, `Optional` | используется в 483 для `_LOG_LEVEL` |
| `_parse_admin_ids` | func | 610–619 | `Optional` | используется в 654 для `ADMIN_IDS` |
| `get_lk_semaphore` | func | 64–75 | `asyncio`, `LK_CONCURRENCY`, `_LK_SEMAPHORE_BY_LOOP` | `DebuggableBonchAPI.login` (132), `get_raw_timetable` (216), и др. вызовы клика по ЛК |
| `_write_heartbeat` | func | 597–602 | `logging`, `Path` | `heartbeat_loop` (5025), `on_startup` (5035) |

### 1.4 Глобальное изменяемое состояние

| Имя | Тип | Строки | Назначение | Кто ссылается |
|-----|-----|--------|-----------|----------------|
| `_LK_SEMAPHORE_BY_LOOP` | global (dict) | 50 | кэш `asyncio.Semaphore` по event loop (Python 3.9 привязка к loop) | `get_lk_semaphore` (71–74) |

### 1.5 Пограничные символы — НЕ чистый config, требуют решения

| Имя | Строки | Почему пограничный |
|-----|--------|---------------------|
| блок настройки прокси | 506–514 | Императивный код на уровне модуля: мутирует `os.environ`, пишет в лог. Side-effect. Должен стать частью `init`-функции. |
| `logging.basicConfig(...)` + aiogram level | 484–492 | Настройка логирования при импорте — side-effect. Должна стать `setup_logging()`. |
| `load_dotenv()` | 479 | Side-effect при импорте (читает `.env` с диска). Должна вызываться явно в `init`. |
| `_fernet` / `encrypt_password` / `decrypt_password` | 520–551 | Шифрование. `_fernet` создаётся при импорте (зависит от `ENCRYPTION_KEY`). Логически это «security», не config. Рекомендуется отдельный модуль `crypto.py`/`security.py`, либо отложенная инициализация. |
| `_parser_failure_monitor` + `ParserFailureMonitor` | 622–663 | Класс монитора + его инстанс. Инстанс создаётся при импорте и читает `PARSER_ALERT_*`. Это доменная логика мониторинга, не config — место классу в `monitoring.py`. В `config.py` можно вынести только дефолты `PARSER_ALERT_*`. |
| `tg_session`, `bot`, `dp` | 555, 581–582 | Создание сетевых/aiogram-объектов при импорте — тяжёлый side-effect. НЕ config; зона ядра приложения (`app.py`/`bot.py`). |
| `conn`, `cursor`, миграции БД | 556–579 | Открытие SQLite + DDL при импорте — side-effect. НЕ config; зона `db.py`. |
| `TIMETABLE_META_FILE` файл-функции `_write/_read_timetable_meta` | 2843–2859 | Сам путь — чистая константа (1.1). Функции чтения/записи — доменные (timetable), их место не в `config.py`. |

---

## 2. Зависимости

### 2.1 Импорты, нужные `config.py`

- `os` — все `os.getenv`, `os.environ`.
- `sys` — `logging.StreamHandler(sys.stdout)`.
- `logging` — `_resolve_log_level`, `basicConfig`, уровни, `_write_heartbeat`.
- `asyncio` — `get_lk_semaphore` (`get_running_loop`, `Semaphore`).
- `re` — `LOGIN_CMD_RE`, `EMAIL_RE` (если их забирать в config).
- `pathlib.Path` — `HEARTBEAT_FILE`, `TIMETABLE_META_FILE`.
- `datetime.time` — `LESSON_INTERVALS`.
- `dotenv.load_dotenv` — функция инициализации.
- `yarl.URL` (`YarlURL`) — только для лог-строки прокси (511), скрывает user в URL.

### 2.2 Граф зависимостей внутри config-кандидатов

```
load_dotenv()  ─┐
                ├─► все os.getenv(...) на уровне модуля должны выполняться ПОСЛЕ load_dotenv()
_resolve_log_level ─► _LOG_LEVEL ─► logging.basicConfig / aiogram level
_parse_admin_ids   ─► ADMIN_IDS
LK_CONCURRENCY     ─► get_lk_semaphore (через _LK_SEMAPHORE_BY_LOOP)
LK_PROXY + NO_PROXY_HOSTS ─► блок мутации os.environ (init-функция)
ENCRYPTION_KEY     ─► _fernet (вне config — см. 1.5)
```

**Важный нюанс порядка:** в текущем `main.py` `LK_CONCURRENCY`, `LK_LOGIN_DELAY_SEC`,
`LK_LOGIN_JITTER_SEC` (строки 47–49) читаются из env **ДО** вызова `load_dotenv()` (строка 479).
То есть сейчас эти три значения берутся ТОЛЬКО из реального окружения процесса, а НЕ из `.env`.
Это, скорее всего, скрытый баг исходного кода. **Чтобы сохранить НУЛЕВОЕ изменение поведения,
в `config.py` нужно либо сохранить этот порядок (env-read до `load_dotenv`), либо явно
зафиксировать смену поведения как осознанное исправление.** Без этого декомпозиция изменит
поведение для развёртываний, где эти переменные заданы только в `.env`.

### 2.3 Обратные зависимости (кто импортирует config-символы)

Будущий `config.py` будет импортироваться практически всеми модулями: классом
`DebuggableBonchAPI` (`get_lk_semaphore`, `LESSON_INTERVALS`), `LessonController`
(`LESSON_INTERVALS`), heartbeat-кодом (`HEARTBEAT_FILE`, `HEARTBEAT_INTERVAL_SEC`,
`_write_heartbeat`), кэшами (`MESSAGES_CACHE_TTL_SEC`, `TIMETABLE_TTL_HOURS`),
rate-limit (`LOGIN_RATE_*`), мониторингом (`ADMIN_IDS`), ядром (`BOT_TOKEN`).
`config.py` сам не должен импортировать ничего из проектных модулей — это лист графа,
чтобы избежать циклов.

---

## 3. Side-effects при импорте

Сейчас `import main` выполняет следующие side-effects, относящиеся к config-зоне:

| # | Side-effect | Строки | Тип | Можно сделать чистым? |
|---|-------------|--------|-----|------------------------|
| 1 | `load_dotenv()` — чтение `.env` с диска | 479 | I/O (файл) | НЕТ — вынести в `init_config()` / `setup()` |
| 2 | `os.getenv(...)` × ~12 на уровне модуля | 47–49, 483, 494, 505, 520, 590, 654, 780–781, 2838 | чтение env | Идемпотентно, но зависит от (1). Завернуть в lazy-getter или собрать в dataclass, создаваемый в `init`. |
| 3 | `logging.basicConfig(...)` + aiogram `setLevel` | 484–492 | глобальная мутация logging | НЕТ — вынести в `setup_logging()` |
| 4 | мутация `os.environ` HTTP(S)_PROXY / NO_PROXY | 506–510 | глобальная мутация процесса | НЕТ — вынести в `setup_proxy()` / `init_config()` |
| 5 | `logging.info(...)` про прокси | 511–514 | вывод в лог | следствие (4); внутри `init` |
| 6 | `re.compile(...)` для `LOGIN_CMD_RE`, `EMAIL_RE` | 747, 752 | вычисление | ЧИСТЫЙ — можно оставить на уровне модуля |
| 7 | литералы `LESSON_INTERVALS`, `NO_PROXY_HOSTS`, числовые TTL/лимиты | 53–61, 504, 591, 705, 748–749 | вычисление | ЧИСТЫЙ — оставить на уровне модуля |
| 8 | `_LK_SEMAPHORE_BY_LOOP = {}` | 50 | создание пустого dict | ЧИСТЫЙ — но это mutable global; держать приватным, доступ только через `get_lk_semaphore` |

Side-effects вне config-зоны, но рядом (для ясности границ): создание `_fernet` (522),
`tg_session`/`bot`/`dp` (555/581/582), `sqlite3.connect` + DDL (556–579), создание
`_parser_failure_monitor` (655–663). Эти НЕ идут в `config.py`.

### Рекомендуемая структура `config.py`

1. **Чистая часть (уровень модуля, без side-effects):** все литеральные константы из 1.1,
   функции `_resolve_log_level`, `_parse_admin_ids`, `get_lk_semaphore`, `_write_heartbeat`,
   приватный `_LK_SEMAPHORE_BY_LOOP`. `import config` после этого безопасен.
2. **Отложенная инициализация:** функция `init_config()` (или `load_settings()`), которая
   вызывает `load_dotenv()`, читает все env-переменные и возвращает immutable-объект
   настроек (`@dataclass(frozen=True) Settings`), а также `setup_logging(level)` и
   `setup_proxy(settings)`. Вызывать их явно из `main()`/точки входа, НЕ при импорте.
3. Значения, которые сейчас читаются внутри функций (`DEBUG_DUMPS*`, `FIRST_DAY`,
   `PARSER_ALERT_*`), можно либо оставить как есть (они уже lazy), либо перенести в
   `Settings`. Для нулевого изменения поведения проще оставить чтение там же на шаг 4.1.

---

## 4. Риски извлечения

1. **Порядок `load_dotenv` vs ранние `os.getenv` (строки 47–49).** Главный риск. Сейчас
   `LK_CONCURRENCY`/`LK_LOGIN_DELAY_SEC`/`LK_LOGIN_JITTER_SEC` читаются ДО `load_dotenv()`.
   В новом модуле порядок легко «починится» неявно — и это изменит поведение. Решение
   принять явно: либо сохранить багги-порядок, либо задокументировать фикс.
2. **`logging.basicConfig` вызывается ровно один раз.** `basicConfig` — no-op при повторном
   вызове, если хендлеры уже стоят. Если `config.py` импортируется раньше, чем что-либо
   ещё настроит logging, поведение сохранится. Риск — если другой модуль настроит logging
   первым: тогда `setup_logging()` станет no-op. Вызывать `setup_logging()` самым первым в `init`.
3. **Мутация `os.environ` (прокси).** Глобальный side-effect на процесс. Если перенести в
   `config.py`, но забыть вызвать `setup_proxy()` — запросы к ЛК пойдут напрямую (тихая
   деградация, не падение). Нужно гарантировать вызов в точке входа.
4. **`get_lk_semaphore` и привязка к event loop (Python 3.9).** Семафор кэшируется по loop.
   `_LK_SEMAPHORE_BY_LOOP` должен остаться единственным экземпляром (модульный синглтон).
   Риск двойного импорта/перезагрузки модуля — низкий, но `_LK_SEMAPHORE_BY_LOOP` обязан
   быть приватным и не дублироваться.
5. **`BOT_TOKEN` / `ENCRYPTION_KEY` могут быть `None`.** Сейчас при импорте `main.py`
   значения просто читаются; падение происходит позже (`Bot(token=None)`). Если `config.py`
   будет валидировать токен при импорте — изменит момент падения. Для шага 4.1 валидацию
   НЕ добавлять.
6. **Циклы импортов.** `config.py` должен оставаться листом графа (импортирует только
   stdlib + `dotenv` + `yarl`). Любой импорт проектного модуля из `config.py` создаст цикл.
7. **`YarlURL` нужен только ради одной лог-строки (511).** Не забыть импорт при переносе
   блока прокси, иначе `NameError` в `setup_proxy()`.
8. **Граница с auth/validation.** `LOGIN_CMD_RE`, `EMAIL_RE`, `MAX_EMAIL_LEN/PASSWORD_LEN`
   технически константы, но семантически принадлежат валидации логина. Согласовать с зоной,
   отвечающей за `parse_login_credentials`, чтобы не растащить связанный код по двум модулям.

---

## 5. Рекомендуемый порядок извлечения

`config.py` — **хороший кандидат на первый извлекаемый модуль**: он лист графа зависимостей,
не зависит от других проектных модулей, и от него зависят почти все остальные. Извлечь его
первым позволяет всем последующим модулям сразу импортировать из `config`.

Порядок шагов (каждый — отдельный коммит в рабочем состоянии):

1. **Создать `config.py` с чистой частью.** Перенести литеральные константы (1.1), приватный
   `_LK_SEMAPHORE_BY_LOOP`, функции `_resolve_log_level`, `_parse_admin_ids`,
   `get_lk_semaphore`, `_write_heartbeat`. В `main.py` заменить определения на
   `from config import ...`. Поведение не меняется (чистый код).
2. **Перенести env-чтение, сохранив порядок.** Либо собрать `Settings` через `init_config()`,
   либо оставить значения на уровне `config.py` — но при этом ТОЧНО воспроизвести текущий
   порядок: ранние `LK_*` (47–49) до `load_dotenv()`, остальные после. Зафиксировать решение
   по риску #1 в комментарии/плане.
3. **Вынести `setup_logging()`.** Перенести `_LOG_LEVEL` + `basicConfig` + aiogram level
   в функцию. Вызвать её из точки входа `main.py` максимально рано.
4. **Вынести `setup_proxy()`.** Перенести блок 506–514 в функцию `config.py`. Вызвать из
   точки входа сразу после `setup_logging()`, до создания сетевых сессий.
5. **Проверить, что `import config` не имеет I/O-side-effects** (нет `load_dotenv`,
   `basicConfig`, мутации `environ` на уровне модуля) — только тогда цель плана достигнута.
6. Пограничные символы (`_fernet`/crypto, `ParserFailureMonitor`/monitoring, `bot`/`dp`/`db`)
   НЕ трогать в рамках 4.1 — это зоны других модулей.

---

## 6. Итог

- **Чистых констант:** 9 (1.1). **Env-значений на уровне модуля:** 12 (1.2).
  **Config-функций:** 4 (1.3). **Mutable global:** 1 (1.4). Плюс env, читаемые внутри
  функций (`DEBUG_DUMPS*`, `FIRST_DAY`, `PARSER_ALERT_*`) — оставить lazy.
- Всего ядро `config.py` ≈ **26 символов** + 3 функции инициализации (`init_config`/
  `setup_logging`/`setup_proxy`), которые надо создать при выносе side-effects.
- `config.py` — **подходит на роль первого извлекаемого модуля**: лист графа зависимостей,
  не импортирует проектный код, от него зависят почти все остальные.
- **Главный риск:** ранние `os.getenv` (строки 47–49) выполняются ДО `load_dotenv()` —
  при наивном переносе поведение изменится для `.env`-конфигов. Требуется явное решение.
- Side-effects при импорте (`load_dotenv`, `basicConfig`, мутация `os.environ`) обязаны
  переехать в явные функции инициализации; чистые литералы и `re.compile` могут остаться
  на уровне модуля.
