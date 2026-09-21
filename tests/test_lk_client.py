"""Тесты LK-клиента DebuggableBonchAPI: сценарии логина и переавторизации.

Сеть замокана — aiohttp.ClientSession подменяется фейком, не ходящим в ЛК.
Эти тесты — страховка для задачи A.1 (объединение клиентов ЛК): фиксируют
поведение login до рефакторинга.

DebuggableBonchAPI создаёт aiohttp.CookieJar в __init__, поэтому экземпляр
создаётся внутри event loop (через asyncio.run).
"""
import asyncio

import aiohttp
import pytest

from satanbonchbot import lk_client
from satanbonchbot import lk_messages


class _FakeResponse:
    """Заглушка ответа aiohttp с настраиваемым статусом и телом."""

    def __init__(self, status=200, text="OK"):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self, encoding=None, errors="strict"):
        return self._text

    async def read(self):
        return self._text.encode("utf-8", errors="replace")

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _FakeLoginSession:
    """Заглушка ClientSession для login: GET — статус-страницы, POST — AUTH."""

    get_status = 200
    post_status = 200
    auth_text = "1"

    def __init__(self, **kwargs):
        self.gets = []
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self.gets.append(url)
        return _FakeResponse(type(self).get_status, "<html>cabinet</html>")

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakeResponse(type(self).post_status, type(self).auth_text)


def _patch_session(monkeypatch, session_cls):
    monkeypatch.setattr(lk_client.aiohttp, "ClientSession", session_cls)
    monkeypatch.setattr(lk_client.aiohttp, "TCPConnector", lambda **kw: object())


def _run_login(session_cls, monkeypatch, email="user@sut.ru", password="secret"):
    _patch_session(monkeypatch, session_cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.login(email, password)

    return asyncio.run(scenario())


# --- login: успех ------------------------------------------------------------

def test_login_success_when_server_returns_one(monkeypatch):
    assert _run_login(_FakeLoginSession, monkeypatch) is True


def test_login_success_tolerates_whitespace_in_response(monkeypatch):
    """ЛК иногда отдаёт '\\n1' вместо '1' — login обрезает пробелы."""
    cls = type("_WhitespaceSession", (_FakeLoginSession,), {"auth_text": "\n1 "})
    assert _run_login(cls, monkeypatch) is True


# --- login: отказ ------------------------------------------------------------

def test_login_fails_on_wrong_credentials(monkeypatch):
    cls = type("_RejectSession", (_FakeLoginSession,), {"auth_text": "0"})
    assert _run_login(cls, monkeypatch) is False


def test_login_fails_on_wrong_credentials_leaves_cookies_falsy(monkeypatch):
    """Анонимные GET на CABINET/CABINET?login=no уже кладут PHPSESSION в
    cookie_jar ДО отправки кредов. Если после этого _refresh_cookies_view()
    вызывается и на неуспешном пути логина, api.cookies становится truthy,
    хотя авторизация провалилась — вызывающий код, который смотрит на
    cookies вместо bool от login() (lk_messages.get_message_api), решит,
    что сессия рабочая."""
    cls = type("_RejectSession", (_FakeLoginSession,), {"auth_text": "0"})
    _patch_session(monkeypatch, cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        ok = await api.login("user@sut.ru", "wrong-password")
        return ok, api.cookies

    ok, cookies = asyncio.run(scenario())

    assert ok is False
    assert not cookies


def test_login_fails_on_forbidden(monkeypatch):
    cls = type("_ForbiddenSession", (_FakeLoginSession,), {"get_status": 403})
    assert _run_login(cls, monkeypatch) is False


def test_login_fails_on_network_error(monkeypatch):
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)

    class _BrokenSession(_FakeLoginSession):
        def get(self, url, **kwargs):
            raise ConnectionError("сеть недоступна")

    assert _run_login(_BrokenSession, monkeypatch) is False


# --- login: креды идут в теле POST, не в query-строке ------------------------

def test_login_sends_credentials_in_post_body(monkeypatch):
    """AUTH — POST с телом {users, parole}, как реальный jQuery $.post со
    страницы логина (js_new/ajax.js: putLoginTo), а не query-строка.

    POST с кредами в URL и пустым телом отличим от AJAX-запроса браузера и
    ловит 403 от WAF lk.sut.ru — даже с валидными данными и рабочим прокси.
    Заодно спецсимволы (`&`/`#`/пробел) не бьются вручную quote() в URL —
    aiohttp сам корректно кодирует form-тело.
    """
    captured = []

    class _CapturingSession(_FakeLoginSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            captured.append(self)

    _patch_session(monkeypatch, _CapturingSession)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.login("user+a@sut.ru", "p&ss#1 x")

    asyncio.run(scenario())

    auth_url, auth_kwargs = next(entry for s in captured for entry in s.posts)
    assert "users=" not in auth_url
    assert "parole=" not in auth_url
    assert auth_kwargs["data"] == {"users": "user+a@sut.ru", "parole": "p&ss#1 x"}
    assert auth_kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"


# --- get_raw_timetable: переавторизация --------------------------------------

class _ExpiredSessionStub(_FakeLoginSession):
    """ClientSession, чей GET всегда отдаёт страницу истёкшей сессии."""

    ERR = "У Вас нет прав доступа. Или необходимо перезагрузить приложение.."

    def get(self, url, **kwargs):
        self.gets.append(url)
        return _FakeResponse(200, type(self).ERR)


class _LoginNoRedirectStub(_FakeLoginSession):
    """ClientSession, чей GET всегда отдаёт редирект на login=no."""

    def get(self, url, **kwargs):
        self.gets.append(url)
        return _FakeResponse(200, "<html>index.php?login=no</html>")


def test_get_raw_timetable_raises_on_expired_session(monkeypatch):
    """get_raw_timetable сам бросает ValueError на ERR_MSG — единая точка
    обнаружения истёкшей сессии для ВСЕХ вызывающих (get_timetable,
    get_current_lesson_details, click_start_lesson...), а не только для
    click_start_lesson, который раньше проверял это отдельно."""
    _patch_session(monkeypatch, _ExpiredSessionStub)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_raw_timetable()

    with pytest.raises(ValueError, match="Session expired"):
        asyncio.run(scenario())


def test_get_raw_timetable_raises_on_login_no_redirect(monkeypatch):
    """Второй сигнал истёкшей сессии (помимо ERR_MSG) — редирект на login=no.
    Раньше это проверял только click_start_lesson; get_current_lesson_details/
    get_upcoming_start_lesson_details не находили <table> в этом HTML и тихо
    отдавали None — неотличимо от «пары сегодня нет», переавторизация никогда
    не запускалась."""
    _patch_session(monkeypatch, _LoginNoRedirectStub)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_raw_timetable()

    with pytest.raises(ValueError, match="login=no"):
        asyncio.run(scenario())


# --- get_timetable(week_offset=...): следующая/предыдущая неделя ------------

def test_get_timetable_with_offset_uses_current_plus_offset_not_first_week(monkeypatch):
    """Регресс: базовый BonchAPI.get_timetable считает
    desired_week = current_week + week_offset + self._first_week. С реальными
    данными (current_week=1, _first_week=23) это уводило «следующую неделю»
    на week=25 — несуществующую неделю почти на год вперёд, вместо
    настоящей next-недели week=2 (проверено вручную на живом расписании:
    week=2 у сайта — это буквально текст «Следующая» на странице week=1).
    Наше переопределение считает просто current_week + week_offset, как и
    сам сайт в своей пагинации.
    """
    requested_weeks = []

    async def fake_get_raw_timetable(self, week_number=False):
        requested_weeks.append(week_number)
        return f"html-for-week-{week_number}"

    async def fake_get_week(html):
        return 1  # «текущая неделя» с week_number=False парсится как 1

    async def fake_get_my_lessons(html):
        return [html]

    monkeypatch.setattr(lk_client.DebuggableBonchAPI, "get_raw_timetable", fake_get_raw_timetable)
    monkeypatch.setattr(lk_client.parser, "get_week", fake_get_week)
    monkeypatch.setattr(lk_client.parser, "get_my_lessons", fake_get_my_lessons)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        # Если бы _first_week использовался в формуле (как в базовом классе),
        # результат увело бы на week=1+1+23=25 вместо правильного week=2.
        api._first_week = 23
        return await api.get_timetable(week_offset=1)

    result = asyncio.run(scenario())

    assert requested_weeks == [False, 2]
    assert result == ["html-for-week-2"]


def test_get_timetable_without_offset_uses_get_my_lessons_directly(monkeypatch):
    """week_offset=0/False — как и раньше, без пересчёта недели."""
    async def fake_get_raw_timetable(self, week_number=False):
        return f"html-for-week-{week_number}"

    async def fake_get_my_lessons(html):
        return [html]

    monkeypatch.setattr(lk_client.DebuggableBonchAPI, "get_raw_timetable", fake_get_raw_timetable)
    monkeypatch.setattr(lk_client.parser, "get_my_lessons", fake_get_my_lessons)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_timetable(week_offset=0)

    result = asyncio.run(scenario())

    assert result == ["html-for-week-False"]


def test_click_start_lesson_raises_when_session_expired(monkeypatch):
    """click_start_lesson на ERR_MSG бросает ValueError — сигнал переавторизоваться."""
    _patch_session(monkeypatch, _ExpiredSessionStub)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.click_start_lesson(user_id=1)

    try:
        asyncio.run(scenario())
        raised = False
    except ValueError:
        raised = True
    assert raised is True


# --- сообщения ЛК (перенесены из public_timetable в A.1.2) -------------------

class _FakeMessageSession:
    """ClientSession для методов сообщений: GET кабинета — прогрев, остальное — page_text."""

    page_text = "<html></html>"
    post_text = "{}"

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        if url.rstrip("/").endswith("cabinet"):
            return _FakeResponse(200, "<html>cabinet</html>")
        return _FakeResponse(200, type(self).page_text)

    def post(self, url, **kwargs):
        return _FakeResponse(200, type(self).post_text)


def test_get_messages_page_returns_empty_on_php_error(monkeypatch):
    cls = type("_PhpErrorSession", (_FakeMessageSession,), {"page_text": "ERRNO: 1 Undefined index"})
    _patch_session(monkeypatch, cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_messages_page(1)

    assert asyncio.run(scenario()) == {'messages': [], 'total_pages': 1}


def test_get_messages_page_returns_empty_on_network_error(monkeypatch):
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)

    class _BrokenSession(_FakeMessageSession):
        def get(self, url, **kwargs):
            raise ConnectionError("сеть недоступна")

    _patch_session(monkeypatch, _BrokenSession)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_messages_page(1)

    assert asyncio.run(scenario()) == {'messages': [], 'total_pages': 1}


def test_get_message_parses_json_and_unescapes_html(monkeypatch):
    cls = type(
        "_JsonMessageSession", (_FakeMessageSession,),
        {"post_text": '{"name": "&lt;Тема&gt;", "annotation": "&amp;текст"}'},
    )
    _patch_session(monkeypatch, cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_message("123")

    result = asyncio.run(scenario())
    assert result["name"] == "<Тема>"
    assert result["annotation"] == "&текст"


# --- _lk_fetch: ретраи и устойчивое чтение (задача A.1) ----------------------

class _FlakyResponse:
    """Ответ aiohttp для _lk_fetch: статус + сырое тело (bytes), читается read()."""

    def __init__(self, status=200, body=b"OK"):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self):
        return self._body

    async def text(self, encoding=None, errors="strict"):
        return self._body.decode(encoding or "utf-8", errors=errors)


class _ScriptedSession:
    """session, чьи GET/POST отыгрывают заданный сценарий шагов.

    Шаг — исключение (поднимается) либо _FlakyResponse (возвращается).
    """

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self, url, **kwargs):
        self.calls += 1
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def get(self, url, **kwargs):
        return self._next(url, **kwargs)

    def post(self, url, **kwargs):
        return self._next(url, **kwargs)


def _scripted_session_cls(steps, sink):
    """Класс ClientSession-заглушки: каждый инстанс отыгрывает `steps`, кладётся в `sink`."""

    class _Cls(_ScriptedSession):
        def __init__(self, **kwargs):
            super().__init__(list(steps))
            sink.append(self)

    return _Cls


def test_lk_fetch_retries_network_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    session = _ScriptedSession([ConnectionError("blip"), _FlakyResponse(200, b"OK")])

    status, text = asyncio.run(lk_client._lk_fetch(session, "GET", "https://lk"))

    assert (status, text) == (200, "OK")
    assert session.calls == 2  # одна неудача + успешный повтор


def test_lk_fetch_retries_server_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    session = _ScriptedSession([
        _FlakyResponse(503, b""), _FlakyResponse(503, b""), _FlakyResponse(200, b"OK"),
    ])

    status, text = asyncio.run(lk_client._lk_fetch(session, "GET", "https://lk"))

    assert status == 200
    assert session.calls == 3


def test_lk_fetch_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    session = _ScriptedSession([ConnectionError("x")] * 3)

    with pytest.raises(OSError):
        asyncio.run(lk_client._lk_fetch(session, "GET", "https://lk"))
    assert session.calls == 3  # повторяли, но все попытки провалились


def test_lk_fetch_returns_last_5xx_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    session = _ScriptedSession([_FlakyResponse(503, b"err")] * 3)

    status, _ = asyncio.run(lk_client._lk_fetch(session, "GET", "https://lk"))

    assert status == 503  # после исчерпания повторов отдаём последний ответ


def test_lk_fetch_decodes_broken_body_without_crash(monkeypatch):
    """Битое тело (не UTF-8) не роняет запрос в UnicodeDecodeError."""
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    session = _ScriptedSession([_FlakyResponse(200, b"\xff\xfe broken")])

    status, text = asyncio.run(lk_client._lk_fetch(session, "GET", "https://lk"))

    assert status == 200
    assert isinstance(text, str)


def test_lk_fetch_skips_body_when_read_body_false():
    """read_body=False — тело НЕ вычитывается (этапам логина нужен только статус).

    Регрессия A.1: страница ЛК после входа (?login=yes) большая/отдаётся
    медленно, response.read() на ней висел до таймаута — а тело там не нужно.
    """
    class _ExplodingReadResponse(_FlakyResponse):
        async def read(self):
            raise AssertionError("read() не должен вызываться при read_body=False")

    session = _ScriptedSession([_ExplodingReadResponse(200, b"unused")])

    status, text = asyncio.run(
        lk_client._lk_fetch(session, "GET", "https://lk", read_body=False)
    )

    assert status == 200
    assert text == ""


# --- login: устойчивость к сетевому сбою (частичный сбой) --------------------

def test_login_succeeds_after_transient_network_error(monkeypatch):
    """Разовый сетевой сбой на первом запросе входа не валит логин — он повторяется."""
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)

    class _FlakyLoginSession(_FakeLoginSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._first_get = True

        def get(self, url, **kwargs):
            if self._first_get:
                self._first_get = False
                raise ConnectionError("разовый сбой сети")
            self.gets.append(url)
            return _FakeResponse(200, "<html>cabinet</html>")

    assert _run_login(_FlakyLoginSession, monkeypatch) is True


# --- сообщения ЛК: ретраи при сетевом сбое (A.1) -----------------------------

def _connector_error(msg="отказ соединения"):
    """Строит aiohttp.ClientConnectorError — «доотправочный» сбой (запрос не ушёл)."""
    from aiohttp.client_reqrep import ConnectionKey

    key = ConnectionKey(host="lk.sut.ru", port=443, is_ssl=True, ssl=None,
                        proxy=None, proxy_auth=None, proxy_headers_hash=None)
    return aiohttp.ClientConnectorError(key, OSError(msg))


def test_get_messages_page_retries_transient_error_no_false_empty(monkeypatch):
    """Разовый сетевой сбой при опросе сообщений повторяется — ложного «нет сообщений» нет."""
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    sink = []
    cls = _scripted_session_cls(
        [ConnectionError("blip"), _FlakyResponse(200, b"<html></html>")], sink)
    _patch_session(monkeypatch, cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_messages_page(2)

    asyncio.run(scenario())
    assert sink[0].calls == 2  # сбой + успешный повтор, а не «пустой» результат из except


def test_get_message_retries_transient_error(monkeypatch):
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    sink = []
    cls = _scripted_session_cls(
        [ConnectionError("blip"),
         _FlakyResponse(200, '{"name": "Тема"}'.encode("utf-8"))], sink)
    _patch_session(monkeypatch, cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_message("123")

    result = asyncio.run(scenario())
    assert sink[0].calls == 2
    assert result["name"] == "Тема"


def test_lk_search_recipients_retries_transient_error(monkeypatch):
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    sink = []
    cls = _scripted_session_cls(
        [ConnectionError("blip"), _FlakyResponse(200, b"<html></html>")], sink)
    _patch_session(monkeypatch, cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await lk_messages.lk_search_recipients(api, "Иванов")

    asyncio.run(scenario())
    assert sink[0].calls == 2


def test_lk_send_message_retries_pre_send_failure(monkeypatch):
    """«Доотправочный» сбой (соединение не установлено) безопасно повторяется."""
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    sink = []
    cls = _scripted_session_cls([_connector_error(), _FlakyResponse(200, b"")], sink)
    _patch_session(monkeypatch, cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await lk_messages.lk_send_message(api, 42, "Тема", "Текст")

    assert asyncio.run(scenario()) is True
    assert sink[0].calls == 2


def test_lk_send_message_does_not_retry_ambiguous_failure(monkeypatch):
    """Неоднозначный сбой после отправки НЕ ретраится — иначе создаётся дубль сообщения."""
    monkeypatch.setattr(lk_client, "_LK_RETRY_BACKOFF_SEC", 0)
    sink = []
    cls = _scripted_session_cls(
        [ConnectionError("оборвалось после POST"), _FlakyResponse(200, b"")], sink)
    _patch_session(monkeypatch, cls)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await lk_messages.lk_send_message(api, 42, "Тема", "Текст")

    assert asyncio.run(scenario()) is False
    assert sink[0].calls == 1  # ровно одна попытка — дубль не создаётся
