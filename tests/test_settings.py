"""Тесты DB-хелперов: регистрация, настройки уведомлений и автоотметки.

Используют фикстуру temp_db — временную in-memory БД, чтобы не трогать
настоящий users.db.
"""
from satanbonchbot import db


def _register(conn, user_id: int) -> None:
    conn.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)",
        (user_id, "user@sut.ru", "pw"),
    )
    conn.commit()


# --- is_registered -----------------------------------------------------------

def test_is_registered_true_for_known_user(temp_db):
    _register(temp_db, 1)
    assert db.is_registered(1) is True


def test_is_registered_false_for_unknown_user(temp_db):
    assert db.is_registered(999) is False


# --- get_notify_settings -----------------------------------------------------

def test_notify_settings_default_for_unknown_user(temp_db):
    assert db.get_notify_settings(424242) == (True, db.NOTIFY_DEFAULT_MINUTES)


def test_notify_settings_default_for_fresh_user(temp_db):
    _register(temp_db, 2)
    assert db.get_notify_settings(2) == (True, 10)


def test_set_notify_minutes_roundtrip(temp_db):
    _register(temp_db, 3)
    db.set_notify_minutes(3, 30)
    enabled, minutes = db.get_notify_settings(3)
    assert enabled is True
    assert minutes == 30


def test_set_notify_enabled_off_and_on(temp_db):
    _register(temp_db, 4)
    db.set_notify_enabled(4, False)
    assert db.get_notify_settings(4)[0] is False
    db.set_notify_enabled(4, True)
    assert db.get_notify_settings(4)[0] is True


def test_notify_minutes_preserved_when_toggling_enabled(temp_db):
    _register(temp_db, 5)
    db.set_notify_minutes(5, 15)
    db.set_notify_enabled(5, False)
    # Выключение уведомлений не сбрасывает выбранное время.
    assert db.get_notify_settings(5) == (False, 15)


# --- autoclick_enabled -------------------------------------------------------

def test_autoclick_enabled_default_true(temp_db):
    _register(temp_db, 6)
    assert db.get_autoclick_enabled(6) is True


def test_autoclick_enabled_unknown_user_defaults_true(temp_db):
    assert db.get_autoclick_enabled(555) is True


def test_set_autoclick_disabled_persists(temp_db):
    _register(temp_db, 7)
    db.set_autoclick_enabled(7, False)
    assert db.get_autoclick_enabled(7) is False


def test_set_autoclick_re_enabled(temp_db):
    _register(temp_db, 8)
    db.set_autoclick_enabled(8, False)
    db.set_autoclick_enabled(8, True)
    assert db.get_autoclick_enabled(8) is True


# --- тумблеры уведомлений бота (B.1) -----------------------------------------

def test_notify_schedule_enabled_default_true_for_fresh_user(temp_db):
    _register(temp_db, 10)
    assert db.get_notify_schedule_enabled(10) is True


def test_notify_schedule_enabled_unknown_user_defaults_true(temp_db):
    assert db.get_notify_schedule_enabled(777) is True


def test_set_notify_schedule_disabled_persists(temp_db):
    _register(temp_db, 11)
    db.set_notify_schedule_enabled(11, False)
    assert db.get_notify_schedule_enabled(11) is False
    db.set_notify_schedule_enabled(11, True)
    assert db.get_notify_schedule_enabled(11) is True


def test_notify_messages_enabled_default_true_for_fresh_user(temp_db):
    _register(temp_db, 12)
    assert db.get_notify_messages_enabled(12) is True


def test_set_notify_messages_disabled_persists(temp_db):
    _register(temp_db, 13)
    db.set_notify_messages_enabled(13, False)
    assert db.get_notify_messages_enabled(13) is False


def test_notify_toggles_are_independent(temp_db):
    """Выключение одного тумблера не трогает второй."""
    _register(temp_db, 14)
    db.set_notify_schedule_enabled(14, False)
    assert db.get_notify_schedule_enabled(14) is False
    assert db.get_notify_messages_enabled(14) is True
