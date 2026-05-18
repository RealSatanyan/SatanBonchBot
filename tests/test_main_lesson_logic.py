"""Тесты логики занятий: интервалы пар, разбор страницы дня, debug-дампы."""
import asyncio
from datetime import datetime, time

import main


def _controller():
    # api и bot не нужны для проверки временной логики.
    return main.LessonController(api=None, bot=None, user_id=1)


async def _construct_api():
    return main.DebuggableBonchAPI()


def _api():
    # CookieJar внутри DebuggableBonchAPI требует event loop при создании.
    return asyncio.run(_construct_api())


# --- is_time_between ---------------------------------------------------------

def test_is_time_between_inside_interval():
    c = _controller()
    assert c.is_time_between(time(9, 0), time(10, 35), time(9, 30)) is True


def test_is_time_between_outside_interval():
    c = _controller()
    assert c.is_time_between(time(9, 0), time(10, 35), time(8, 0)) is False


def test_is_time_between_includes_boundaries():
    c = _controller()
    assert c.is_time_between(time(9, 0), time(10, 35), time(9, 0)) is True
    assert c.is_time_between(time(9, 0), time(10, 35), time(10, 35)) is True


def test_is_time_between_handles_overnight_interval():
    c = _controller()
    assert c.is_time_between(time(23, 0), time(1, 0), time(0, 30)) is True
    assert c.is_time_between(time(23, 0), time(1, 0), time(12, 0)) is False


# --- is_lesson_time ----------------------------------------------------------

def test_is_lesson_time_during_first_pair():
    assert _controller().is_lesson_time(time(9, 30)) is True


def test_is_lesson_time_in_break_between_pairs():
    # Между 2-й (до 12:20) и 3-й (с 13:00) парой.
    assert _controller().is_lesson_time(time(12, 30)) is False


def test_is_lesson_time_before_any_pair():
    assert _controller().is_lesson_time(time(7, 0)) is False


# --- _current_lesson_interval_index ------------------------------------------

def test_current_lesson_interval_index_third_pair():
    assert _controller()._current_lesson_interval_index(time(13, 30)) == 2


def test_current_lesson_interval_index_none_outside_pairs():
    assert _controller()._current_lesson_interval_index(time(7, 0)) is None


# --- _upcoming_lesson_interval_index -----------------------------------------

def test_upcoming_lesson_index_nine_minutes_before_first_pair():
    # 08:51 — за 9 минут до начала 1-й пары (09:00).
    assert _controller()._upcoming_lesson_interval_index(datetime(2026, 5, 18, 8, 51)) == 0


def test_upcoming_lesson_index_seventh_pair():
    # 19:51 — за 9 минут до 7-й пары (20:00).
    assert _controller()._upcoming_lesson_interval_index(datetime(2026, 5, 18, 19, 51)) == 6


def test_upcoming_lesson_index_none_when_too_early():
    # 08:45 — за 15 минут, вне окна напоминания 9..10.
    assert _controller()._upcoming_lesson_interval_index(datetime(2026, 5, 18, 8, 45)) is None


def test_upcoming_lesson_index_custom_window():
    # Окно 14..15 минут: 08:45 — за 15 минут до 1-й пары.
    idx = _controller()._upcoming_lesson_interval_index(
        datetime(2026, 5, 18, 8, 45),
        min_minutes_before_start=14,
        max_minutes_before_start=15,
    )
    assert idx == 0


# --- _parse_today_start_lesson_details ---------------------------------------

def test_parse_today_lesson_details_found(load_fixture):
    html = load_fixture("raspisanie_today.html")
    details = _api()._parse_today_start_lesson_details(html, "18.05.2026", 3)
    assert details is not None
    assert details["subject"] == "Базы данных"
    assert details["room"] == "ауд. 305"
    assert details["teacher"] == "Иванов И.И."
    assert details["rasp"] == "7788"
    assert details["week_param"] == "38"


def test_parse_today_lesson_details_without_start_button(load_fixture):
    # 5-я пара есть в расписании, но кнопки «Начать занятие» ещё нет.
    html = load_fixture("raspisanie_today.html")
    details = _api()._parse_today_start_lesson_details(html, "18.05.2026", 5)
    assert details is not None
    assert details["subject"] == "Программирование"
    assert details["rasp"] is None
    assert details["week_param"] is None


def test_parse_today_lesson_details_absent_pair_returns_none(load_fixture):
    # 7-й пары в расписании на сегодня нет — деталей быть не должно
    # (регрессия бага «уведомление о несуществующей паре»).
    html = load_fixture("raspisanie_today.html")
    assert _api()._parse_today_start_lesson_details(html, "18.05.2026", 7) is None


def test_parse_today_lesson_details_respects_date(load_fixture):
    html = load_fixture("raspisanie_today.html")
    # 1-я пара стоит на 19.05, а не на 18.05.
    assert _api()._parse_today_start_lesson_details(html, "18.05.2026", 1) is None
    assert _api()._parse_today_start_lesson_details(html, "19.05.2026", 1) is not None


def test_parse_today_lesson_details_empty_html():
    assert _api()._parse_today_start_lesson_details("", "18.05.2026", 1) is None


# --- delegаты парсеров в DebuggableBonchAPI ----------------------------------

def test_api_week_parsers_delegate(load_fixture):
    api = _api()
    html = load_fixture("raspisanie_with_lessons.html")
    assert api._get_week_safe(html) == 15
    assert api._get_week_param_safe(html) == 38
    assert api._extract_start_lesson_ids(html) == ("1001", "1002")
    assert api._extract_lesson_ids_fallback(html) == ("1001", "1002")


# --- save_debug_dump / _prune_debug_dumps ------------------------------------

def test_save_debug_dump_disabled_returns_none(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEBUG_DUMPS", "0")
    assert main.save_debug_dump("test", "<html>") is None
    assert not (tmp_path / "debug_dumps").exists()


def test_save_debug_dump_writes_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEBUG_DUMPS", "1")
    path = main.save_debug_dump("no_candidates", "<html>контент</html>")
    assert path is not None
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "<html>контент</html>"
    assert "no_candidates" in path.name


def test_save_debug_dump_prunes_old_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEBUG_DUMPS", "1")
    monkeypatch.setenv("DEBUG_DUMPS_KEEP", "3")
    for i in range(6):
        main.save_debug_dump("dump", f"content {i}")
    remaining = list((tmp_path / "debug_dumps").glob("*.html"))
    assert len(remaining) == 3
