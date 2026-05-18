# Задача 4.1 — Анализ будущего модуля `db.py`

Зона: работа с SQLite (`users.db`). Цель — НУЛЕВОЕ изменение поведения, только перенос
кода из `main.py` (~5091 строк) в модуль `db.py`.

Источник: `/root/Satanyan/SatanBonchBot/main.py`.

---

## 1. Глобали и инициализация (side-effects при импорте)

### 1.1 `conn` — глобаль (соединение SQLite)
- **Тип:** `sqlite3.Connection`
- **Строки:** `556`
- **Код:** `conn = sqlite3.connect('users.db', check_same_thread=False)`
- **БД:** открывает файл `users.db` (относительный путь от CWD).
- **Зависит от:** `import sqlite3` (строка 8); CWD на момент импорта.
- **Кто использует снаружи:** через контекст `with conn:` в `set_notify_enabled`,
  `set_notify_minutes`, `set_autoclick_enabled`, `perform_login`, `cb_logout` (строка 4953).
- **Особенность:** `check_same_thread=False` — обязателен, т.к. бот многопоточный
  (aiogram). При переносе флаг сохранить как есть.

### 1.2 `cursor` — глобаль (курсор)
- **Тип:** `sqlite3.Cursor`
- **Строки:** `557`
- **Код:** `cursor = conn.cursor()`
- **Кто использует снаружи:** все DB-функции + 4 прямых inline-`cursor.execute(...)`
  внутри классов/хендлеров (см. раздел 4).

### 1.3 Создание таблицы + миграции — код уровня модуля (side-effect)
- **Тип:** исполняемый блок верхнего уровня (НЕ функция)
- **Строки:** `559–579`
- **Что делает:**
  - открывает ВТОРОЕ, отдельное соединение через `with closing(sqlite3.connect('users.db')) as db:`;
  - `CREATE TABLE IF NOT EXISTS users (...)` — схема: `user_id INTEGER PRIMARY KEY`,
    `email TEXT NOT NULL`, `password TEXT NOT NULL`, `notify_enabled INTEGER NOT NULL DEFAULT 1`,
    `notify_minutes INTEGER NOT NULL DEFAULT 10`, `autoclick_enabled INTEGER NOT NULL DEFAULT 1`;
  - читает `PRAGMA table_info(users)` → множество `_user_columns`;
  - идемпотентная миграция: `ALTER TABLE users ADD COLUMN ...` для каждой из трёх колонок
    настроек, если её нет (`ALTER TABLE ADD COLUMN` сам не идемпотентен);
  - `db.commit()`.
- **БД:** пишет схему/колонки в `users.db`.
- **Зависит от:** `from contextlib import closing` (строка 9), `import sqlite3`.
- **Промежуточная глобаль:** `_user_columns` (строка 572) — техническая, после блока не нужна.

---

## 2. Зависимость от `security.py` (НЕ зона db.py)

`encrypt_password` (строки 534–538) и `decrypt_password` (строки 541–551) и переменная
`_fernet` — переезжают в **`security.py`** (отдельная зона, не моя).

DB-функции, которые их вызывают (после переезда `db.py` должен импортировать их из `security`):
- `LessonController.reauthenticate` — `decrypt_password` (строка 1082) — *остаётся в main/controller, не в db.py*
- `auto_login_user` — `decrypt_password` (строка 3931) — *остаётся в main*
- callback восстановления cookies — `decrypt_password` (строка 3992) — *остаётся в main*
- `perform_login` — `encrypt_password` (строка 4330) — **переезжает в db.py**, значит
  `db.py` будет импортировать `encrypt_password` из `security.py`.

> Прямой SQL (`SELECT email, password ...`) в `reauthenticate` / `auto_login_user` /
> восстановлении cookies — это inline-SQL внутри бизнес-логики. Эти места НЕ являются
> отдельными db-функциями; см. раздел 4 (рекомендация ввести хелпер `get_credentials`).

---

## 3. DB-функции, переезжающие в `db.py`

Всего **7 функций** (6 sync + 1 async). Для каждой — сигнатура, строки, SQL, вызовы.

### 3.1 `is_registered(user_id: int) -> bool`
- **Тип:** обычная функция
- **Строки:** `4258–4260`
- **SQL:** `SELECT 1 FROM users WHERE user_id = ?` → `fetchone() is not None`
- **Читает/пишет:** только чтение.
- **Зависит от:** `cursor`.
- **Вызывают снаружи:** строки `1132`, `4372`, `4380`, `4393`, `4407`, `4529`, `4668`, `4679`
  (хендлеры меню/расписания/автоотметки/сообщений/профиля).

### 3.2 `get_notify_settings(user_id: int) -> tuple[bool, int]`
- **Тип:** обычная функция
- **Строки:** `4268–4280`
- **SQL:** `SELECT notify_enabled, notify_minutes FROM users WHERE user_id = ?`
- **Поведение:** если строки нет → `(True, NOTIFY_DEFAULT_MINUTES)`; `None`-значения
  нормализуются (`enabled` → True, `minutes` → дефолт).
- **Читает/пишет:** только чтение.
- **Зависит от:** `cursor`, константа `NOTIFY_DEFAULT_MINUTES` (строка 4264).
- **Вызывают снаружи:** строки `920` (в классе `LessonController`), `4417`, `4895`, `4905`, `4924`.

### 3.3 `set_notify_enabled(user_id: int, enabled: bool) -> None`
- **Тип:** обычная функция
- **Строки:** `4283–4288`
- **SQL:** `UPDATE users SET notify_enabled = ? WHERE user_id = ?` (1/0), обёрнут в `with conn:`
- **Читает/пишет:** запись (транзакция через `with conn`).
- **Зависит от:** `conn`, `cursor`.
- **Вызывают снаружи:** строка `4907`.

### 3.4 `set_notify_minutes(user_id: int, minutes: int) -> None`
- **Тип:** обычная функция
- **Строки:** `4291–4296`
- **SQL:** `UPDATE users SET notify_minutes = ? WHERE user_id = ?`, в `with conn:`
- **Читает/пишет:** запись.
- **Зависит от:** `conn`, `cursor`.
- **Вызывают снаружи:** строка `4923`.

### 3.5 `get_autoclick_enabled(user_id: int) -> bool`
- **Тип:** обычная функция
- **Строки:** `4299–4308`
- **SQL:** `SELECT autoclick_enabled FROM users WHERE user_id = ?`
- **Поведение:** нет строки или `None` → `True` (дефолт «включено»).
- **Читает/пишет:** только чтение.
- **Зависит от:** `cursor`.
- **Вызывают снаружи:** строка `3958` (внутри `auto_login_user`).

### 3.6 `set_autoclick_enabled(user_id: int, enabled: bool) -> None`
- **Тип:** обычная функция
- **Строки:** `4311–4316`
- **SQL:** `UPDATE users SET autoclick_enabled = ? WHERE user_id = ?`, в `with conn:`
- **Читает/пишет:** запись.
- **Зависит от:** `conn`, `cursor`.
- **Вызывают снаружи:** строки `1208`, `1227`, `4640`, `4648`.

### 3.7 `perform_login(user_id: int, email: str, password: str) -> bool` — **async**
- **Тип:** `async def`
- **Строки:** `4319–4341`
- **SQL:**
  - `SELECT user_id FROM users WHERE user_id = ?` (проверка существования);
  - в `with conn:` либо `UPDATE users SET email = ?, password = ? WHERE user_id = ?`,
    либо `INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)`.
- **Читает/пишет:** чтение + запись (upsert).
- **Зависит от:** `conn`, `cursor`, `encrypt_password` (→ `security.py`),
  `DebuggableBonchAPI`, `LessonController`, глобали `apis` и `controllers`, `bot`.
- **ВАЖНО — смешанная ответственность:** функция делает не только DB-работу — она
  выполняет `await api.login(...)`, создаёт `DebuggableBonchAPI` и `LessonController`,
  кладёт их в `apis`/`controllers`. То есть это НЕ чистая db-функция.
  - **Рекомендация:** оставить `perform_login` в `main.py` (бизнес-логика входа),
    а в `db.py` вынести только запись данных, например `save_user(user_id, email, password)`
    (upsert + `encrypt_password`). Тогда `perform_login` будет вызывать `db.save_user(...)`.
  - Если приоритет — «нулевой риск / минимум изменений», можно `perform_login` целиком
    оставить в `main.py`, а в `db.py` вынести только sync-хелпер upsert. Окончательное
    решение — за владельцем main.py; здесь это помечено как точка согласования.
- **Вызывают снаружи:** строки `1179`, `4508`.

---

## 4. Inline-SQL вне функций (не отдельные символы, требуют решения)

Эти `cursor.execute(...)` встроены прямо в классы/хендлеры. Они не являются именованными
функциями, поэтому формально «переезжать в db.py» нечему. Два варианта:
(A) оставить inline-SQL в main (но тогда main.py продолжает напрямую трогать `cursor`);
(B) ввести в `db.py` тонкие хелперы и заменить inline-SQL вызовами.
Рекомендуется (B) для будущего, но для задачи 4.1 (нулевой риск) допустимо (A) —
тогда `main.py` импортирует `cursor`/`conn` из `db.py`.

| Место | Строки | SQL | Контекст |
|---|---|---|---|
| `LessonController.reauthenticate` | 1076–1077 | `SELECT email, password FROM users WHERE user_id = ?` | метод класса |
| `cmd_my_account` | 1270–1271 | `SELECT email FROM users WHERE user_id = ?` | хендлер `/my_account` |
| `auto_login_user` | 3924–3925 | `SELECT email, password FROM users WHERE user_id = ?` | async-функция |
| восстановление cookies | 3988–3989 | `SELECT email, password FROM users WHERE user_id = ?` | внутри функции |
| `menu_profile` | 4413–4414 | `SELECT email FROM users WHERE user_id = ?` | хендлер профиля |
| `cb_logout` | 4953–4954 | `DELETE FROM users WHERE user_id = ?` (в `with conn:`) | callback выхода |
| `auto_login_all_users` | 4987–4988 | `SELECT user_id FROM users` → `fetchall()` | async-функция |

**Рекомендуемые хелперы для `db.py`** (если выбран вариант B; повторяющиеся запросы):
- `get_credentials(user_id) -> tuple[str, str] | None` — закрывает строки 1076, 3924, 3988
  (`SELECT email, password ...`). Расшифровку пароля оставить вызывающей стороне
  (т.к. `decrypt_password` живёт в `security.py`), либо вернуть уже расшифрованным —
  это меняет поведение, поэтому по умолчанию возвращать как в БД.
- `get_email(user_id) -> str | None` — закрывает строки 1270, 4413.
- `delete_user(user_id) -> None` — закрывает строку 4953.
- `list_user_ids() -> list[int]` — закрывает строку 4987.

> Для строгого «нулевого изменения поведения» в рамках 4.1 безопаснее вариант (A):
> перенести только 7 именованных функций + глобали + `init_db`, а inline-SQL оставить,
> экспортировав `conn`/`cursor` из `db.py`. Введение хелперов — отдельный последующий шаг.

---

## 5. Side-effects при импорте — как убрать

Сейчас при `import main` выполняется: `sqlite3.connect('users.db')` (строка 556) +
второе соединение и `CREATE TABLE`/`ALTER TABLE` (559–579). План требует убрать
side-effects при импорте.

**Рекомендация: явная функция `init_db()`.**

```text
db.py:
    conn = None        # модульная глобаль, None до init
    cursor = None

    def connect(db_path='users.db'):
        global conn, cursor
        conn = sqlite3.connect(db_path, check_same_thread=False)
        cursor = conn.cursor()

    def init_db(db_path='users.db'):
        # CREATE TABLE IF NOT EXISTS + миграция колонок (бывшие строки 559-579)
        ...

    # либо один init_db(), который и connect, и схему делает
```

- `init_db()` вызывается явно в точке старта бота (в `main.py` — в `main()` /
  на старте `__main__`, рядом с запуском поллинга), а НЕ на уровне модуля.
- **Альтернатива — ленивое подключение** (`_get_conn()` с проверкой `conn is None`):
  сложнее, добавляет проверку в каждый вызов, и `cursor` как модульная глобаль
  плохо сочетается с ленивостью. Для текущего стиля кода (везде используется
  глобаль `cursor`) **`init_db()` проще и безопаснее**.
- **Риск нулевого изменения поведения:** если `db.py` импортируется, а `init_db()`
  не вызван, `cursor`/`conn` будут `None` → падение. Нужно гарантировать вызов
  `init_db()` до первого хендлера. Тесты это закрывают фикстурой (см. ниже).

**Важно про текущую двойную connect:** строка 556 (`conn`/`cursor` — рабочее
соединение, `check_same_thread=False`) и строка 559 (отдельное `closing`-соединение
только для DDL). При переносе сохранить обе роли: `init_db()` может выполнять DDL
через то же рабочее `conn` ИЛИ через отдельное — чтобы не менять поведение, проще
повторить как есть (отдельное короткоживущее соединение для DDL, рабочее `conn` для
запросов). Менять на одно соединение — отдельное решение, не для 4.1.

---

## 6. Влияние на `conftest.py` / тесты

Файл: `/root/Satanyan/SatanBonchBot/conftest.py`.

### Фикстура `temp_db` (строки 44–63) — **ТРЕБУЕТ ПРАВКИ**
Сейчас:
```python
import main
...
original_conn, original_cursor = main.conn, main.cursor
main.conn = test_conn
main.cursor = test_conn.cursor()
...
main.conn, main.cursor = original_conn, original_cursor
```
После переезда глобали `conn`/`cursor` будут жить в `db.py`, а не в `main`.
**Нужно:**
- импортировать `db` (`import db` или `from SatanBonchBot import db` — по схеме проекта);
- подменять `db.conn` / `db.cursor` вместо `main.conn` / `main.cursor`.

**Подводный камень — связывание имён.** Если `main.py` делает
`from db import conn, cursor` (импорт значений), то DB-функции, оставшиеся в `main`
(или inline-SQL), будут ссылаться на СВОЮ копию имени, и подмена `db.cursor` фикстурой
их не затронет. Варианты:
- **(Рекомендуется)** все DB-функции и весь inline-SQL живут в `db.py` и обращаются к
  модульным глобалям `db.conn`/`db.cursor` напрямую → фикстура подменяет `db.cursor`,
  всё работает.
- Если что-то остаётся в `main` и трогает курсор — в `main.py` использовать
  `import db` и обращаться `db.cursor` / `db.conn` (НЕ `from db import cursor`),
  чтобы подмена была видна.
- `temp_db` для надёжности может подменять оба пространства имён, если переходный
  период требует. Но цель — чтобы курсор был в одном месте.

### `USERS_SCHEMA` (строки 24–33)
Дублирует схему таблицы. После переезда уместно (но не обязательно для 4.1) импортировать
схему/`init_db` из `db.py`, чтобы не расходились. Для 4.1 — оставить как есть (схема
идентична строкам 561–568), просто отметить как потенциальный дубль.

### Фикстура `reset_rate_limit` (строки 66–73)
Трогает `main._login_attempts` — к `db.py` отношения не имеет, **правки не требует**.

### Прочие тесты
Найти все тесты, которые обращаются к `main.is_registered`, `main.get_notify_settings`,
`main.set_notify_*`, `main.get/set_autoclick_enabled`, `main.perform_login` — после
переезда импорт изменится на `db.*`. Это надо проверить grep'ом по `tests/` отдельно
перед реализацией (в этом анализе каталог тестов не сканировался — только conftest).

---

## 7. Сводка по символам для `db.py`

| Символ | Тип | Строки main.py | Переезд |
|---|---|---|---|
| `conn` | глобаль `sqlite3.Connection` | 556 | да → `db.conn` |
| `cursor` | глобаль `sqlite3.Cursor` | 557 | да → `db.cursor` |
| блок `CREATE TABLE` + миграции | код модуля | 559–579 | да → внутрь `init_db()` |
| `is_registered` | func | 4258–4260 | да |
| `get_notify_settings` | func | 4268–4280 | да (+ `NOTIFY_DEFAULT_MINUTES`) |
| `set_notify_enabled` | func | 4283–4288 | да |
| `set_notify_minutes` | func | 4291–4296 | да |
| `get_autoclick_enabled` | func | 4299–4308 | да |
| `set_autoclick_enabled` | func | 4311–4316 | да |
| `perform_login` | async func | 4319–4341 | частично — см. 3.7 (точка согласования) |
| `init_db()` (новая) | func | — | создать, заменяет side-effect 559–579 |

**Константы:** `NOTIFY_DEFAULT_MINUTES` (4264) логически нужна `get_notify_settings`.
`NOTIFY_MINUTE_OPTIONS` (4265) — про UI/клавиатуру, НЕ db; оставить в main.
Решить: либо `NOTIFY_DEFAULT_MINUTES` переезжает в `db.py`, либо `db.py` импортирует
её из main (риск циклического импорта). Рекомендация — перенести `NOTIFY_DEFAULT_MINUTES`
в `db.py` и реэкспортировать/импортировать в main для UI-кода.

**Не переезжает (зона `security.py`):** `encrypt_password`, `decrypt_password`, `_fernet`,
warning о ключе. `db.py` будет импортировать `encrypt_password` из `security.py`
(для `perform_login`/`save_user`).

**Импорты, нужные `db.py`:** `import sqlite3`, `from contextlib import closing`,
`import logging` (для `perform_login`), `from security import encrypt_password`.

---

## 8. Главные риски

1. **Связывание имён `conn`/`cursor`.** `from db import cursor` ломает фикстуру `temp_db`
   и подмену в рантайме. Везде использовать `import db` + `db.cursor`/`db.conn`,
   либо держать ВЕСЬ код, трогающий курсор, внутри `db.py`.
2. **`temp_db` обязательно переписать** на `db.conn`/`db.cursor` — иначе все DB-тесты
   падают/идут в реальный `users.db`.
3. **`perform_login` смешивает DB и API-логику** — нельзя слепо «перенести в db.py».
   Нужно либо расщепить (`db.save_user` + `perform_login` в main), либо оставить целиком
   в main. Требует решения владельца.
4. **`init_db()` должен быть вызван** до первого обращения к `cursor`, иначе `None`-падение.
   Гарантировать вызов на старте бота и в тестовой фикстуре (или фикстура сама создаёт схему,
   как сейчас в `USERS_SCHEMA`).
5. **Inline-SQL в 7 местах** (раздел 4) — если оставить в main, main продолжает зависеть
   от `db.cursor`/`db.conn`; «полная» развязка требует введения хелперов (отдельный шаг,
   не строго 4.1).
6. **Циклический импорт** `main ↔ db` при переносе констант/функций — следить за
   направлением (`db.py` не должен импортировать `main`).
7. **Дубль схемы** `USERS_SCHEMA` в conftest vs `CREATE TABLE` в коде — расхождение
   при будущих миграциях.
