"""Тесты lk_messages.get_message_api: переавторизация при протухших cookies.

Сеть замокана: DebuggableBonchAPI подменяется фейком с настраиваемым
результатом login(), реальный ЛК не трогается.
"""
import asyncio

from satanbonchbot import db
from satanbonchbot import lk_client
from satanbonchbot import lk_messages


class _FakeApi:
    """Фейк DebuggableBonchAPI с настраиваемым результатом login()."""

    def __init__(self, cookies=None, login_result=True):
        self.cookies = cookies
        self.login_result = login_result
        self.login_calls = []

    async def login(self, email, password):
        self.login_calls.append((email, password))
        if self.login_result:
            self.cookies = "fresh-cookie"
        return self.login_result


def _add_user(user_id=1, email="user@sut.ru", password="encrypted"):
    db.cursor.execute(
        "INSERT INTO users (user_id, email, password) VALUES (?, ?, ?)",
        (user_id, email, password),
    )
    db.conn.commit()


def test_get_message_api_reauthenticates_when_cookies_missing(monkeypatch, temp_db, reset_registries):
    """Нет cookies у уже созданного API — переавторизуется по данным из БД и
    возвращает тот же инстанс, если логин прошёл успешно."""
    _add_user()
    monkeypatch.setattr(lk_messages, "decrypt_password", lambda p: "plain-pw")
    api = _FakeApi(cookies=None, login_result=True)
    lk_client.apis[1] = api

    result = asyncio.run(lk_messages.get_message_api(1))

    assert result is api
    assert api.login_calls == [("user@sut.ru", "plain-pw")]


def test_get_message_api_returns_none_when_reauth_login_fails(monkeypatch, temp_db, reset_registries):
    """Регрессия: раньше провал login() при переавторизации проверялся по
    cookies-truthiness, а не по возвращаемому bool, так что неудачный релогин
    молча трактовался как успех — поиск/отправка сообщений потом работали
    против неавторизованной сессии без единого сигнала пользователю."""
    _add_user()
    monkeypatch.setattr(lk_messages, "decrypt_password", lambda p: "plain-pw")
    api = _FakeApi(cookies=None, login_result=False)
    lk_client.apis[1] = api

    result = asyncio.run(lk_messages.get_message_api(1))

    assert result is None
