"""Тесты timetable_service: кэш расписания всех групп, TTL, прогресс.

Сеть и tqdm замоканы: get_timetable_api и all_groups_timetable_with_progress
подменяются фейками. Изменяемое состояние сервиса изолируется фикстурой
reset_timetable_service.
"""
import asyncio

from satanbonchbot.timetable_service import  get_all_groups_timetable, send_progress_update


SAMPLE = {"ИКВ-11": [{"Предмет": "Физика"}], "ИКВ-12": [{"Предмет": "Химия"}]}


class _FakeProgressMessage:
    """Фейк aiogram Message: edit_text только записывает тексты."""

    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)


# --- get_all_groups_timetable: кэш в памяти ----------------------------------

def test_returns_in_memory_cache_without_reload(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = SAMPLE
    # Кэш не устарел — фонового обновления быть не должно.
    monkeypatch.setattr(ts, "_is_timetable_stale", lambda *a, **kw: False)

    result = asyncio.run(get_all_groups_timetable())

    assert result == SAMPLE


def test_stale_cache_triggers_background_refresh(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service
    ts.all_groups_timetable_cache = SAMPLE
    monkeypatch.setattr(ts, "_is_timetable_stale", lambda *a, **kw: True)

    refreshed = []

    async def _fake_refresh():
        refreshed.append(True)

    monkeypatch.setattr(ts, "_refresh_timetable_quietly", _fake_refresh)

    async def scenario():
        result = await get_all_groups_timetable()
        # Даём запущенной create_task фоновой корутине отработать.
        await asyncio.sleep(0)
        return result

    result = asyncio.run(scenario())

    assert result == SAMPLE
    assert refreshed == [True]


# --- get_all_groups_timetable: загрузка из JSON ------------------------------

def test_loads_from_json_when_cache_empty(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service
    monkeypatch.setattr(ts.TimetableBonchAPI, "load_from_json", staticmethod(lambda *a: SAMPLE))

    result = asyncio.run(get_all_groups_timetable())

    assert result == SAMPLE
    assert ts.all_groups_timetable_cache == SAMPLE


# --- get_all_groups_timetable: принудительная перезагрузка -------------------

def _patch_force_reload(monkeypatch, ts, loader):
    async def _fake_api():
        return object()

    monkeypatch.setattr(ts, "get_timetable_api", _fake_api)
    monkeypatch.setattr(ts, "all_groups_timetable_with_progress", loader)
    monkeypatch.setattr(ts, "_write_timetable_meta", lambda *a, **kw: None)


def test_force_reload_loads_from_server(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service

    async def _loader(api):
        return SAMPLE

    _patch_force_reload(monkeypatch, ts, _loader)

    result = asyncio.run(get_all_groups_timetable(force_reload=True))

    assert result == SAMPLE
    assert ts.timetable_loading is False  # флаг сброшен в finally


def test_force_reload_resets_progress_after_load(monkeypatch, reset_timetable_service):
    """finally в get_all_groups_timetable обязан сбросить timetable_progress."""
    ts = reset_timetable_service
    ts.timetable_progress = {'current': 99, 'total': 99, 'start_time': object()}

    async def _loader(api):
        return SAMPLE

    _patch_force_reload(monkeypatch, ts, _loader)

    asyncio.run(get_all_groups_timetable(force_reload=True))

    assert ts.timetable_progress == {'current': 0, 'total': 0, 'start_time': None}


def test_force_reload_propagates_error_and_clears_flag(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service

    async def _failing_loader(api):
        raise RuntimeError("сервер недоступен")

    _patch_force_reload(monkeypatch, ts, _failing_loader)

    try:
        asyncio.run(get_all_groups_timetable(force_reload=True))
        raised = False
    except RuntimeError:
        raised = True

    assert raised is True
    assert ts.timetable_loading is False


# --- send_progress_update ----------------------------------------------------

def test_send_progress_update_skips_unknown_user(reset_timetable_service):
    from datetime import datetime

    # Пользователя нет в timetable_progress_users — вызов не должен падать.
    asyncio.run(send_progress_update(1, current=1, total=10, start_time=datetime.now()))


def test_send_progress_update_edits_message_for_waiting_user(reset_timetable_service):
    from datetime import datetime, timedelta

    ts = reset_timetable_service
    fake_msg = _FakeProgressMessage()
    ts.timetable_progress_users[1] = fake_msg

    asyncio.run(send_progress_update(1, current=5, total=10, start_time=datetime.now() - timedelta(seconds=5)))

    assert len(fake_msg.edits) == 1
    assert "5/10" in fake_msg.edits[0]


# --- get_all_groups_timetable: прогресс ожидающим пользователям --------------

def test_force_reload_sends_final_message_to_waiting_user(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service
    fake_msg = _FakeProgressMessage()

    async def _loader(api):
        return SAMPLE

    _patch_force_reload(monkeypatch, ts, _loader)

    asyncio.run(get_all_groups_timetable(force_reload=True, user_id=1, progress_message=fake_msg))

    assert any("успешно загружено" in text for text in fake_msg.edits)
    assert ts.timetable_progress_users == {}  # список очищен в finally


def test_force_reload_sends_error_message_to_waiting_user(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service
    fake_msg = _FakeProgressMessage()

    async def _failing_loader(api):
        raise RuntimeError("сбой загрузки")

    _patch_force_reload(monkeypatch, ts, _failing_loader)

    try:
        asyncio.run(get_all_groups_timetable(force_reload=True, user_id=1, progress_message=fake_msg))
    except RuntimeError:
        pass

    assert any("Не удалось загрузить" in text for text in fake_msg.edits)


# --- preload_timetable / _refresh_timetable_quietly --------------------------

def test_preload_timetable_uses_json_cache(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service
    monkeypatch.setattr(ts.TimetableBonchAPI, "load_from_json", staticmethod(lambda *a: SAMPLE))

    asyncio.run(ts.preload_timetable())

    assert ts.all_groups_timetable_cache == SAMPLE


def test_refresh_timetable_quietly_swallows_errors(monkeypatch, reset_timetable_service):
    ts = reset_timetable_service

    async def _failing_loader(api):
        raise RuntimeError("сбой")

    _patch_force_reload(monkeypatch, ts, _failing_loader)

    # Фоновое обновление не должно пробрасывать исключение наружу.
    asyncio.run(ts._refresh_timetable_quietly())


# --- all_groups_timetable_with_progress --------------------------------------

class _FakeTimetableAPI:
    """Фейк BonchAPI для загрузки расписания без сети."""

    limit = 5

    def __init__(self):
        self.groups_id = {}

    async def get_schet(self):
        pass

    async def get_groups(self):
        self.groups_id = {"1": "ИКВ-11", "2": "ИКВ-12"}

    async def get_timetable(self, session, type_z, group_id):
        return [{"Предмет": f"Предмет {group_id}"}]


class _FakeAiohttpSession:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_all_groups_timetable_with_progress_collects_all_groups(monkeypatch, reset_timetable_service):
    import aiohttp
    import tqdm.asyncio

    ts = reset_timetable_service
    monkeypatch.setattr(aiohttp, "ClientSession", _FakeAiohttpSession)
    monkeypatch.setattr(aiohttp, "TCPConnector", lambda **kw: object())
    monkeypatch.setattr(ts.TimetableBonchAPI, "save_to_json", staticmethod(lambda *a, **kw: None))

    async def _fake_gather(*tasks, **kwargs):
        return await asyncio.gather(*tasks)

    monkeypatch.setattr(tqdm.asyncio.tqdm_asyncio, "gather", _fake_gather)

    result = asyncio.run(ts.all_groups_timetable_with_progress(_FakeTimetableAPI()))

    assert set(result.keys()) == {"ИКВ-11", "ИКВ-12"}
    assert ts.timetable_progress['total'] == 2


# --- A.3: фоновый рефреш + переопределение групп -----------------------------

from satanbonchbot.timetable_service import  _group_redetect_every_ticks


async def _no_sleep(*a, **kw):
    pass


def test_group_redetect_every_ticks_daily_for_six_hour_refresh():
    """Рефреш каждые 6ч, переопределение раз в сутки → раз в 4 тика."""
    assert _group_redetect_every_ticks(refresh_hours=6, redetect_hours=24) == 4


def test_group_redetect_every_ticks_never_more_often_than_refresh():
    """Переопределение не может быть чаще рефреша — минимум 1 тик."""
    assert _group_redetect_every_ticks(refresh_hours=12, redetect_hours=6) == 1


def test_group_redetect_every_ticks_rounds_to_nearest():
    assert _group_redetect_every_ticks(refresh_hours=5, redetect_hours=24) == 5


def test_redetect_user_groups_calls_detect_for_each_authorized_user(monkeypatch, reset_registries):
    from satanbonchbot import lk_client
    from satanbonchbot import login_service
    from satanbonchbot import timetable_service as ts

    lk_client.apis[101] = object()
    lk_client.apis[202] = object()

    detected = []

    async def _fake_detect(user_id, api):
        detected.append(user_id)

    monkeypatch.setattr(login_service, "detect_user_group", _fake_detect)
    monkeypatch.setattr(ts.asyncio, "sleep", _no_sleep)

    asyncio.run(ts._redetect_user_groups())

    assert sorted(detected) == [101, 202]


def test_redetect_user_groups_noop_without_authorized_users(monkeypatch, reset_registries):
    from satanbonchbot import login_service
    from satanbonchbot import timetable_service as ts

    detected = []

    async def _fake_detect(user_id, api):
        detected.append(user_id)

    monkeypatch.setattr(login_service, "detect_user_group", _fake_detect)

    asyncio.run(ts._redetect_user_groups())

    assert detected == []


def test_periodic_refresh_tick_refreshes_every_tick_without_redetect(monkeypatch):
    """Не каждый тик переопределяет группы — на промежуточном тике только рефреш."""
    from satanbonchbot import timetable_service as ts
    calls = {"refresh": 0, "redetect": 0}

    async def _fake_refresh():
        calls["refresh"] += 1

    async def _fake_redetect():
        calls["redetect"] += 1

    monkeypatch.setattr(ts, "_refresh_timetable_quietly", _fake_refresh)
    monkeypatch.setattr(ts, "_redetect_user_groups", _fake_redetect)

    asyncio.run(ts._periodic_refresh_tick(tick=1, redetect_every=4))

    assert calls == {"refresh": 1, "redetect": 0}


def test_periodic_refresh_tick_redetects_groups_on_schedule(monkeypatch):
    """На кратном тике рефреш сопровождается переопределением групп."""
    from satanbonchbot import timetable_service as ts
    calls = {"refresh": 0, "redetect": 0}

    async def _fake_refresh():
        calls["refresh"] += 1

    async def _fake_redetect():
        calls["redetect"] += 1

    monkeypatch.setattr(ts, "_refresh_timetable_quietly", _fake_refresh)
    monkeypatch.setattr(ts, "_redetect_user_groups", _fake_redetect)

    asyncio.run(ts._periodic_refresh_tick(tick=4, redetect_every=4))

    assert calls == {"refresh": 1, "redetect": 1}
