"""Тесты счётчика сообщений и тёплого кэша списка /messages."""
import main


# --- format_message_count ----------------------------------------------------

def test_count_exact_when_all_loaded():
    assert main.format_message_count(15, total_pages=1, per_page=20, has_more=False) == "15"


def test_count_approximate_when_more_pages():
    # 35 страниц по ~20 — показываем оценку, а не «20+»
    assert main.format_message_count(20, total_pages=35, per_page=20, has_more=True) == "≈700"


def test_count_falls_back_to_plus_without_page_info():
    assert main.format_message_count(20, total_pages=1, per_page=0, has_more=True) == "20+"
    assert main.format_message_count(20, total_pages=0, per_page=20, has_more=True) == "20+"


# --- _messages_cache_fresh ---------------------------------------------------

def test_cache_fresh_within_ttl():
    state = {"messages": [{"id": 1}], "fetched_at": 1000.0}
    assert main._messages_cache_fresh(state, now_ts=1100.0, ttl_sec=300) is True


def test_cache_stale_past_ttl():
    state = {"messages": [{"id": 1}], "fetched_at": 1000.0}
    assert main._messages_cache_fresh(state, now_ts=1400.0, ttl_sec=300) is False


def test_cache_not_fresh_without_timestamp():
    state = {"messages": [{"id": 1}], "fetched_at": None}
    assert main._messages_cache_fresh(state, now_ts=1100.0, ttl_sec=300) is False


def test_cache_not_fresh_when_empty_or_missing():
    assert main._messages_cache_fresh(None, now_ts=1100.0, ttl_sec=300) is False
    assert main._messages_cache_fresh({"messages": [], "fetched_at": 1100.0}, 1100.0, 300) is False
