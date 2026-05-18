"""Тесты сборщиков клавиатур, текста настроек уведомлений и справки."""
import main


def _reply_button_texts(kb) -> list:
    return [btn.text for row in kb.keyboard for btn in row]


def _inline_buttons(kb) -> list:
    return [btn for row in kb.inline_keyboard for btn in row]


def _callbacks(kb) -> list:
    return [btn.callback_data for btn in _inline_buttons(kb)]


# --- main_menu_kb ------------------------------------------------------------

def test_main_menu_has_all_sections():
    texts = _reply_button_texts(main.main_menu_kb())
    assert main.BTN_SCHEDULE in texts
    assert main.BTN_AUTOCLICK in texts
    assert main.BTN_MESSAGES in texts
    assert main.BTN_PROFILE in texts
    assert main.BTN_HELP in texts


# --- schedule_menu_kb --------------------------------------------------------

def test_schedule_menu_shows_my_schedule_only_when_logged_in():
    logged_in = _callbacks(main.schedule_menu_kb(logged_in=True))
    logged_out = _callbacks(main.schedule_menu_kb(logged_in=False))
    assert "m:sched:my" in logged_in
    assert "m:sched:my" not in logged_out


def test_schedule_menu_always_has_group_teacher_room():
    callbacks = _callbacks(main.schedule_menu_kb(logged_in=False))
    assert {"m:sched:group", "m:sched:teacher", "m:sched:room"} <= set(callbacks)


def test_schedule_menu_has_reload_button():
    callbacks = _callbacks(main.schedule_menu_kb(logged_in=False))
    assert "m:sched:reload" in callbacks


# --- пресеты «Сегодня» / «Завтра» --------------------------------------------

def test_week_navigation_has_today_and_tomorrow_presets():
    callbacks = _callbacks(main.get_week_navigation_buttons(week_offset=0))
    assert "my_day_0" in callbacks
    assert "my_day_1" in callbacks


def test_group_navigation_has_today_and_tomorrow_presets():
    callbacks = _callbacks(main.get_group_week_navigation_buttons("ИКВТ-21", week_number=0))
    day_presets = [c for c in callbacks if c and c.startswith("group_day_")]
    assert len(day_presets) == 2
    assert any(c.endswith("_0") for c in day_presets)
    assert any(c.endswith("_1") for c in day_presets)


# --- autoclick_menu_kb -------------------------------------------------------

def test_autoclick_menu_shows_stop_when_running():
    callbacks = _callbacks(main.autoclick_menu_kb(is_running=True))
    assert "m:auto:stop" in callbacks
    assert "m:auto:start" not in callbacks


def test_autoclick_menu_shows_start_when_stopped():
    callbacks = _callbacks(main.autoclick_menu_kb(is_running=False))
    assert "m:auto:start" in callbacks
    assert "m:auto:stop" not in callbacks


# --- profile_menu_kb ---------------------------------------------------------

def test_profile_menu_has_notifications_entry():
    assert "m:profile:notify" in _callbacks(main.profile_menu_kb())


# --- notify_settings_kb ------------------------------------------------------

def test_notify_settings_kb_enabled_shows_minute_options():
    callbacks = _callbacks(main.notify_settings_kb(enabled=True, minutes=10))
    assert "m:notify:toggle" in callbacks
    for opt in main.NOTIFY_MINUTE_OPTIONS:
        assert f"m:notify:min:{opt}" in callbacks


def test_notify_settings_kb_disabled_hides_minute_options():
    callbacks = _callbacks(main.notify_settings_kb(enabled=False, minutes=10))
    assert callbacks == ["m:notify:toggle"]


def test_notify_settings_kb_marks_current_minute():
    buttons = _inline_buttons(main.notify_settings_kb(enabled=True, minutes=15))
    marked = [b.text for b in buttons if "✅" in b.text]
    assert len(marked) == 1
    assert "15" in marked[0]


# --- notify_settings_text ----------------------------------------------------

def test_notify_settings_text_enabled_mentions_minutes():
    text = main.notify_settings_text(enabled=True, minutes=30)
    assert "30" in text


def test_notify_settings_text_disabled():
    text = main.notify_settings_text(enabled=False, minutes=10)
    assert "выключены" in text.lower()


# --- HELP_TEXT ---------------------------------------------------------------

def test_help_text_covers_main_sections():
    help_text = main.HELP_TEXT
    for keyword in ("Расписание", "Автоотметка", "Сообщения", "Профиль", "/login", "/help"):
        assert keyword in help_text
