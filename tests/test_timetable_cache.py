"""Тесты TTL-кэша расписания групп: метаданные, возраст, устаревание."""
from datetime import datetime, timedelta, timezone

import main

UTC = timezone.utc


# --- _write_timetable_meta / _read_timetable_meta ----------------------------

def test_write_then_read_meta_roundtrip(tmp_path):
    path = tmp_path / "meta.json"
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    main._write_timetable_meta(now, path)
    meta = main._read_timetable_meta(path)
    assert meta is not None
    assert meta["fetched_at"] == now.isoformat()


def test_read_meta_missing_file_returns_none(tmp_path):
    assert main._read_timetable_meta(tmp_path / "nope.json") is None


def test_read_meta_corrupt_returns_none(tmp_path):
    path = tmp_path / "meta.json"
    path.write_text("{ битый json", encoding="utf-8")
    assert main._read_timetable_meta(path) is None


# --- _timetable_age_seconds --------------------------------------------------

def test_age_seconds_for_recent_fetch():
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    meta = {"fetched_at": (now - timedelta(minutes=30)).isoformat()}
    assert main._timetable_age_seconds(meta, now) == 1800


def test_age_seconds_none_when_no_meta():
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    assert main._timetable_age_seconds(None, now) is None
    assert main._timetable_age_seconds({}, now) is None


def test_age_seconds_none_when_fetched_at_corrupt():
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    assert main._timetable_age_seconds({"fetched_at": "не дата"}, now) is None


# --- _is_timetable_stale -----------------------------------------------------

def test_fresh_cache_not_stale():
    assert main._is_timetable_stale(3600, ttl_hours=6) is False


def test_old_cache_is_stale():
    assert main._is_timetable_stale(7 * 3600, ttl_hours=6) is True


def test_missing_age_treated_as_stale():
    assert main._is_timetable_stale(None, ttl_hours=6) is True


# --- _format_cache_age -------------------------------------------------------

def test_format_age_just_now():
    assert main._format_cache_age(30) == "только что"


def test_format_age_minutes():
    assert main._format_cache_age(5 * 60) == "5 мин назад"


def test_format_age_hours():
    assert main._format_cache_age(3 * 3600) == "3 ч назад"


def test_format_age_unknown():
    assert main._format_cache_age(None) == "время неизвестно"
