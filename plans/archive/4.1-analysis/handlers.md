# Задача 4.1 — анализ пакета `handlers/` и остатка `main.py`

Анализ `main.py` (5091 строка, aiogram-бот) на предмет декомпозиции в пакет
`handlers/` плюс набор вспомогательных модулей. Цель — **нулевое изменение
поведения**, только перенос кода.

Зона этого анализа — самая крупная: все aiogram-хэндлеры, `LessonController`,
рендер/форматирование расписания, хелперы счётчика сообщений и TTL-кэша,
а также то, что физически должно остаться в `main.py`.

> Соседние анализы: `config.py`, `db.py`, `keyboards_states.py`, `security.py`.
> Этот документ опирается на их разбивку (см. ссылки ниже) и НЕ перетягивает их
> символы (`BTN_*`, `UIStates`, `*_kb`, `conn/cursor`, `_fernet`, `ParserFailureMonitor`).

---

## 0. Сводка по хэндлерам

Всего обработчиков aiogram — **49**:
- `@dp.message(...)` — **30** (включая `@dp.message()` fallback и 9 FSM-state-хэндлеров);
- `@dp.callback_query(...)` — **19**.

Декоратор регистрации сейчас — глобальный `dp` (создаётся в строке 582). При
декомпозиции каждый файл `handlers/*.py` заводит свой `Router()`, а `main.py`
делает `dp.include_router(...)` для каждого. См. раздел 8.

---

## 1. Карта хэндлеров по доменным файлам

### 1.1 `handlers/common.py` — старт/онбординг/login/help/cancel/fallback

| Функция | Декоратор/фильтр | Строки | Что вызывает |
|---|---|---|---|
| `cmd_start` | `@dp.message(Command("start"))` | 1128–1153 | `is_registered`, `main_menu_kb`, `login_prompt_kb` |
| `cmd_login` | `@dp.message(Command("login"))` | 1155–1190 | `parse_login_credentials`, `check_login_rate_limit`, `format_retry_after`, `perform_login`, `main_menu_kb`, `login_prompt_kb` |
| `cmd_help` | `@dp.message(Command("help"))` | 4429–4432 | `HELP_TEXT`, `main_menu_kb` |
| `menu_help` | `@dp.message(F.text == BTN_HELP)` | 4435–4438 | `HELP_TEXT` |
| `cmd_cancel` | `@dp.message(Command("cancel"))` | 4443–4450 | `main_menu_kb` |
| `cb_cancel` | `@dp.callback_query(F.data == "m:cancel")` | 4453–4461 | `main_menu_kb` |
| `cb_login` | `@dp.callback_query(F.data == "m:login")` | 4466–4473 | `UIStates.login_email`, `cancel_kb` |
| `fsm_login_email` | `@dp.message(UIStates.login_email)` | 4476–4487 | `EMAIL_RE`, `cancel_kb` |
| `fsm_login_password` | `@dp.message(UIStates.login_password)` | 4490–4520 | `check_login_rate_limit`, `format_retry_after`, `perform_login`, `main_menu_kb`, `login_prompt_kb` |
| `menu_schedule` | `@dp.message(F.text == BTN_SCHEDULE)` | 4366–4373 | `_format_cache_age`, `_timetable_cache_age_now`, `schedule_menu_kb`, `is_registered` |
| `menu_autoclick` | `@dp.message(F.text == BTN_AUTOCLICK)` | 4376–4387 | `is_registered`, `login_prompt_kb`, `send_autoclick_panel` |
| `menu_messages` | `@dp.message(F.text == BTN_MESSAGES)` | 4390–4400 | `is_registered`, `login_prompt_kb`, `messages_menu_kb` |
| `menu_profile` | `@dp.message(F.text == BTN_PROFILE)` | 4403–4426 | `is_registered`, `cursor`, `get_notify_settings`, `apis`, `profile_menu_kb` |
| `fallback_handler` | `@dp.message()` | 4965–4970 | `main_menu_kb` |

Замечания:
- `menu_schedule`, `menu_autoclick`, `menu_messages`, `menu_profile` — это
  reply-меню верхнего уровня; они «диспетчеры» в свои домены. Можно держать в
  `common.py` (точка входа) либо распределить по доменам. **Рекомендация:** держать
  все четыре пункта меню в `common.py` — они тонкие и тесно связаны с `main_menu_kb`.
- `fallback_handler` (`@dp.message()` без фильтра) обязан регистрироваться
  **последним** — его роутер `include_router` должен идти после всех остальных
  (см. раздел 8, риск порядка).
- `cb_login` живёт в `common.py`, но `cb_relogin` (профиль) тоже ставит
  `UIStates.login_email` → оба ведут в один FSM-поток `fsm_login_*`.

### 1.2 `handlers/autoclick.py` — автоотметка (LessonController-команды + меню)

| Функция | Декоратор/фильтр | Строки | Что вызывает |
|---|---|---|---|
| `cmd_start_lesson` | `@dp.message(Command("start_lesson"))` | 1192–1209 | `controllers`, `auto_login_user`, `set_autoclick_enabled`, `LessonController.start_lesson` |
| `cmd_stop_lesson` | `@dp.message(Command("stop_lesson"))` | 1211–1228 | `controllers`, `auto_login_user`, `set_autoclick_enabled`, `LessonController.stop_lesson` |
| `cmd_status` | `@dp.message(Command("status"))` | 1230–1242 | `controllers`, `auto_login_user`, `LessonController.get_status` |
| `cmd_test_notify` | `@dp.message(Command("test_notify"))` | 1244–1263 | `bot.send_message` |
| `cmd_my_account` | `@dp.message(Command("my_account"))` | 1265–1312 | `cursor`, `apis`, `controllers`, `auto_login_user` |
| `cb_autoclick` | `@dp.callback_query(F.data.startswith("m:auto:"))` | 4611–4659 | `controllers`, `auto_login_user`, `login_prompt_kb`, `cmd_test_notify`, `set_autoclick_enabled`, `LessonController.start/stop_lesson/get_status`, `autoclick_menu_kb` |

Замечания:
- `cb_autoclick` напрямую вызывает `cmd_test_notify` (4631). Перенос их в один
  модуль убирает кросс-модульный импорт.
- `send_autoclick_panel` (4344–4361) — хелпер автоотметки, вызывается из
  `menu_autoclick` (common). **Рекомендация:** `send_autoclick_panel` положить в
  `handlers/autoclick.py` и импортировать его в `common.py`.

### 1.3 `handlers/schedule.py` — расписание (личное/группа/преподаватель/аудитория)

| Функция | Декоратор/фильтр | Строки | Что вызывает |
|---|---|---|---|
| `cmd_timetable` | `@dp.message(Command("timetable"))` | 2661–2685 | `apis`, `auto_login_user`, `format_timetable`, `get_week_navigation_buttons` |
| `cmd_teacher_timetable` | `@dp.message(Command("teacher_timetable"))` | 3004–3060 | `get_all_groups_timetable`, `TimetableBonchAPI.teacher_timetable`, `format_timetable_dict`, `get_teacher_week_navigation_buttons`, `all_groups_timetable_cache`, `timetable_loading` |
| `cmd_classroom_timetable` | `@dp.message(Command("classroom_timetable"))` | 3062–3118 | `get_all_groups_timetable`, `TimetableBonchAPI.classroom_timetable`, `format_timetable_dict`, `get_classroom_week_navigation_buttons` |
| `cmd_teachers` | `@dp.message(Command("teachers"))` | 3120–3176 | `get_all_groups_timetable`, `all_groups_timetable_cache`, `timetable_loading` |
| `cmd_classrooms` | `@dp.message(Command("classrooms"))` | 3437–3491 | `get_all_groups_timetable`, `all_groups_timetable_cache`, `timetable_loading` |
| `cmd_groups` | `@dp.message(Command("groups"))` | 3493–3524 | `get_timetable_api` |
| `cmd_group_timetable` | `@dp.message(Command("group_timetable"))` | 3526–3646 | `get_all_groups_timetable`, `get_timetable_api`, `format_timetable_dict`, `get_group_week_navigation_buttons` |
| `cmd_reload_timetable` | `@dp.message(Command("reload_timetable"))` | 3648–3666 | `get_all_groups_timetable(force_reload=True)` |
| `process_image_week` | `@dp.callback_query(F.data.startswith("image_week_"))` | 2211–2243 | `apis`, `generate_timetable_image` |
| `process_teacher_week_navigation` | `@dp.callback_query(prev/next/all_teacher_week_)` | 2348–2401 | `all_groups_timetable_cache`, `TimetableBonchAPI.teacher_timetable`, `format_timetable_dict`, `get_teacher_week_navigation_buttons` |
| `process_classroom_week_navigation` | `@dp.callback_query(prev/next/all_classroom_week_)` | 2403–2456 | `all_groups_timetable_cache`, `TimetableBonchAPI.classroom_timetable`, `format_timetable_dict`, `get_classroom_week_navigation_buttons` |
| `process_group_week_navigation` | `@dp.callback_query(prev/next/image_group_week_)` | 2458–2554 | `all_groups_timetable_cache`, `format_timetable_dict`, `generate_timetable_image_from_dict`, `get_group_week_navigation_buttons` |
| `process_group_day` | `@dp.callback_query(F.data.startswith("group_day_"))` | 2556–2595 | `all_groups_timetable_cache`, `filter_group_lessons_by_date`, `format_timetable_dict` |
| `process_week_navigation` | `@dp.callback_query(prev/next/current_week_)` | 2597–2631 | `apis`, `format_timetable`, `get_week_navigation_buttons` |
| `process_my_day` | `@dp.callback_query(F.data.startswith("my_day_"))` | 2633–2659 | `apis`, `filter_personal_lessons_by_date`, `_week_offset_for_date`, `_moscow_today`, `format_timetable` |
| `cb_sched_my` | `@dp.callback_query(F.data == "m:sched:my")` | 4525–4534 | `is_registered`, `login_prompt_kb`, `cmd_timetable` |
| `cb_sched_group` | `@dp.callback_query(F.data == "m:sched:group")` | 4537–4544 | `UIStates.ask_group`, `cancel_kb` |
| `cb_sched_teacher` | `@dp.callback_query(F.data == "m:sched:teacher")` | 4547–4554 | `UIStates.ask_teacher`, `cancel_kb` |
| `cb_sched_room` | `@dp.callback_query(F.data == "m:sched:room")` | 4557–4564 | `UIStates.ask_classroom`, `cancel_kb` |
| `cb_sched_reload` | `@dp.callback_query(F.data == "m:sched:reload")` | 4567–4588 | `get_all_groups_timetable(force_reload=True)` |
| `fsm_ask_group` | `@dp.message(UIStates.ask_group)` | 4591–4594 | `cmd_group_timetable` |
| `fsm_ask_teacher` | `@dp.message(UIStates.ask_teacher)` | 4597–4600 | `cmd_teacher_timetable` |
| `fsm_ask_classroom` | `@dp.message(UIStates.ask_classroom)` | 4603–4606 | `cmd_classroom_timetable` |

Замечания:
- `cmd_timetable`, `cmd_group_timetable`, `cmd_teacher_timetable`,
  `cmd_classroom_timetable` имеют необязательный параметр (`uid` / `override`) —
  их **переиспользуют** меню-хэндлеры (`cb_sched_my`, `fsm_ask_*`). Все эти
  вызовы внутримодульные при сборке `schedule.py`.
- Все навигационные коллбэки расписания читают `all_groups_timetable_cache` через
  `global` — это связность №1 (см. раздел 5).
- `get_timetable_api`, `all_groups_timetable_with_progress`, `progress_updater`,
  `send_progress_update`, `get_all_groups_timetable`, `_refresh_timetable_quietly`
  — это **сервис загрузки расписания групп**, НЕ хэндлеры. См. раздел 4 и 6.

### 1.4 `handlers/messages.py` — сообщения ЛК (чтение + отправка)

| Функция | Декоратор/фильтр | Строки | Что вызывает |
|---|---|---|---|
| `cmd_send_lk` | `@dp.message(Command("send_lk"))` | 3179–3389 | `get_message_api`, `lk_search_recipients`, `lk_send_message`, `pending_lk_messages` |
| `handle_lk_send_callback` | `@dp.callback_query(F.data.startswith("lk_send_"))` | 3392–3435 | `pending_lk_messages`, `get_message_api`, `lk_send_message` |
| `cmd_messages` | `@dp.message(Command("messages"))` | 3668–3719 | `message_states`, `_messages_cache_fresh`, `get_message_api`, `_build_message_state`, `show_message_list`, `MESSAGES_CACHE_TTL_SEC` |
| `handle_message_callback` | `@dp.callback_query(F.data.startswith("msg_"))` | 3782–3917 | `message_states`, `_build_message_state`, `get_message_api`, `show_message_list` |
| `cb_msg_inbox` | `@dp.callback_query(F.data == "m:msg:inbox")` | 4664–4673 | `is_registered`, `login_prompt_kb`, `cmd_messages` |
| `cb_msg_write` | `@dp.callback_query(F.data == "m:msg:write")` | 4676–4688 | `is_registered`, `login_prompt_kb`, `UIStates.write_recipient`, `cancel_kb` |
| `fsm_write_recipient` | `@dp.message(UIStates.write_recipient)` | 4774–4780 | `start_recipient_pick` |
| `fsm_write_pick_refine` | `@dp.message(UIStates.write_pick)` | 4783–4788 | `start_recipient_pick` |
| `cb_write_noop` | `@dp.callback_query(F.data == "mw:noop")` | 4791–4793 | — |
| `cb_write_page` | `@dp.callback_query(F.data.startswith("mw:page:"))` | 4796–4807 | `recipients_page_kb` |
| `cb_write_pick` | `@dp.callback_query(F.data.startswith("mw:pick:"))` | 4810–4826 | `UIStates.write_title`, `title_kb` |
| `fsm_write_title` | `@dp.message(UIStates.write_title)` | 4829–4834 | `UIStates.write_text`, `cancel_kb` |
| `cb_notitle` | `@dp.callback_query(F.data == "mw:notitle")` | 4837–4846 | `UIStates.write_text`, `cancel_kb` |
| `fsm_write_text` | `@dp.message(UIStates.write_text)` | 4849–4885 | `get_message_api`, `lk_send_message`, `_invalidate_messages_cache`, `login_prompt_kb`, `main_menu_kb` |

Хелперы домена сообщений (НЕ хэндлеры, переносятся в `messages.py`):
- `show_message_list` (3721–3780) — рендер карточки сообщения + клавиатура;
  использует `message_states`, `format_message_count`, `bot.send_message`.
- `start_recipient_pick` (4723–4772) — поиск получателя; `get_message_api`,
  `lk_search_recipients`, `recipients_page_kb`, `title_kb`, `cancel_kb`.
- `get_message_api` (3970–4007) — строит `TimetableBonchAPI` из cookies `apis[uid]`;
  `apis`, `auto_login_user`, `cursor`, `decrypt_password`.
- `lk_search_recipients` / `lk_upload_file` / `lk_send_message` (4010–4114) —
  HTTP-обёртки над ЛК. `lk_upload_file` нигде не вызывается (мёртвый код, но
  переносится как есть — нулевое изменение поведения).
- `title_kb` (4691–4695), `recipients_page_kb` (4701–4720), `RECIPIENTS_PER_PAGE`
  (4698) — по анализу `keyboards_states.md` уходят в `keyboards.py`. `messages.py`
  их импортирует.

### 1.5 `handlers/profile.py` — профиль (уведомления, повторный вход, выход)

| Функция | Декоратор/фильтр | Строки | Что вызывает |
|---|---|---|---|
| `cb_notify_settings` | `@dp.callback_query(F.data == "m:profile:notify")` | 4890–4899 | `get_notify_settings`, `notify_settings_text`, `notify_settings_kb` |
| `cb_notify_toggle` | `@dp.callback_query(F.data == "m:notify:toggle")` | 4902–4912 | `get_notify_settings`, `set_notify_enabled`, `notify_settings_text/kb` |
| `cb_notify_minutes` | `@dp.callback_query(F.data.startswith("m:notify:min:"))` | 4915–4929 | `set_notify_minutes`, `get_notify_settings`, `notify_settings_text/kb` |
| `cb_relogin` | `@dp.callback_query(F.data == "m:profile:relogin")` | 4932–4939 | `UIStates.login_email`, `cancel_kb` |
| `cb_logout` | `@dp.callback_query(F.data == "m:profile:logout")` | 4942–4960 | `controllers`, `apis`, `conn`, `cursor`, `LessonController.stop_lesson`, `main_menu_kb` |

Замечание: `menu_profile` (1.1) живёт в `common.py`, но открывает профиль; сами
действия профиля — в `profile.py`. Это нормальное разделение «меню → действие».

---

## 2. `LessonController` — куда отнести

Класс `LessonController` (850–1126, ~277 строк). **Рекомендация: отдельный модуль
`lesson_controller.py`** (НЕ внутри `handlers/`).

Обоснование: это доменная сущность (фоновая корутина автоотметки + напоминания),
а не aiogram-хэндлер. Её используют `handlers/autoclick.py`, `auto_login_user`,
`perform_login` (login-слой) и `on_shutdown` (main.py). Если класть в
`handlers/autoclick.py`, то `auto_login_user`/`perform_login`/`on_shutdown`
получают зависимость от пакета хэндлеров — нежелательно. Отдельный модуль —
лист графа зависимостей.

### Методы

| Метод | Строки | Назначение |
|---|---|---|
| `__init__(api, bot, user_id)` | 851–862 | поля состояния + `lesson_intervals = LESSON_INTERVALS` |
| `_current_lesson_interval_index` | 864–871 | индекс текущей пары по времени |
| `_upcoming_lesson_interval_index` | 873–889 | индекс пары, до которой N мин |
| `is_time_between` | 891–896 | попадание времени в интервал |
| `is_lesson_time` | 898–903 | идёт ли сейчас любая пара |
| `start_lesson` (async) | 905–1057 | главный цикл: напоминание + клик + переавторизация |
| `stop_lesson` (async) | 1059–1067 | останавливает цикл, отменяет `self.task` |
| `get_status` (async) | 1069–1070 | строка статуса |
| `reauthenticate` (async) | 1072–1087 | пересоздаёт `apis[uid]`, перелогин |
| `dump_timetable_snapshot` (async) | 1089–1110 | HTML-дамп расписания |
| `capture_debug_artifacts` (async) | 1112–1126 | отладочные артефакты при ошибке |

### Зависимости `LessonController` (что нужно импортировать в `lesson_controller.py`)

- `LESSON_INTERVALS` (53–61) — константа интервалов пар. По `config.md` это
  чистый литерал config-уровня. Импорт из `config.py` (или `lesson_controller.py`
  держит свою копию — но лучше единый источник).
- `get_notify_settings` (4268) — БД-хелпер настроек уведомлений (слой `db.py`).
- `apis` (585), `cursor` (557) — **глобальное состояние** (см. раздел 5).
  `reauthenticate` пишет в `apis[self.user_id]` и читает `cursor`.
- `DebuggableBonchAPI` (78) — создаётся в `reauthenticate` (строка 1084).
- `decrypt_password` (541) — в `reauthenticate` (1082); слой `security.py`/`crypto`.
- `save_debug_dump` (827) — в `dump_timetable_snapshot` (1101).
- `pytz`, `datetime`, `time`, `asyncio`, `logging`, `Path`, `Optional` — stdlib.

Важная связность: `reauthenticate` мутирует **глобальный** словарь `apis`.
Чтобы перенос был нулевым по поведению, `apis` обязан остаться единым объектом,
импортируемым и `lesson_controller.py`, и хэндлерами, и login-слоем. См. раздел 5.

`DebuggableBonchAPI` сам по себе — расширение `BonchAPI` (78–470). Это API-слой,
логически ближе к будущему объединённому `bonch_api`/задаче 4.2. **Рекомендация:**
вынести `DebuggableBonchAPI` в отдельный модуль `bonch_api.py` (или
`debuggable_api.py`); `lesson_controller.py`, login-слой и тесты импортируют его
оттуда. Тесты уже обращаются к `main.DebuggableBonchAPI` — см. раздел 9.

---

## 3. Форматирование, рендер и кэш-хелперы

### 3.1 Модуль `formatting.py` — текст и фильтры расписания

| Символ | Строки | Сигнатура / роль | Зависимости |
|---|---|---|---|
| `filter_group_lessons_by_date` | 1316–1320 | фильтр занятий группы по дате `YYYY.MM.DD` | — (чистая) |
| `filter_personal_lessons_by_date` | 1323–1327 | фильтр личных занятий по дате `YYYY-MM-DD` | — (чистая) |
| `_week_offset_for_date` | 1330–1334 | offset недели target от today | `timedelta` |
| `_moscow_today` | 1337–1339 | дата по МСК | `datetime`, `pytz` |
| `format_timetable` | 1342–1376 | текст из объектов ЛК | `datetime` |
| `format_timetable_dict` | 1378–1441 | текст из dict-формата `TImetabels` | `datetime` |

Все шесть — чистые функции (без БД/сети/глобалов). Перенос тривиальный.

### 3.2 Модуль `rendering.py` (или `timetable_image.py`) — генерация картинок

| Символ | Строки | Роль |
|---|---|---|
| `generate_timetable_image` | 1444–1551 | PNG из объектов ЛК |
| `draw_rounded_rectangle` | 1553–1599 | примитив рисования |
| `generate_timetable_image_from_dict` | 1601–2077 | PNG из dict-формата (содержит вложенную `draw_lesson_entry` 1810) |
| `draw_lesson` | 2078–2111 | рендер одной пары |
| `draw_text_with_emoji` | 2113–2209 | текст с эмодзи-шрифтом |

Зависимости: `PIL` (`Image`, `ImageDraw`, `ImageFont`), шрифты-файлы в корне
(`G8.otf`, `Montserrat-SemiBold.ttf`, `NotoColorEmoji.ttf`, `OpenSansEmoji.ttf`,
`seguiemj.ttf`), `os`. Чистые по данным, но тяжёлый блок (~760 строк) — выделить
отдельно от текстового `formatting.py`. Используются только в `handlers/schedule.py`
(`process_image_week`, `process_group_week_navigation`).

### 3.3 Модуль `messages_cache.py` — хелперы счётчика и кэша сообщений

| Символ | Строки | Роль | Зависимости |
|---|---|---|---|
| `format_message_count` | 708–714 | счётчик «N / ≈N / N+» | — (чистая) |
| `_messages_cache_fresh` | 717–724 | свежесть кэша по TTL | — (чистая) |
| `_build_message_state` | 727–738 | сборка записи `message_states` | `time_module` |
| `_invalidate_messages_cache` | 741–745 | помечает кэш устаревшим | **`message_states`** (global) |
| `MESSAGES_CACHE_TTL_SEC` | 705 | константа TTL = 300 | — |

Проблема: `_invalidate_messages_cache` пишет в глобальный `message_states`.
Если `message_states` и эти хелперы окажутся в разных модулях — нужен общий
источник словаря. **Рекомендация:** держать `message_states` и эти хелперы
**в одном месте**. Варианты:
- (а) поместить `message_states` + `format_message_count` + `_messages_cache_*` +
  `_build_message_state` + `_invalidate_messages_cache` прямо в
  `handlers/messages.py` — всё используется только им;
- (б) отдельный `messages_cache.py`, который владеет `message_states` и
  экспортирует и словарь, и хелперы.
Вариант (б) чище для тестов (тесты импортируют `main.format_message_count` и
`main._messages_cache_fresh` — см. раздел 9). Чистые функции (`format_message_count`,
`_messages_cache_fresh`) можно держать отдельно от словаря, но `_build_message_state`
и `_invalidate_*` тесно связаны с состоянием.

### 3.4 Модуль `timetable_cache.py` — TTL-кэш расписания групп

| Символ | Строки | Роль | Зависимости |
|---|---|---|---|
| `TIMETABLE_META_FILE` | 2836 | путь sidecar-файла | `Path` |
| `TIMETABLE_TTL_HOURS` | 2837–2840 | TTL из env | `os.getenv` |
| `_write_timetable_meta` | 2843–2851 | запись метки времени | `json`, `Path` |
| `_read_timetable_meta` | 2854–2859 | чтение sidecar | `json`, `Path` |
| `_timetable_age_seconds` | 2862–2870 | возраст кэша (сек) | `datetime` |
| `_is_timetable_stale` | 2873–2877 | устарел ли кэш | — (чистая) |
| `_format_cache_age` | 2880–2890 | «N мин назад» | — (чистая) |
| `_timetable_cache_age_now` | 2893–2896 | возраст на текущий момент | `datetime`, `pytz` |

Все восемь — чистые/файловые, без зависимости от глобального `all_groups_timetable_cache`.
Перенос в `timetable_cache.py` безопасен. `TIMETABLE_TTL_HOURS` читает env —
по `config.md` его дефолт может уйти в `config.py`; путь `TIMETABLE_META_FILE` —
чистая константа. Используются в `handlers/schedule.py` и сервисе загрузки
расписания (раздел 6).

### 3.5 Прочие хелперы рядом с хэндлерами

- `parse_login_credentials` (755–774), `check_login_rate_limit` (785–798),
  `format_retry_after` (801–806), `LOGIN_CMD_RE`, `EMAIL_RE`, `_login_attempts`,
  `LOGIN_RATE_LIMIT/WINDOW` — по `security.md` это login-валидация/rate-limit.
  Хэндлеры `cmd_login`, `fsm_login_password` их импортируют из `security.py`.
- `perform_login` (4319–4341) — login-оркестратор: создаёт `DebuggableBonchAPI`,
  `LessonController`, пишет в `apis`/`controllers`/БД. Это **login-слой**
  (`auth.py`/`login_service.py`), не хэндлер. Зависит от `apis`, `controllers`,
  `bot`, `DebuggableBonchAPI`, `LessonController`, `encrypt_password`, `cursor/conn`.
- `auto_login_user` (3919–3951), `auto_start_lesson` (3953–3968) — туда же
  (`login_service.py`): фоновый автологин/автозапуск автоотметки.
- `_alert_admins_parser_broken` (666–684), `_note_parser_failure` (686–694) —
  по `config.md` это monitoring; зависят от `bot` и `_parser_failure_monitor`.

---

## 4. Что ОСТАЁТСЯ в `main.py`

После декомпозиции `main.py` становится тонким composition root:

| Символ | Строки | Остаётся, потому что |
|---|---|---|
| `bot` | 581 | корневой объект aiogram; импортируется почти всеми модулями |
| `dp` | 582 | `Dispatcher`; включает роутеры |
| `tg_session` | 555 | сессия для `bot` |
| `_background_tasks` | 594 | список фоновых задач для graceful shutdown |
| `set_bot_commands` | 4973–4980 | регистрация команд бота |
| `auto_login_all_users` | 4982–4007 | фоновый автологин при старте |
| `preload_timetable` | 5009–5020 | фоновая предзагрузка расписания |
| `heartbeat_loop` | 5022–5026 | heartbeat для healthcheck |
| `on_startup` | 5028–5046 | стартовая инициализация + запуск фоновых задач |
| `on_shutdown` | 5048–5077 | graceful shutdown: гасит контроллеры и задачи |
| `main` | 5079–5089 | точка входа: `on_startup` → `start_polling` → `on_shutdown` |
| `__main__` блок | 5091+ | запуск `asyncio.run(main())` |
| **регистрация роутеров** | новое | `dp.include_router(...)` для каждого `handlers/*.py` |

Замечания по `main.py`:
- `auto_login_all_users` и `preload_timetable` — фоновые задачи, но вызывают
  `auto_login_user`/`auto_start_lesson` (login-слой) и `get_all_groups_timetable`
  (сервис расписания). Можно оставить в `main.py` (они — часть startup-сценария)
  либо вынести в `startup.py`. **Рекомендация:** оставить в `main.py` рядом с
  `on_startup` — это сценарий запуска, а не доменная логика.
- `on_shutdown` итерирует `controllers.values()` — снова зависимость от
  глобального словаря `controllers` (раздел 5).
- `tg_session`, открытие `conn/cursor`, `bot = Bot(...)`, `dp = Dispatcher()` —
  side-effects при импорте. Часть из них (`conn/cursor`) по `db.md` уходит в
  `db.py`, прокси/`_fernet`/logging по `config.md`/`security.md` — в свои модули.
  В `main.py` остаются именно `bot`/`dp`/`tg_session`.

---

## 5. Карта глобального изменяемого состояния

Это **главный источник связности**. Пять словарей/структур, к которым
обращаются из разных будущих модулей. При декомпозиции каждый должен иметь
**ровно один владеющий модуль**, остальные импортируют его оттуда (Python
импортирует объект по ссылке — мутации видны всем; пересоздавать словарь
нельзя, иначе разъедутся ссылки).

### 5.1 `controllers` (584) — `{user_id: LessonController}`

| Модуль | Читает | Пишет |
|---|---|---|
| `handlers/autoclick.py` (`cmd_start/stop_lesson`, `cmd_status`, `cmd_my_account`, `cb_autoclick`, `send_autoclick_panel`) | ✅ | — |
| `handlers/profile.py` (`cb_logout`, строка 4946 `controllers.pop`) | — | ✅ |
| `login_service.py` (`auto_login_user` 3939/3949–3950, `auto_start_lesson` 3964) | ✅ | ✅ |
| `auth/login` (`perform_login` 4327) | — | ✅ |
| `main.py` (`on_shutdown` 5054) | ✅ | — |

**Владелец:** предлагается отдельный модуль `state.py` (или `runtime_state.py`),
который объявляет `controllers = {}`, `apis = {}`. Все перечисленные модули
импортируют `from state import controllers, apis`.

### 5.2 `apis` (585) — `{user_id: DebuggableBonchAPI}`

| Модуль | Читает | Пишет |
|---|---|---|
| `handlers/schedule.py` (`process_image_week` 2217/2223, `process_week_navigation` 2611/2615, `process_my_day` 2637/2646, `cmd_timetable` 2664/2673) | ✅ | — |
| `handlers/autoclick.py` (`cmd_my_account` 1282/1295) | ✅ | — |
| `handlers/messages.py` (`get_message_api` 3976/3984) | ✅ | — |
| `handlers/profile.py` (`menu_profile` 4416 — но `menu_profile` в `common.py`; `cb_logout` 4952 `apis.pop`) | ✅ | ✅ |
| `handlers/common.py` (`menu_profile` 4416) | ✅ | — |
| `lesson_controller.py` (`reauthenticate` 1084–1087) | — | ✅ |
| `login_service.py` (`auto_login_user` 3934/3947–3948) | ✅ | ✅ |
| `auth/login` (`perform_login` 4326) | — | ✅ |

**Владелец:** тот же `state.py`. Семь модулей зависят от `apis` — самый
«разлапистый» глобал после `all_groups_timetable_cache`.

### 5.3 `all_groups_timetable_cache` (697) — `dict | None`, кэш расписания всех групп

| Модуль | Читает | Пишет |
|---|---|---|
| `handlers/schedule.py` (8 хэндлеров: 2377/2432/2488/2566 навигация; 3028/3086/3132/3449/3553 команды) | ✅ | — (через `global`, но реально пишет только сервис) |
| `timetable_service.py` (`get_all_groups_timetable` 2913–3002 — единственный реальный writer) | ✅ | ✅ |

Особенность: хэндлеры объявляют `global all_groups_timetable_cache` и читают его
напрямую (а не только через `get_all_groups_timetable`). Это значит: модуль-владелец
кэша должен экспортировать **getter**, либо хэндлеры обязаны импортировать модуль
целиком и обращаться `timetable_service.all_groups_timetable_cache` (а не
`from ... import all_groups_timetable_cache` — иначе при пересоздании `None → dict`
импортированное имя останется `None`).

**Риск №1.** Это самый опасный для нулевого поведения глобал: `get_all_groups_timetable`
переприсваивает переменную модульного уровня (`all_groups_timetable_cache = ...`).
`from module import all_groups_timetable_cache` создаст копию ссылки, которая НЕ
обновится. **Решение:** обращаться через `import timetable_service` и
`timetable_service.all_groups_timetable_cache`, либо завести функцию-аксессор
`get_cached_all_groups()`.

### 5.4 `message_states` (702) — `{user_id: {...}}`, навигация по сообщениям

| Модуль | Читает | Пишет |
|---|---|---|
| `handlers/messages.py` (`cmd_messages` 3680/3708, `show_message_list` 3725/3728, `handle_message_callback` 3794/3805/3841/3901/3909) | ✅ | ✅ |
| `messages_cache.py` (`_invalidate_messages_cache` 743) | ✅ | ✅ |

Используется только доменом сообщений. Безопаснее всего: `message_states` живёт
в одном модуле с хелперами кэша (см. 3.3, вариант (б)) или прямо в
`handlers/messages.py`.

### 5.5 `timetable_progress_users` / `timetable_progress` / `timetable_loading` / `timetable_api` (696–700)

Связка прогресса загрузки расписания групп:

| Символ | Строки-владельцы | Кто читает/пишет |
|---|---|---|
| `timetable_api` | 696, 692–703 | `get_timetable_api` (создаёт), `cmd_groups`, `cmd_group_timetable` |
| `all_groups_timetable_cache` | см. 5.3 | — |
| `timetable_loading` | 698 | `get_all_groups_timetable` (W), `progress_updater` (R), `cmd_teachers/classrooms/...` (R через `global`) |
| `timetable_progress_users` | 699 | `get_all_groups_timetable`, `send_progress_update`, `progress_updater` |
| `timetable_progress` | 700 | `all_groups_timetable_with_progress` (W), `progress_updater` (R), `get_all_groups_timetable` (reset 2989) |

**Владелец:** все пять — внутреннее состояние сервиса загрузки расписания.
Их естественное место — модуль `timetable_service.py` (см. раздел 6), где они
объявляются и где живут все функции, которые их мутируют. Хэндлеры
`cmd_teachers`/`cmd_classrooms`/`cmd_group_timetable` читают `timetable_loading`
и `all_groups_timetable_cache` напрямую — здесь снова применима оговорка
**риска №1** (доступ через модуль, не через `from import`).

### Сводная рекомендация по состоянию

- `state.py` — владеет `controllers`, `apis` (per-user runtime API/контроллеры).
- `timetable_service.py` — владеет `timetable_api`, `all_groups_timetable_cache`,
  `timetable_loading`, `timetable_progress`, `timetable_progress_users`.
- `handlers/messages.py` (или `messages_cache.py`) — владеет `message_states`,
  `pending_lk_messages`.
- `pending_lk_messages` (746) — `{(user_id, recipient_id): {...}}`, используется
  только `cmd_send_lk` и `handle_lk_send_callback` → живёт в `handlers/messages.py`.

---

## 6. Сервис загрузки расписания групп (не хэндлеры, не main.py)

Отдельный модуль **`timetable_service.py`** — оркестрация загрузки расписания всех
групп с прогрессом и TTL:

| Функция | Строки | Роль |
|---|---|---|
| `get_timetable_api` | 2687–2703 | ленивая инициализация `TimetableBonchAPI` |
| `all_groups_timetable_with_progress` | 2705–2774 | загрузка всех групп с tqdm-прогрессом |
| `send_progress_update` | 2776–2806 | прогресс конкретному пользователю |
| `progress_updater` | 2808–2830 | фоновый цикл обновления прогресса |
| `get_all_groups_timetable` | 2908–3002 | главная точка: кэш + JSON + TTL-рефреш |
| `_refresh_timetable_quietly` | 2899–2905 | фоновое обновление без сообщений |

Зависимости: `TimetableBonchAPI`, `BROWSER_HEADERS` (из `TImetabels`),
`tqdm.asyncio`, `aiohttp`, `timetable_cache.py` (TTL-хелперы), глобальное
состояние из 5.5. Импортируется хэндлерами `schedule.py` и `preload_timetable`
в `main.py`.

---

## 7. Итоговая структура пакета

```
main.py                  bot, dp, tg_session, main(), on_startup/on_shutdown,
                         heartbeat_loop, auto_login_all_users, preload_timetable,
                         set_bot_commands, dp.include_router(...)
state.py                 controllers={}, apis={}            (владелец runtime-словарей)
config.py                env/константы                      (см. config.md)
db.py                    conn/cursor, *_settings хелперы     (см. db.md)
security.py              _fernet, encrypt/decrypt, login-rate-limit, валидация
keyboards.py / states.py клавиатуры, BTN_*, UIStates         (см. keyboards_states.md)
monitoring.py            ParserFailureMonitor, _alert_admins_parser_broken,
                         _note_parser_failure
bonch_api.py             DebuggableBonchAPI
lesson_controller.py     LessonController
login_service.py         perform_login, auto_login_user, auto_start_lesson
formatting.py            filter_*, _week_offset_for_date, _moscow_today,
                         format_timetable, format_timetable_dict
rendering.py             generate_timetable_image*, draw_*  (PIL, тяжёлый блок)
timetable_cache.py       _read/_write_timetable_meta, _*_stale, _format_cache_age,
                         TIMETABLE_META_FILE, _timetable_cache_age_now
timetable_service.py     get_all_groups_timetable и компания + их глобалы (5.5)
messages_cache.py        message_states, format_message_count, _messages_cache_*,
                         _build_message_state, _invalidate_messages_cache
handlers/
  __init__.py            опционально: список роутеров для include
  common.py              start/login/help/cancel/fallback + 4 пункта reply-меню
  schedule.py            расписание: команды + навигационные коллбэки + FSM ask_*
  autoclick.py           автоотметка: команды + cb_autoclick + send_autoclick_panel
  messages.py            сообщения ЛК: чтение/отправка + lk_* хелперы + pending_lk_messages
  profile.py             профиль: уведомления, relogin, logout
```

> Точная граница `config.py`/`db.py`/`security.py`/`keyboards.py`/`states.py` —
> в соседних анализах. Здесь они показаны как «получатели» символов, чтобы
> зафиксировать импорты хэндлеров.

---

## 8. Переход с `@dp` на `Router()` per-file

Сейчас все 49 хэндлеров висят на глобальном `dp` (создаётся строкой 582).
Aiogram 3 не позволяет регистрировать хэндлеры на `Dispatcher` из другого модуля
до того, как `dp` создан, без циклического импорта `main`. Стандартное решение —
**`Router` на каждый файл**:

В каждом `handlers/*.py`:
```python
from aiogram import Router, F
from aiogram.filters import Command
router = Router()

@router.message(Command("start"))
async def cmd_start(...): ...
```
То есть массовая замена декоратора `@dp.message(...)` → `@router.message(...)`,
`@dp.callback_query(...)` → `@router.callback_query(...)`. Имена функций,
фильтры и тела не меняются — это и есть «нулевое изменение поведения».

В `main.py` после создания `dp`:
```python
from handlers import schedule, autoclick, messages, profile, common
dp.include_router(schedule.router)
dp.include_router(autoclick.router)
dp.include_router(messages.router)
dp.include_router(profile.router)
dp.include_router(common.router)   # последним — из-за fallback
```

**Риск №2 — порядок роутеров.** Aiogram перебирает роутеры/хэндлеры в порядке
регистрации; первый совпавший выигрывает. Сейчас порядок определён порядком
строк в файле. Критичные случаи:
- `fallback_handler` (`@dp.message()` без фильтра) — обязан быть **последним**;
  его роутер (`common.router`) включается после всех остальных, и сам хэндлер —
  последним в `common.py`.
- `cmd_login` (`Command("login")`) и `fsm_login_email/password` (state-фильтры) —
  разные фильтры, конфликта нет, но проверить, что команды (`Command(...)`) не
  перехватываются state-хэндлерами: в aiogram state-хэндлер срабатывает только
  при активном FSM-состоянии, а `Command` — нет, так что порядок между ними
  безопасен.
- `cb_autoclick` ловит `F.data.startswith("m:auto:")`, `process_*_navigation`
  ловят `startswith(...)` по своим префиксам — префиксы не пересекаются.
  `handle_message_callback` (`startswith("msg_")`) и `cb_msg_*` (`==`) —
  `m:msg:inbox`/`m:msg:write` не начинаются с `msg_`, конфликта нет.
- В пределах `schedule.py`: навигационные коллбэки используют непересекающиеся
  префиксы — порядок между ними не важен, но сохранить исходный для надёжности.

**Рекомендация:** сохранить исходный относительный порядок хэндлеров внутри
каждого файла и включать `common.router` последним. Это гарантирует идентичную
маршрутизацию.

**Риск №3 — циклические импорты.** `handlers/common.py` вызывает `perform_login`
(login_service), `schedule.py` вызывает `cmd_timetable` внутри себя (ок),
`autoclick.py` ↔ `login_service` (login_service создаёт `LessonController`,
хэндлеры читают `controllers`). Чтобы не словить цикл:
- `bot` и `dp` берутся из `main.py`, но `main.py` импортирует `handlers/*` —
  значит `handlers/*` не должны импортировать `main`. **Решение:** `bot` вынести
  либо в `state.py`/`config.py`, либо передавать через middleware/DI. Минимальный
  вариант с нулевым поведением — отдельный модуль `bot_instance.py` с `bot`,
  который импортируют и `main.py`, и хэндлеры, и `lesson_controller.py`.
- `state.py`, `config.py`, `db.py`, `formatting.py`, `keyboards.py` — листья
  графа, их импортируют все, сами они не импортируют хэндлеры.

---

## 9. Влияние на тесты

Тесты сейчас обращаются к символам через `import main` / `main.<symbol>`
(`conftest.py` подменяет `main.conn`/`main.cursor`/`main._login_attempts`).
После декомпозиции импорты придётся переключить на новые модули. Полный список
затронутых `main.*` символов (из `grep` по `tests/`):

### 9.1 Переключаются на новые модули

| Тестовый файл | Сейчас | Новый модуль |
|---|---|---|
| `test_main_formatting.py` | `main.format_timetable_dict` | `formatting.format_timetable_dict` |
| `test_timetable_presets.py` | `main.filter_group_lessons_by_date`, `main.filter_personal_lessons_by_date`, `main._week_offset_for_date` | `formatting.*` |
| `test_messages_cache.py` | `main.format_message_count`, `main._messages_cache_fresh` | `messages_cache.*` |
| `test_timetable_cache.py` | `main._read_timetable_meta`, `main._write_timetable_meta`, `main._timetable_age_seconds`, `main._is_timetable_stale`, `main._format_cache_age` | `timetable_cache.*` |
| `test_main_lesson_logic.py` | `main.LessonController` | `lesson_controller.LessonController` |
| `test_click_lesson.py` | `main.DebuggableBonchAPI`, `main.aiohttp` | `bonch_api.DebuggableBonchAPI` (и `aiohttp` напрямую) |
| `test_main_security.py` | `main.encrypt_password`, `main.decrypt_password`, `main._fernet`, `main.parse_login_credentials`, `main.check_login_rate_limit`, `main.format_retry_after`, `main._login_attempts`, `main.LOGIN_RATE_LIMIT`, `main.LOGIN_RATE_WINDOW_SEC` | `security.*` (см. security.md) |
| `test_main_config.py` | `main._resolve_log_level`, `main._write_heartbeat` | `config.*` / `main.*` (heartbeat остаётся в main) |
| `test_parser_monitor.py` | `main.ParserFailureMonitor`, `main._parse_admin_ids` | `monitoring.*` / `config.*` |
| `test_main_keyboards.py` | `main.*_kb`, `main.BTN_*`, `main.HELP_TEXT`, `main.notify_settings_text/kb`, `main.get_*_week_navigation_buttons` | `keyboards.*` (см. keyboards_states.md) |
| `test_main_settings.py` | `main.get_notify_settings`, `main.set_notify_*`, `main.get/set_autoclick_enabled`, `main.is_registered`, `main.NOTIFY_DEFAULT_MINUTES`, `main.NOTIFY_MINUTE_OPTIONS` | `db.*` / `config.*` (см. db.md) |

> `show_message_list` упомянута в задании как тестовый импорт — в текущих тестах
> прямого `main.show_message_list` НЕ найдено. После переноса она будет
> `messages.show_message_list` (модуль `handlers/messages.py`); если тест на неё
> появится — импортировать оттуда.

### 9.2 `conftest.py` — фикстуры

- `temp_db` подменяет `main.conn` / `main.cursor`. После переноса БД в `db.py`
  фикстура должна подменять `db.conn` / `db.cursor` (см. db.md). Если хэндлеры
  и `db.py`-хелперы используют `cursor` через `import db; db.cursor`, подмена
  сработает; если через `from db import cursor` — НЕ сработает (та же оговорка
  риска №1). Это надо учесть согласованно с анализом `db.md`.
- `reset_rate_limit` подменяет `main._login_attempts` → станет `security._login_attempts`.

### 9.3 Рекомендация по тестам

Переключение импортов в тестах — механическое (`main.X` → `module.X`). Чтобы
изменение поведения осталось нулевым и для тестов:
- сохранить **имена** всех функций/классов/констант без переименований;
- модули-владельцы изменяемого состояния (`state`, `db`, `timetable_service`,
  `messages_cache`) тесты должны импортировать **целиком** и обращаться через
  атрибут модуля, иначе подмена/мутация в фикстурах разъедется.

---

## 10. Главные риски связности (резюме)

1. **Глобальные словари, переприсваиваемые на уровне модуля**
   (`all_groups_timetable_cache`, `timetable_loading`, `timetable_api`).
   `from module import name` зафиксирует устаревшую ссылку/значение. Обязателен
   доступ через модуль (`timetable_service.all_groups_timetable_cache`) или
   функция-аксессор.
2. **`apis` и `controllers`** читают/пишут 7 и 5 модулей соответственно. Нужен
   единый владеющий модуль `state.py`; все импортируют один и тот же объект.
3. **`bot`** нужен почти всем (хэндлеры, `LessonController`, monitoring, login).
   `handlers/*` не должны импортировать `main` → `bot` в отдельный
   `bot_instance.py`/`state.py`.
4. **Порядок роутеров**: `fallback_handler` (`@dp.message()`) обязан быть
   последним; `common.router` включать последним; внутри файлов сохранить
   исходный порядок хэндлеров.
5. **Циклические импорты** между `handlers/*`, `login_service`,
   `lesson_controller` — разрывать выносом `bot` и runtime-состояния в
   листовые модули.
6. **`message_states` + хелперы кэша** должны жить в одном модуле (или владелец
   экспортирует и словарь, и функции), иначе `_invalidate_messages_cache`
   мутирует не тот словарь.
7. **`DebuggableBonchAPI`** создаётся в `lesson_controller` (`reauthenticate`),
   `login_service` (`auto_login_user`), `auth` (`perform_login`) — единый
   источник `bonch_api.py`.
8. **`conftest.py`-фикстуры** (`temp_db`, `reset_rate_limit`) подменяют атрибуты
   модуля — после переноса состояния модули обязаны экспонировать его как
   атрибут модуля, а не как `from import`-копию.
