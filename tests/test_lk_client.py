"""Тесты LK-клиента DebuggableBonchAPI: сценарии логина и переавторизации.

Сеть замокана — aiohttp.ClientSession подменяется фейком, не ходящим в ЛК.
Эти тесты — страховка для задачи A.1 (объединение клиентов ЛК): фиксируют
поведение login до рефакторинга.

DebuggableBonchAPI создаёт aiohttp.CookieJar в __init__, поэтому экземпляр
создаётся внутри event loop (через asyncio.run).
"""
import asyncio

import lk_client


class _FakeResponse:
    """Заглушка ответа aiohttp с настраиваемым статусом и телом."""

    def __init__(self, status=200, text="OK"):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._text

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
        self.posts.append(url)
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


def test_login_fails_on_forbidden(monkeypatch):
    cls = type("_ForbiddenSession", (_FakeLoginSession,), {"get_status": 403})
    assert _run_login(cls, monkeypatch) is False


def test_login_fails_on_network_error(monkeypatch):
    class _BrokenSession(_FakeLoginSession):
        def get(self, url, **kwargs):
            raise ConnectionError("сеть недоступна")

    assert _run_login(_BrokenSession, monkeypatch) is False


# --- get_raw_timetable: переавторизация --------------------------------------

class _ExpiredSessionStub(_FakeLoginSession):
    """ClientSession, чей GET всегда отдаёт страницу истёкшей сессии."""

    ERR = "У Вас нет прав доступа. Или необходимо перезагрузить приложение.."

    def get(self, url, **kwargs):
        self.gets.append(url)
        return _FakeResponse(200, type(self).ERR)


def test_get_raw_timetable_returns_err_msg_on_expired_session(monkeypatch):
    """get_raw_timetable отдаёт ERR_MSG как есть — переавторизацию решает вызывающий."""
    _patch_session(monkeypatch, _ExpiredSessionStub)

    async def scenario():
        api = lk_client.DebuggableBonchAPI()
        return await api.get_raw_timetable()

    assert asyncio.run(scenario()).strip() == _ExpiredSessionStub.ERR


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
