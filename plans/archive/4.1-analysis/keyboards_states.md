# Задача 4.1 — анализ модулей `keyboards.py` и `states.py`

Источник: `/root/Satanyan/SatanBonchBot/main.py` (5091 строк).
Цель: нулевое изменение поведения, только перенос кода. Это анализ, код не менялся.

---

## 1. Модуль `states.py`

### `UIStates` (класс FSM-состояний)

- **Тип:** `StatesGroup` (aiogram FSM).
- **Строки:** 4127–4136.
- **Сигнатура:** `class UIStates(StatesGroup)` со State-полями:
  `login_email`, `login_password`, `ask_group`, `ask_teacher`, `ask_classroom`,
  `write_recipient`, `write_pick`, `write_title`, `write_text`.
- **Зависимости:** `State`, `StatesGroup` из `aiogram.fsm.state` (импорт строка 19).
  Внешних зависимостей в модуле `main` нет — класс чисто декларативный.
- **Кто вызывает снаружи:** хендлеры в `main.py` (фильтры `dp.message(UIStates.*)`,
  вызовы `state.set_state(UIStates.*)`). Других `StatesGroup` в файле нет — `UIStates`
  единственный класс состояний. Полностью переносится в `states.py` без изменений.

**Итого `states.py`: 1 класс, 9 состояний.** Перенос тривиальный, нулевой риск.

---

## 2. Модуль `keyboards.py`

### 2.1 UI-константы (тексты кнопок)

| Имя | Строка | Значение | Зависимости | Кто использует |
|-----|--------|----------|-------------|----------------|
| `BTN_SCHEDULE` | 4120 | `"📅 Расписание"` | — | `main_menu_kb`, фильтр `F.text == BTN_SCHEDULE` (4366), тест |
| `BTN_AUTOCLICK` | 4121 | `"✅ Автоотметка"` | — | `main_menu_kb`, фильтр (4376), тест |
| `BTN_MESSAGES` | 4122 | `"✉️ Сообщения"` | — | `main_menu_kb`, фильтр (4390), тест |
| `BTN_PROFILE` | 4123 | `"👤 Профиль"` | — | `main_menu_kb`, фильтр (4403), тест |
| `BTN_HELP` | 4124 | `"❓ Помощь"` | — | `main_menu_kb`, фильтр (4435), тест |

Все `BTN_*` — чистые строковые константы UI. Переносятся в `keyboards.py`.
**Важно:** хендлеры в `main.py` используют их в декораторах `@dp.message(F.text == BTN_*)`,
поэтому после переноса `main.py` должен импортировать `BTN_*` из `keyboards` (или
`from keyboards import *`).

### 2.2 Текстовые UI-хелперы

| Имя | Тип | Строки | Сигнатура | Зависимости | Кто использует |
|-----|-----|--------|-----------|-------------|----------------|
| `HELP_TEXT` | `str` (константа) | 4151–4177 | — | — (чистая строка) | хендлер `/help` (4432), тест `test_help_text_covers_main_sections` |
| `notify_settings_text` | функция | 4231–4241 | `notify_settings_text(enabled: bool, minutes: int) -> str` | — (чистая функция, только форматирует строку) | хендлеры уведомлений (4895–4928), тесты `test_notify_settings_text_*` |

Оба чисто UI, без БД/сети. Переносятся в `keyboards.py`.

### 2.3 Reply-клавиатура

| Имя | Строки | Сигнатура | Зависимости | Кто использует |
|-----|--------|-----------|-------------|----------------|
| `main_menu_kb` | 4139–4148 | `main_menu_kb() -> ReplyKeyboardMarkup` | `BTN_SCHEDULE/AUTOCLICK/MESSAGES/PROFILE/HELP`, `ReplyKeyboardMarkup`, `KeyboardButton` | хендлеры `/start`, `/help`, отмена и др. (строки 1135, 1148, 1185, 4432, 4449, 4461, 4863, 4959, 4969), тест |

Чистая, без логики. Переносится.

### 2.4 Inline-клавиатуры — простые сборщики (чистые)

| Имя | Строки | Сигнатура | Зависимости | Кто использует |
|-----|--------|-----------|-------------|----------------|
| `cancel_kb` | 4180–4183 | `cancel_kb() -> InlineKeyboardMarkup` | `InlineKeyboardMarkup`, `InlineKeyboardButton` | множество хендлеров диалогов (4472, 4482, 4487, 4543, 4553, 4563, 4687, 4751, 4778, 4834, 4846, 4853, 4938) |
| `login_prompt_kb` | 4186–4189 | `login_prompt_kb() -> InlineKeyboardMarkup` | те же | хендлеры онбординга/входа (1152, 1167, 1175, 4352, 4384, 4397, 4410, 4504, 4520, 4531, 4623, 4670, 4681, 4730, 4871) |
| `schedule_menu_kb` | 4192–4200 | `schedule_menu_kb(logged_in: bool) -> InlineKeyboardMarkup` | те же | хендлер `BTN_SCHEDULE` (4372), тесты |
| `autoclick_menu_kb` | 4203–4213 | `autoclick_menu_kb(is_running: bool) -> InlineKeyboardMarkup` | те же | хендлеры автоотметки (4360, 4657, 4659), тесты |
| `messages_menu_kb` | 4216–4220 | `messages_menu_kb() -> InlineKeyboardMarkup` | те же | хендлер `BTN_MESSAGES` (4400) |
| `profile_menu_kb` | 4223–4228 | `profile_menu_kb() -> InlineKeyboardMarkup` | те же | хендлер `BTN_PROFILE` (4425), тест |
| `title_kb` | 4691–4695 | `title_kb() -> InlineKeyboardMarkup` | те же | хендлеры написания сообщения (4739, 4761, 4826) |

Все семь — чистые: только конструируют разметку из аргументов и литералов.
`callback_data` — статические строки. Переносятся без изменений.

### 2.5 Inline-клавиатуры — навигация по неделям (чистые)

| Имя | Строки | Сигнатура | Зависимости | Кто использует |
|-----|--------|-----------|-------------|----------------|
| `get_week_navigation_buttons` | 2245–2267 | `get_week_navigation_buttons(week_offset: int = 0) -> InlineKeyboardMarkup` | `InlineKeyboardMarkup`, `InlineKeyboardButton` | хендлеры «Моё расписание» (2621, 2654, 2679), тест |
| `get_teacher_week_navigation_buttons` | 2269–2292 | `get_teacher_week_navigation_buttons(teacher_name: str, week_number: int = None) -> InlineKeyboardMarkup` | те же + `base64` (локальный `import base64` внутри) | хендлеры расписания преподавателя (2393, 3055) |
| `get_classroom_week_navigation_buttons` | 2294–2317 | `get_classroom_week_navigation_buttons(classroom_number: str, week_number: int = None) -> InlineKeyboardMarkup` | те же + `base64` (локальный импорт) | хендлеры расписания аудитории (2448, 3113) |
| `get_group_week_navigation_buttons` | 2319–2346 | `get_group_week_navigation_buttons(group_name: str, week_number: int = None) -> InlineKeyboardMarkup` | те же + `base64` (локальный импорт) | хендлеры расписания группы (2542, 2590, 3619), тест |

Все четыре — чистые: считают `callback_data` из аргументов, `base64`-кодирование имени.
Никаких БД/сети. Локальный `import base64` внутри функций можно оставить как есть
(нулевое изменение поведения) либо поднять в шапку `keyboards.py`.

### 2.6 Inline-клавиатура с пагинацией (почти чистая)

| Имя | Строки | Сигнатура | Зависимости | Кто использует |
|-----|--------|-----------|-------------|----------------|
| `recipients_page_kb` | 4701–4720 | `recipients_page_kb(results: list, page: int) -> InlineKeyboardMarkup` | `InlineKeyboardMarkup`, `InlineKeyboardButton`, **модульная константа `RECIPIENTS_PER_PAGE`** | хендлеры выбора получателя (4770, 4805) |
| `RECIPIENTS_PER_PAGE` | 4698 | `= 8` (константа) | — | только `recipients_page_kb` |

`recipients_page_kb` чистая (получает `results` готовым списком, ничего не читает из БД),
но опирается на `RECIPIENTS_PER_PAGE`. Константу нужно перенести вместе с функцией в
`keyboards.py`. `results` — это `list[dict]` с ключами `label`/`id`; функция использует
только `results[i]["label"]` — данные приходят снаружи, не запрашиваются.

---

## 3. Особые случаи и зависимости через границу модуля

### 3.1 `notify_settings_kb` — порядок определения константы

| Имя | Строки | Сигнатура | Зависимости | Кто использует |
|-----|--------|-----------|-------------|----------------|
| `notify_settings_kb` | 4244–4255 | `notify_settings_kb(enabled: bool, minutes: int) -> InlineKeyboardMarkup` | `InlineKeyboardMarkup`, `InlineKeyboardButton`, **`NOTIFY_MINUTE_OPTIONS`** | хендлеры уведомлений (4898, 4911, 4928), тесты |

**Тонкость:** `NOTIFY_MINUTE_OPTIONS` (строка 4265) объявлена *после* функции
`notify_settings_kb` (строка 4244). Сейчас это работает, т.к. имя резолвится в момент
вызова, а не определения.

`NOTIFY_MINUTE_OPTIONS` / `NOTIFY_DEFAULT_MINUTES` (строки 4264–4265) используются также
бизнес-логикой настроек: `get_notify_settings` (4276, 4279), и тестами
`test_main_settings.py` (`main.NOTIFY_DEFAULT_MINUTES`). То есть это **не чисто UI-константы** —
они принадлежат и слою настроек, и слою клавиатур.

**Рекомендация:** `NOTIFY_MINUTE_OPTIONS` и `NOTIFY_DEFAULT_MINUTES` оставить в модуле
настроек (`settings.py`/`config.py`), а `keyboards.py` импортирует `NOTIFY_MINUTE_OPTIONS`
оттуда. Это устранит зависимость «снизу вверх» по тексту и сделает порядок явным.
Альтернатива (минимальный риск, но дублирование) — поместить константы в `keyboards.py`
и импортировать в настройки. Выбор согласовать с зоной настроек (задача 4.1).

### 3.2 `is_registered` — НЕ клавиатура, не переносить в `keyboards.py`

- **Строки:** 4258–4260. Сигнатура `is_registered(user_id: int) -> bool`.
- Физически расположена среди клавиатур, но **обращается к БД** (`cursor.execute(...)`).
- Это бизнес-логика/доступ к данным. В `keyboards.py` ей не место.
- Используется хендлерами (1132, 4372, 4380, 4393, 4407, 4529, 4668, 4679), в т.ч.
  как аргумент `schedule_menu_kb(is_registered(...))`. Сам вызов остаётся в хендлерах
  `main.py` — `schedule_menu_kb` получает уже готовый `bool`, поэтому связи с БД у
  клавиатуры нет. `is_registered` отнести к слою БД/пользователей, не к `keyboards.py`.

### 3.3 Inline-клавиатуры, собираемые прямо в хендлерах (НЕ выносятся)

Это не функции-сборщики, а разметка, построенная по месту внутри обработчиков. В рамках
задачи 4.1 их трогать не нужно (нулевое изменение поведения), но они мешают полному
покрытию `keyboards.py`. Кандидаты на будущий рефакторинг (вне 4.1):

- Строки 3358–3380: список получателей `lk_send_*` в обработчике отправки в ЛК.
- Строки 3760–3775: навигация по входящим сообщениям (`msg_prev_*`, `msg_next_*`,
  `msg_open_*`, `msg_refresh`).
- Строка 3881: ещё одна inline-разметка в обработчике сообщений.

Эти участки тянут локальный контекст хендлера (`messages`, `index`, `has_more_pages`,
`pending_lk_messages` и т.п.), поэтому в 4.1 их оставляем в `main.py`.

---

## 4. Влияние на тесты

Файл `/root/Satanyan/SatanBonchBot/tests/test_main_keyboards.py` сейчас обращается
ко всем символам через `import main` и `main.<symbol>`:

- `main.main_menu_kb`, `main.schedule_menu_kb`, `main.autoclick_menu_kb`,
  `main.profile_menu_kb`, `main.notify_settings_kb`
- `main.get_week_navigation_buttons`, `main.get_group_week_navigation_buttons`
- `main.BTN_SCHEDULE/AUTOCLICK/MESSAGES/PROFILE/HELP`
- `main.NOTIFY_MINUTE_OPTIONS`, `main.notify_settings_text`, `main.HELP_TEXT`

После выноса в `keyboards.py` есть два пути сохранить зелёные тесты:

1. **Реэкспорт в `main.py`** — `from keyboards import *` (или явный список) и
   `from states import UIStates`. Тогда `main.<symbol>` продолжает работать,
   тест-файл менять не нужно. Минимальный риск, рекомендуется для 4.1.
2. **Переключить импорты в тесте** на `import keyboards` и `keyboards.<symbol>`.
   Тогда `main.py` может не реэкспортировать. Требует правки тест-файла.

`NOTIFY_MINUTE_OPTIONS` используется и в `test_main_settings.py` (`main.NOTIFY_DEFAULT_MINUTES`),
поэтому если константа уезжает в `settings.py`, реэкспорт в `main.py` (вариант 1) нужен
в любом случае, чтобы не сломать `test_main_settings.py`.

`UIStates` в тестах напрямую не упоминается — перенос в `states.py` тесты клавиатур не
затрагивает; достаточно реэкспорта в `main.py` для хендлеров.

---

## 5. Сводная таблица символов

### `states.py`
| Символ | Строки |
|--------|--------|
| `UIStates` | 4127–4136 |

### `keyboards.py`
| Символ | Тип | Строки | Чистый? |
|--------|-----|--------|---------|
| `BTN_SCHEDULE` | const | 4120 | да |
| `BTN_AUTOCLICK` | const | 4121 | да |
| `BTN_MESSAGES` | const | 4122 | да |
| `BTN_PROFILE` | const | 4123 | да |
| `BTN_HELP` | const | 4124 | да |
| `HELP_TEXT` | const | 4151–4177 | да |
| `notify_settings_text` | func | 4231–4241 | да |
| `main_menu_kb` | func | 4139–4148 | да |
| `cancel_kb` | func | 4180–4183 | да |
| `login_prompt_kb` | func | 4186–4189 | да |
| `schedule_menu_kb` | func | 4192–4200 | да |
| `autoclick_menu_kb` | func | 4203–4213 | да |
| `messages_menu_kb` | func | 4216–4220 | да |
| `profile_menu_kb` | func | 4223–4228 | да |
| `notify_settings_kb` | func | 4244–4255 | да (зависит от `NOTIFY_MINUTE_OPTIONS`) |
| `title_kb` | func | 4691–4695 | да |
| `RECIPIENTS_PER_PAGE` | const | 4698 | да |
| `recipients_page_kb` | func | 4701–4720 | да |
| `get_week_navigation_buttons` | func | 2245–2267 | да |
| `get_teacher_week_navigation_buttons` | func | 2269–2292 | да (локальный `import base64`) |
| `get_classroom_week_navigation_buttons` | func | 2294–2317 | да (локальный `import base64`) |
| `get_group_week_navigation_buttons` | func | 2319–2346 | да (локальный `import base64`) |

### Импорты для `keyboards.py`
`from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton`
+ `base64` (если поднять из локальных импортов) + `NOTIFY_MINUTE_OPTIONS` (из модуля настроек, см. 3.1).

### НЕ переносить в `keyboards.py`
- `is_registered` (4258–4260) — доступ к БД, в слой данных.
- `NOTIFY_MINUTE_OPTIONS` / `NOTIFY_DEFAULT_MINUTES` (4264–4265) — общие с настройками, решить в зоне настроек.
- Inline-разметка внутри хендлеров (3358–3380, 3760–3775, 3881) — вне рамок 4.1.
