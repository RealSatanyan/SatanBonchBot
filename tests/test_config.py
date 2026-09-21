"""Тесты конфигурационных хелперов main.py (уровень логов, heartbeat и т.п.)."""
import importlib
import logging
import os
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
    stale = time.time() - 500
    os.utime(hb, (stale, stale))
    config._write_heartbeat(hb)
    assert time.time() - hb.stat().st_mtime < 5


# --- порядок чтения LK_CONCURRENCY/LK_LOGIN_DELAY_SEC/LK_LOGIN_JITTER_SEC ----

def test_lk_throttle_settings_read_after_load_dotenv(monkeypatch):
    """Регрессия: эти три константы читались через os.getenv() ДО вызова
    load_dotenv(), поэтому значение, заданное только в .env-файле (не в
    реальном окружении процесса) — единственный канал конфигурации в
    docker-compose.yml — никогда не подхватывалось: оператор увеличивал
    троттлинг запросов к lk.sut.ru в .env, а бот молча оставался на дефолтах."""
    monkeypatch.delenv("LK_CONCURRENCY", raising=False)

    def _fake_load_dotenv(*args, **kwargs):
        # Имитирует эффект настоящего load_dotenv(): значение появляется в
        # окружении процесса ТОЛЬКО в момент вызова load_dotenv().
        os.environ["LK_CONCURRENCY"] = "7"

    # Патчим dotenv.load_dotenv, а не config.load_dotenv: importlib.reload
    # заново выполняет `from dotenv import load_dotenv` config.py, которое
    # перезаписало бы монки-патч атрибута модуля до того, как выполнится сам
    # вызов load_dotenv().
    monkeypatch.setattr("dotenv.load_dotenv", _fake_load_dotenv)
    try:
        importlib.reload(config)
        assert config.LK_CONCURRENCY == 7
    finally:
        os.environ.pop("LK_CONCURRENCY", None)
        importlib.reload(config)
