"""Тесты TTL-кэша расписания групп: метаданные, возраст, устаревание."""
from datetime import datetime, timedelta, timezone

from satanbonchbot import timetable_cache

UTC = timezone.utc


# --- _write_timetable_meta / _read_timetable_meta ----------------------------

def test_write_then_read_meta_roundtrip(tmp_path):
    path = tmp_path / "meta.json"
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    timetable_cache._write_timetable_meta(now, path)
    meta = timetable_cache._read_timetable_meta(path)
    assert meta is not None
    assert meta["fetched_at"] == now.isoformat()


def test_read_meta_missing_file_returns_none(tmp_path):
    assert timetable_cache._read_timetable_meta(tmp_path / "nope.json") is None


def test_read_meta_corrupt_returns_none(tmp_path):
    path = tmp_path / "meta.json"
    path.write_text("{ битый json", encoding="utf-8")
    assert timetable_cache._read_timetable_meta(path) is None


# --- _timetable_age_seconds --------------------------------------------------

def test_age_seconds_for_recent_fetch():
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    meta = {"fetched_at": (now - timedelta(minutes=30)).isoformat()}
    assert timetable_cache._timetable_age_seconds(meta, now) == 1800


def test_age_seconds_none_when_no_meta():
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    assert timetable_cache._timetable_age_seconds(None, now) is None
    assert timetable_cache._timetable_age_seconds({}, now) is None


def test_age_seconds_none_when_fetched_at_corrupt():
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    assert timetable_cache._timetable_age_seconds({"fetched_at": "не дата"}, now) is None


# --- _is_timetable_stale -----------------------------------------------------

def test_fresh_cache_not_stale():
    assert timetable_cache._is_timetable_stale(3600, ttl_hours=6) is False


def test_old_cache_is_stale():
    assert timetable_cache._is_timetable_stale(7 * 3600, ttl_hours=6) is True


def test_missing_age_treated_as_stale():
    assert timetable_cache._is_timetable_stale(None, ttl_hours=6) is True


# --- _format_cache_age -------------------------------------------------------

def test_format_age_just_now():
    assert timetable_cache._format_cache_age(30) == "только что"


def test_format_age_minutes():
    assert timetable_cache._format_cache_age(5 * 60) == "5 мин назад"


def test_format_age_hours():
    assert timetable_cache._format_cache_age(3 * 3600) == "3 ч назад"


def test_format_age_unknown():
    assert timetable_cache._format_cache_age(None) == "время неизвестно"


# --- _write_first_day_cache / _read_first_day_cache --------------------------

def test_write_then_read_first_day_roundtrip(tmp_path):
    path = tmp_path / "first_day.json"
    timetable_cache._write_first_day_cache("2026-08-24", path)
    assert timetable_cache._read_first_day_cache(path) == "2026-08-24"


def test_read_first_day_missing_file_returns_none(tmp_path):
    assert timetable_cache._read_first_day_cache(tmp_path / "nope.json") is None


def test_read_first_day_corrupt_returns_none(tmp_path):
    path = tmp_path / "first_day.json"
    path.write_text("{ битый json", encoding="utf-8")
    assert timetable_cache._read_first_day_cache(path) is None


# --- get_first_day -------------------------------------------------------------

def test_get_first_day_prefers_cache_over_env(tmp_path, monkeypatch):
    path = tmp_path / "first_day.json"
    timetable_cache._write_first_day_cache("2026-08-24", path)
    monkeypatch.setattr(timetable_cache, "FIRST_DAY_CACHE_FILE", path)
    monkeypatch.setenv("FIRST_DAY", "2099-01-01")
    assert timetable_cache.get_first_day() == "2026-08-24"


def test_get_first_day_falls_back_to_env_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(timetable_cache, "FIRST_DAY_CACHE_FILE", tmp_path / "nope.json")
    monkeypatch.setenv("FIRST_DAY", "2026-08-24")
    assert timetable_cache.get_first_day() == "2026-08-24"


def test_get_first_day_falls_back_to_hardcoded_default(tmp_path, monkeypatch):
    monkeypatch.setattr(timetable_cache, "FIRST_DAY_CACHE_FILE", tmp_path / "nope.json")
    monkeypatch.delenv("FIRST_DAY", raising=False)
    assert timetable_cache.get_first_day() == timetable_cache.FIRST_DAY_FALLBACK
