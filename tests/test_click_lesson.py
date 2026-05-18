"""Тесты критичного пути автоотметки: click_start_lesson и
get_upcoming_start_lesson_details.

Сеть замокана (aiohttp.ClientSession подменён), HTML берётся из фикстур.
DebuggableBonchAPI создаёт aiohttp.CookieJar в __init__, поэтому экземпляр
создаётся внутри event loop (через asyncio.run).
"""
import asyncio
from datetime import datetime

import pytest

import main


class _FakeResponse:
    """Заглушка ответа aiohttp на POST клика занятия."""

    def __init__(self, status=200, text="OK"):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._text


class _FakeSession:
    """Заглушка aiohttp.ClientSession: не ходит в сеть, отвечает 200."""

    response_status = 200
    response_text = "OK"

    def __init__(self, **kwargs):
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data=None, proxy=None):
        self.posts.append(data)
        return _FakeResponse(self.response_status, self.response_text)


def _patch_network(monkeypatch, session_cls=_FakeSession):
    monkeypatch.setattr(main.aiohttp, "ClientSession", session_cls)
    monkeypatch.setattr(main.aiohttp, "TCPConnector", lambda **kw: object())


def _api_with_timetable(html):
    """DebuggableBonchAPI с подменённым get_raw_timetable (без сети)."""
    api = main.DebuggableBonchAPI()

    async def _fake_raw(*args, **kwargs):
        return html

    api.get_raw_timetable = _fake_raw
    return api


# --- click_start_lesson ------------------------------------------------------

def test_click_start_lesson_clicks_every_candidate(monkeypatch, load_fixture):
    """raspisanie_with_lessons.html → два кандидата → два успешных клика."""
    html = load_fixture("raspisanie_with_lessons.html")
    _patch_network(monkeypatch)

    async def scenario():
        api = _api_with_timetable(html)
        return await api.click_start_lesson(user_id=1)

    assert asyncio.run(scenario()) == 2


def test_click_start_lesson_no_candidates_returns_zero(monkeypatch, load_fixture):
    """raspisanie_no_candidates.html → 0 кликов, без исключения."""
    html = load_fixture("raspisanie_no_candidates.html")
    _patch_network(monkeypatch)

    async def scenario():
        api = _api_with_timetable(html)
        return await api.click_start_lesson(user_id=1)

    assert asyncio.run(scenario()) == 0


def test_click_start_lesson_counts_only_status_200(monkeypatch, load_fixture):
    """Ответ сервера не 200 → клик не засчитывается."""
    html = load_fixture("raspisanie_with_lessons.html")

    class _FailSession(_FakeSession):
        response_status = 500

    _patch_network(monkeypatch, _FailSession)

    async def scenario():
        api = _api_with_timetable(html)
        return await api.click_start_lesson(user_id=1)

    assert asyncio.run(scenario()) == 0


def test_click_start_lesson_raises_on_expired_session(monkeypatch):
    """HTML с редиректом login=no → ValueError (нужна переавторизация)."""
    _patch_network(monkeypatch)

    async def scenario():
        api = _api_with_timetable("<html>index.php?login=no</html>")
        return await api.click_start_lesson(user_id=1)

    with pytest.raises(ValueError):
        asyncio.run(scenario())


# --- get_upcoming_start_lesson_details ---------------------------------------

def test_upcoming_lesson_details_within_window(load_fixture):
    """За 10 мин до пары 3 (13:00) на 18.05.2026 → детали пары."""
    html = load_fixture("raspisanie_today.html")
    now = datetime(2026, 5, 18, 12, 50)

    async def scenario():
        api = _api_with_timetable(html)
        return await api.get_upcoming_start_lesson_details(
            now_dt=now, target_pair_index=2, window_minutes=15
        )

    details = asyncio.run(scenario())
    assert details is not None
    assert details["pair_number"] == 3
    assert details["subject"] == "Базы данных"


def test_upcoming_lesson_details_none_when_too_early(load_fixture):
    """Пара далеко впереди (вне окна 15 мин) → None."""
    html = load_fixture("raspisanie_today.html")
    now = datetime(2026, 5, 18, 11, 0)  # до пары 3 ещё ~2 часа

    async def scenario():
        api = _api_with_timetable(html)
        return await api.get_upcoming_start_lesson_details(
            now_dt=now, target_pair_index=2, window_minutes=15
        )

    assert asyncio.run(scenario()) is None


def test_upcoming_lesson_details_none_after_start(load_fixture):
    """Пара уже началась (delta < 0) → None."""
    html = load_fixture("raspisanie_today.html")
    now = datetime(2026, 5, 18, 13, 30)

    async def scenario():
        api = _api_with_timetable(html)
        return await api.get_upcoming_start_lesson_details(
            now_dt=now, target_pair_index=2, window_minutes=15
        )

    assert asyncio.run(scenario()) is None
