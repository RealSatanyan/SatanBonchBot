"""Тесты flow «✏️ Написать» с вложением файла (задача B.2).

Сеть замокана: get_message_api / lk_upload_file / lk_send_message подменяются
фейками. Telegram-загрузка файла замокана фейковым bot.download.
"""
import asyncio
from types import SimpleNamespace

import handlers.messages as messages_mod
from handlers.messages import (
    fsm_write_text,
    fsm_write_file_attach,
    fsm_write_file_invalid,
    cb_write_nofile,
)
from states import UIStates
from lk_client import LK_MAX_FILE_SIZE_MB


class FakeBot:
    """Фейк aiogram Bot: download создаёт файл по destination."""

    def __init__(self):
        self.downloads = []

    async def download(self, file_obj, destination):
        self.downloads.append(destination)
        with open(destination, "wb") as f:
            f.write(b"file-content")


class FakeMessage:
    """Фейк aiogram Message с поддержкой document/photo."""

    def __init__(self, text=None, document=None, photo=None, user_id=1):
        self.text = text
        self.document = document
        self.photo = photo
        self.from_user = SimpleNamespace(id=user_id)
        self.bot = FakeBot()
        self.answers = []
        self.edits = []
        self.replies = []

    async def answer(self, text, **kwargs):
        self.answers.append({"text": text, **kwargs})
        reply = FakeMessage(user_id=self.from_user.id)
        self.replies.append(reply)
        return reply

    async def edit_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})

    async def edit_reply_markup(self, **kwargs):
        pass

    async def delete(self):
        pass


class FakeCallbackQuery:
    def __init__(self, user_id=1):
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage(user_id=user_id)
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


class FakeState:
    """Фейк aiogram FSMContext."""

    def __init__(self, data=None, state=None):
        self._data = dict(data or {})
        self._state = state

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def set_state(self, value):
        self._state = value

    async def clear(self):
        self._data = {}
        self._state = None


def _doc(file_size, file_name="doc.pdf"):
    return SimpleNamespace(file_size=file_size, file_name=file_name, file_id="fid")


def _patch_lk(monkeypatch, *, upload_idinfo=0, send_ok=True):
    """Замокать сетевые функции ЛК; вернуть список вызовов lk_send_message."""
    send_calls = []

    async def fake_get_message_api(user_id):
        return object()

    async def fake_upload(message_api, filename, id=0):
        return upload_idinfo

    async def fake_send(**kwargs):
        send_calls.append(kwargs)
        return send_ok

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_message_api)
    monkeypatch.setattr(messages_mod, "lk_upload_file", fake_upload)
    monkeypatch.setattr(messages_mod, "lk_send_message", fake_send)
    return send_calls


# --- fsm_write_text: переход к шагу прикрепления файла -----------------------

def test_write_text_transitions_to_write_file(reset_message_states):
    state = FakeState(data={"recipient_id": 5, "title": "Тема"})
    msg = FakeMessage(text="привет", user_id=1)

    asyncio.run(fsm_write_text(msg, state))

    assert state._state == UIStates.write_file
    assert state._data["text"] == "привет"
    assert any("без файла" in a["text"] for a in msg.answers)


def test_write_text_rejects_empty(reset_message_states):
    state = FakeState(data={"recipient_id": 5})
    msg = FakeMessage(text="   ", user_id=1)

    asyncio.run(fsm_write_text(msg, state))

    assert state._state != UIStates.write_file
    assert any("пустой" in a["text"] for a in msg.answers)


# --- cb_write_nofile: отправка без вложения ----------------------------------

def test_nofile_sends_without_attachment(monkeypatch, reset_message_states):
    send_calls = _patch_lk(monkeypatch, send_ok=True)
    state = FakeState(data={"recipient_id": 42, "title": "T", "text": "тело"})
    cb = FakeCallbackQuery(user_id=1)

    asyncio.run(cb_write_nofile(cb, state))

    assert len(send_calls) == 1
    assert send_calls[0]["idinfo"] == 0
    assert send_calls[0]["recipient_id"] == 42


# --- fsm_write_file_attach: лимит размера ------------------------------------

def test_file_attach_rejects_oversized(monkeypatch, reset_message_states):
    send_calls = _patch_lk(monkeypatch)
    too_big = (LK_MAX_FILE_SIZE_MB + 1) * 1024 * 1024
    msg = FakeMessage(document=_doc(too_big), user_id=1)
    state = FakeState(data={"recipient_id": 1, "text": "t"}, state=UIStates.write_file)

    asyncio.run(fsm_write_file_attach(msg, state))

    assert send_calls == []  # сообщение не отправлено
    assert any("больше" in a["text"] for a in msg.answers)


# --- fsm_write_file_attach: успешная загрузка --------------------------------

def test_file_attach_uploads_and_sends_with_idinfo(monkeypatch, reset_message_states):
    send_calls = _patch_lk(monkeypatch, upload_idinfo=777, send_ok=True)
    msg = FakeMessage(document=_doc(1000, "report.pdf"), user_id=1)
    state = FakeState(data={"recipient_id": 9, "title": "T", "text": "тело"}, state=UIStates.write_file)

    asyncio.run(fsm_write_file_attach(msg, state))

    assert len(send_calls) == 1
    assert send_calls[0]["idinfo"] == 777
    assert msg.bot.downloads  # файл скачивался из Telegram


def test_file_attach_handles_upload_failure(monkeypatch, reset_message_states):
    send_calls = _patch_lk(monkeypatch, upload_idinfo=0)
    msg = FakeMessage(document=_doc(1000), user_id=1)
    state = FakeState(data={"recipient_id": 9, "text": "тело"}, state=UIStates.write_file)

    asyncio.run(fsm_write_file_attach(msg, state))

    assert send_calls == []  # при сбое загрузки сообщение не отправляется
    status_msg = msg.replies[0]
    assert any("Не удалось загрузить файл" in e["text"] for e in status_msg.edits)


# --- fsm_write_file_invalid: пришёл не файл ----------------------------------

def test_write_file_invalid_reprompts(reset_message_states):
    msg = FakeMessage(text="просто текст", user_id=1)
    state = FakeState(state=UIStates.write_file)

    asyncio.run(fsm_write_file_invalid(msg, state))

    assert any("не файл" in a["text"].lower() for a in msg.answers)
