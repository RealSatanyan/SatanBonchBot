"""Тесты мониторинга сбоев парсера ЛК: скользящее окно, порог, cooldown."""
from datetime import datetime, timedelta

import main

BASE = datetime(2026, 5, 18, 12, 0)


def _monitor(**kw):
    opts = dict(window_minutes=30, threshold_users=3, cooldown_minutes=60)
    opts.update(kw)
    return main.ParserFailureMonitor(**opts)


# --- ParserFailureMonitor ----------------------------------------------------

def test_distinct_users_counts_unique_not_repeats():
    m = _monitor()
    m.record_failure(1, BASE)
    m.record_failure(1, BASE)  # тот же пользователь — не должен удваивать
    m.record_failure(2, BASE)
    assert m.distinct_users(BASE) == 2


def test_old_events_pruned_outside_window():
    m = _monitor(window_minutes=30)
    m.record_failure(1, BASE)
    later = BASE + timedelta(minutes=31)
    m.record_failure(2, later)
    assert m.distinct_users(later) == 1  # пользователь 1 выпал из окна


def test_no_alert_below_threshold():
    m = _monitor(threshold_users=3)
    m.record_failure(1, BASE)
    m.record_failure(2, BASE)
    assert m.should_alert(BASE) is False


def test_alert_fires_at_threshold():
    m = _monitor(threshold_users=3)
    for uid in (1, 2, 3):
        m.record_failure(uid, BASE)
    assert m.should_alert(BASE) is True


def test_alert_fires_once_then_cooldown_silences():
    m = _monitor(threshold_users=3, cooldown_minutes=60)
    for uid in (1, 2, 3):
        m.record_failure(uid, BASE)
    assert m.should_alert(BASE) is True
    assert m.should_alert(BASE + timedelta(minutes=5)) is False


def test_alert_fires_again_after_cooldown():
    m = _monitor(threshold_users=3, cooldown_minutes=60)
    for uid in (1, 2, 3):
        m.record_failure(uid, BASE)
    assert m.should_alert(BASE) is True
    after = BASE + timedelta(minutes=61)
    for uid in (4, 5, 6):
        m.record_failure(uid, after)
    assert m.should_alert(after) is True


# --- _parse_admin_ids --------------------------------------------------------

def test_parse_admin_ids_basic():
    assert main._parse_admin_ids("123,456") == [123, 456]


def test_parse_admin_ids_trims_and_skips_junk():
    assert main._parse_admin_ids(" 123 , abc, 456 ") == [123, 456]


def test_parse_admin_ids_empty_or_none():
    assert main._parse_admin_ids("") == []
    assert main._parse_admin_ids(None) == []
