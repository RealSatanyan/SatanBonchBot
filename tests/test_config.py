"""Тесты конфигурационных хелперов main.py (уровень логов, heartbeat и т.п.)."""
import logging
import time

from satanbonchbot import config


# --- _resolve_log_level ------------------------------------------------------

def test_resolve_log_level_known_names():
    assert config._resolve_log_level("DEBUG") == logging.DEBUG
    assert config._resolve_log_level("INFO") == logging.INFO
    assert config._resolve_log_level("WARNING") == logging.WARNING
    assert config._resolve_log_level("ERROR") == logging.ERROR


def test_resolve_log_level_is_case_insensitive_and_trims():
    assert config._resolve_log_level("  warning  ") == logging.WARNING
    assert config._resolve_log_level("info") == logging.INFO


def test_resolve_log_level_unknown_falls_back_to_info():
    assert config._resolve_log_level("LOUD") == logging.INFO
    assert config._resolve_log_level("") == logging.INFO
    assert config._resolve_log_level(None) == logging.INFO


# --- _write_heartbeat --------------------------------------------------------

def test_write_heartbeat_creates_fresh_file(tmp_path):
    hb = tmp_path / "heartbeat"
    config._write_heartbeat(hb)
    assert hb.exists()
    assert time.time() - hb.stat().st_mtime < 5


def test_write_heartbeat_refreshes_existing_file(tmp_path):
    hb = tmp_path / "heartbeat"
    hb.touch()
    import os
    stale = time.time() - 500
    os.utime(hb, (stale, stale))
    config._write_heartbeat(hb)
    assert time.time() - hb.stat().st_mtime < 5
