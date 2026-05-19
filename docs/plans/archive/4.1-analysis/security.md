# Задача 4.1 — Анализ модуля `security.py`

Зона ответственности: шифрование паролей ЛК (Fernet) и in-memory rate-limit на попытки входа.

Источник: `main.py` (5091 строк). Цель — ПЕРЕНОС кода без изменения поведения.

---

## 1. Символы для переноса в `security.py`

### 1.1 Шифрование паролей (блок `main.py:516–551`)

| Символ | Тип | Строки | Сигнатура | Описание |
|---|---|---|---|---|
| `ENCRYPTION_KEY` | module-level переменная (`str \| None`) | 520 | — | `os.getenv("ENCRYPTION_KEY")`. Читается при импорте. |
| `_fernet` | module-level переменная (`Fernet \| None`) | 522–525 | — | Создаётся при импорте: `Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None`. При невалидном ключе — `None` + `logging.error`. |
| (warning-блок) | side-effect при импорте | 526–531 | — | `if _fernet is None: logging.warning(...)` — предупреждение об отключённом шифровании. |
| `encrypt_password` | функция | 534–538 | `encrypt_password(password: str) -> str` | Шифрует пароль. При `_fernet is None` возвращает аргумент как есть. |
| `decrypt_password` | функция | 541–551 | `decrypt_password(stored: str) -> str` | Расшифровывает; при `_fernet is None`/пустой строке — passthrough; при `InvalidToken` — passthrough (обратная совместимость со старым plaintext). |

**Зависимости (импорты, нужные в `security.py`):**
- `os` (чтение env)
- `logging`
- `from cryptography.fernet import Fernet, InvalidToken` (`main.py:34`)

**Внешние вызовы (call sites в `main.py`):**
- `decrypt_password` — строки **1082, 3931, 3992** (авторизация / автологин по сохранённым кредам).
- `encrypt_password` — строка **4330** (сохранение пароля в `users.db` при `/login`).
- `_fernet` — используется только внутри `encrypt_password`/`decrypt_password`.

---

### 1.2 Rate-limit входа (блок `main.py:777–806`)

| Символ | Тип | Строки | Сигнатура | Описание |
|---|---|---|---|---|
| `LOGIN_RATE_LIMIT` | module-level константа (`int`) | 780 | — | `max(1, int(os.getenv("LOGIN_RATE_LIMIT", "5")))`. Читается при импорте. |
| `LOGIN_RATE_WINDOW_SEC` | module-level константа (`int`) | 781 | — | `max(1, int(os.getenv("LOGIN_RATE_WINDOW_SEC", "300")))`. Читается при импорте. |
| `_login_attempts` | module-level переменная (`dict[int, list[float]]`) | 782 | — | In-memory счётчик попыток входа по `user_id`. |
| `check_login_rate_limit` | функция | 785–798 | `check_login_rate_limit(user_id: int) -> int` | Регистрирует попытку и проверяет лимит. Возвращает `0` если вход разрешён, иначе число секунд ожидания. Мутирует `_login_attempts`. |
| `format_retry_after` | функция | 801–806 | `format_retry_after(seconds: int) -> str` | Человекочитаемое время ожидания (`"N сек"` / `"N мин"`). |

**Зависимости (импорты, нужные в `security.py`):**
- `os` (чтение env)
- `import time as time_module` (`main.py:30`) — используется `time_module.monotonic()` в `check_login_rate_limit`. В `security.py` достаточно `import time` и обращения `time.monotonic()` (или сохранить алиас `time_module` ради дословного переноса).

**Внешние вызовы (call sites в `main.py`):**
- `check_login_rate_limit` — строки **1171, 4500** (хендлеры входа).
- `format_retry_after` — строки **1174, 4503** (сообщения пользователю; всегда рядом с `check_login_rate_limit`).
- `LOGIN_RATE_LIMIT`, `LOGIN_RATE_WINDOW_SEC`, `_login_attempts` — внутри `check_login_rate_limit` и в тестах/conftest.

> Примечание: `format_retry_after` логически часть rate-limit-блока (`main.py:801–806`, сразу после `check_login_rate_limit`) и тестируется в `test_main_security.py`. Включается в зону `security.py`.

---

## 2. Side-effects при импорте `security.py`

Перенос переносит и порядок исполнения. При первом `import security` произойдёт:

1. `os.getenv("ENCRYPTION_KEY")` → `ENCRYPTION_KEY`.
2. `Fernet(...)` → `_fernet` (или `None` + `logging.error` при `ValueError/TypeError`).
3. `logging.warning(...)` если `_fernet is None`.
4. `os.getenv("LOGIN_RATE_LIMIT")` / `os.getenv("LOGIN_RATE_WINDOW_SEC")` → константы.
5. `_login_attempts = {}`.

**Условие сохранения поведения:** `.env` должен быть загружен (`load_dotenv()`) ДО импорта `security.py`. В `main.py` `load_dotenv()` вызывается раньше блока 516. При декомпозиции нужно гарантировать, что либо `security.py` сам делает `load_dotenv()` на верхнем уровне, либо `main.py` импортирует `security` уже после `load_dotenv()`. Иначе `ENCRYPTION_KEY`/`_fernet` окажутся `None` и шифрование молча отключится — это уронит `test_real_encryption_key_is_valid`.

---

## 3. Влияние на тесты

### 3.1 `tests/test_main_security.py`

Файл сейчас обращается ко всем символам через `main.*`. После переноса все security-обращения должны перейти на `security.*` (либо `main.py` должен реэкспортировать символы — см. раздел 4).

Затронутые тесты:

| Тест | Обращения | Что менять |
|---|---|---|
| `test_encrypt_decrypt_roundtrip` | `main._fernet`, `main.encrypt_password`, `main.decrypt_password` | `monkeypatch.setattr(security, "_fernet", ...)` + `security.encrypt_password/decrypt_password` |
| `test_encrypted_value_is_not_plaintext` | `main._fernet`, `main.encrypt_password` | то же |
| `test_decrypt_legacy_plaintext_passthrough` | `main._fernet`, `main.decrypt_password` | то же |
| `test_decrypt_empty_value` | `main._fernet`, `main.decrypt_password` | то же |
| `test_encryption_disabled_is_identity` | `main._fernet`, `main.encrypt_password/decrypt_password` | то же |
| `test_real_encryption_key_is_valid` | `main._fernet is not None` | `security._fernet is not None` |
| `test_rate_limit_allows_attempts_up_to_limit` | `main.check_login_rate_limit`, `main.LOGIN_RATE_LIMIT` | `security.*` |
| `test_rate_limit_blocks_after_limit` | `main.check_login_rate_limit`, `main.LOGIN_RATE_LIMIT` | `security.*` |
| `test_rate_limit_is_per_user` | `main.check_login_rate_limit`, `main.LOGIN_RATE_LIMIT` | `security.*` |
| `test_rate_limit_ignores_attempts_outside_window` | `main._login_attempts`, `main.check_login_rate_limit`, `main.LOGIN_RATE_LIMIT`, `main.LOGIN_RATE_WINDOW_SEC` | `security.*` |
| `test_format_retry_after_*` | `main.format_retry_after` | `security.format_retry_after` |

Тесты `parse_login_credentials_*` (строки 50–80) к зоне security НЕ относятся — это разбор `/login`, остаётся в другом модуле/`main`.

**Важно по `monkeypatch.setattr(..., "_fernet", ...)`:** `encrypt_password`/`decrypt_password` читают `_fernet` через глобал своего модуля. После переноса патчить нужно атрибут ТОГО модуля, где определены функции (`security`), иначе подмена не подействует. Патчить `main._fernet` будет бесполезно (если только `main` не реэкспортирует — см. раздел 4, и даже тогда патч `main._fernet` не повлияет на чтение внутри `security`).

### 3.2 `conftest.py` — фикстура `reset_rate_limit` (строки 66–73)

Сейчас:
```python
@pytest.fixture
def reset_rate_limit():
    import main
    main._login_attempts.clear()
    yield
    main._login_attempts.clear()
```
После переноса `_login_attempts` живёт в `security`. Фикстуру переключить на `import security` / `security._login_attempts.clear()`.

`temp_db` и `load_fixture` фикстуры security не касаются — без изменений.

### 3.3 `migrate_passwords.py`

Использует `os.getenv("ENCRYPTION_KEY")` и собственный `Fernet`, **не импортирует** `main` и не вызывает его функции. От переноса НЕ зависит — изменений не требует.

---

## 4. Риски и рекомендации

1. **Порядок `load_dotenv()` vs импорт.** Главный риск нулевого поведения: если `security.py` импортируется до загрузки `.env`, `ENCRYPTION_KEY` будет `None`. Рекомендация — `load_dotenv()` в `security.py` на верхнем уровне (идемпотентно) либо строгий порядок импортов в `main.py`.

2. **Монопатч `_fernet` в тестах.** `monkeypatch.setattr` должен указывать на модуль, где определены функции (`security`). При простом реэкспорте `from security import _fernet` в `main.py` создаётся отдельная ссылка-копия; патч `main._fernet` не изменит поведение `security.encrypt_password`. Тесты обязательно переводить на `security.*`, реэкспорт здесь не спасает.

3. **`_login_attempts` — общий мутабельный стейт.** И `check_login_rate_limit`, и фикстура `reset_rate_limit`, и тест `test_rate_limit_ignores_attempts_outside_window` пишут напрямую в `_login_attempts`. Все они должны указывать на ОДИН объект `security._login_attempts`. Реэкспорт словаря (`from security import _login_attempts`) сохранит идентичность объекта (это та же ссылка), но во избежание путаницы лучше унифицировать на `security._login_attempts`.

4. **Минимизация изменений в `main.py`.** Чтобы не трогать ~7 call sites (строки 1082, 1171, 1174, 3931, 3992, 4330, 4500, 4503), можно в начале `main.py` сделать `from security import (ENCRYPTION_KEY, _fernet, encrypt_password, decrypt_password, LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SEC, _login_attempts, check_login_rate_limit, format_retry_after)`. Тогда существующие вызовы внутри `main.py` остаются без правок. Тесты и `conftest.py` всё равно переводятся на `security.*` (см. пункт 2).

5. **`time_module`.** В `security.py` достаточно `import time` и `time.monotonic()`. Если хочется дословного переноса — оставить алиас `import time as time_module`. На поведение не влияет.

6. **Логи при импорте.** `logging.error`/`logging.warning` из блока 522–531 переедут в `security.py`. Если `logging` ещё не сконфигурирован к моменту импорта `security`, сообщения уйдут в дефолтный обработчик — это не меняет поведение бота, но порядок строк в логе может слегка отличаться. Некритично.

---

## 5. Итоговый список переноса

В `security.py` переезжают (диапазоны `main.py`):
- `ENCRYPTION_KEY`, `_fernet` + init/warning блок — строки **516–531**
- `encrypt_password` — строки **534–538**
- `decrypt_password` — строки **541–551**
- `LOGIN_RATE_LIMIT`, `LOGIN_RATE_WINDOW_SEC`, `_login_attempts` + комментарии — строки **777–782**
- `check_login_rate_limit` — строки **785–798**
- `format_retry_after` — строки **801–806**

Импорты модуля `security.py`: `os`, `logging`, `time`, `from cryptography.fernet import Fernet, InvalidToken` (+ при необходимости `load_dotenv`).
