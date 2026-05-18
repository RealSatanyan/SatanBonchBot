"""Тесты Docker healthcheck — проверка свежести heartbeat-файла бота."""
import os
import time

import healthcheck


def test_healthcheck_fails_when_file_missing(tmp_path):
    ok, msg = healthcheck.check(tmp_path / "nope", max_age_sec=120)
    assert ok is False
    assert "отсутствует" in msg


def test_healthcheck_ok_for_fresh_file(tmp_path):
    hb = tmp_path / "hb"
    hb.touch()
    ok, msg = healthcheck.check(hb, max_age_sec=120)
    assert ok is True
    assert "ok" in msg


def test_healthcheck_fails_for_stale_file(tmp_path):
    hb = tmp_path / "hb"
    hb.touch()
    stale = time.time() - 500
    os.utime(hb, (stale, stale))
    ok, msg = healthcheck.check(hb, max_age_sec=120)
    assert ok is False
    assert "устарел" in msg
