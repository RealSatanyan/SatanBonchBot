"""Тесты обработчиков сообщений ЛК (handlers/messages/).

Flow «✏️ Написать» с вложением файла (B.2), чтение входящих (C.2 №1–2),
парсинг аргументов /send_lk (C.2 №3). Сеть замокана: get_message_api /
lk_upload_file / lk_send_message / lk_search_recipients подменяются фейками.
"""
import asyncio
from types import SimpleNamespace

from satanbonchbot.handlers.messages import compose as messages_mod
from satanbonchbot.handlers.messages import inbox as inbox_mod
from satanbonchbot.handlers.messages.compose import  (
    cmd_send_lk,
    handle_lk_send_callback,
    cb_msg_write,
    fsm_write_recipient,
    fsm_write_title,
    cb_notitle,
    cb_write_pick,
    fsm_write_text,
    fsm_write_file_attach,
    fsm_write_file_invalid,
    cb_write_nofile,
)
from satanbonchbot.handlers.messages.inbox import  cmd_messages, handle_message_callback
from satanbonchbot.states import  UIStates
from satanbonchbot.lk_messages import  LK_MAX_FILE_SIZE_MB


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
        self.chat = SimpleNamespace(id=user_id)
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
    def __init__(self, user_id=1, data=""):
        self.data = data
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


# --- C.2 №1: cmd_messages — тёплый кэш ---------------------------------------

def test_cmd_messages_warm_cache_skips_lk_request(monkeypatch, reset_message_states):
    """Свежий кэш — список показывается без перезапроса первой страницы из ЛК."""
    from satanbonchbot import messages_service
    from satanbonchbot.messages_service import  _build_message_state

    shown, api_calls = [], []

    async def fake_show(user_id, chat_id, index):
        shown.append((user_id, index))

    async def fake_get_api(user_id):
        api_calls.append(user_id)
        return None

    monkeypatch.setattr(inbox_mod, "show_message_list", fake_show)
    monkeypatch.setattr(inbox_mod, "get_message_api", fake_get_api)
    messages_service.message_states[1] = _build_message_state(
        object(), {"messages": [{"id": "m1"}], "total_pages": 1}
    )

    asyncio.run(cmd_messages(FakeMessage(user_id=1)))

    assert shown == [(1, 0)]
    assert api_calls == []  # тёплый кэш — в ЛК не ходили


def test_cmd_messages_stale_cache_refetches_first_page(monkeypatch, reset_message_states):
    """Устаревший кэш — первая страница перезапрашивается из ЛК."""
    from satanbonchbot import messages_service
    from satanbonchbot.messages_service import  _build_message_state

    pages = []

    class FakeApi:
        async def get_messages_page(self, page):
            pages.append(page)
            return {"messages": [{"id": "m1"}], "total_pages": 1}

    async def fake_show(user_id, chat_id, index):
        pass

    async def fake_get_api(user_id):
        return FakeApi()

    monkeypatch.setattr(inbox_mod, "show_message_list", fake_show)
    monkeypatch.setattr(inbox_mod, "get_message_api", fake_get_api)
    stale = _build_message_state(object(), {"messages": [{"id": "old"}], "total_pages": 1})
    stale["fetched_at"] = None  # помечен устаревшим
    messages_service.message_states[1] = stale

    asyncio.run(cmd_messages(FakeMessage(user_id=1)))

    assert pages == [1]  # перезапросили именно первую страницу


# --- C.2 №2: handle_message_callback — навигация -----------------------------

def test_message_navigation_prev_moves_back(monkeypatch, reset_message_states):
    from satanbonchbot import messages_service

    shown = []

    async def fake_show(user_id, chat_id, index):
        shown.append(index)

    monkeypatch.setattr(inbox_mod, "show_message_list", fake_show)
    messages_service.message_states[1] = {
        "api": object(), "messages": [{"id": "m1"}, {"id": "m2"}],
        "total_pages": 1, "loaded_pages": 1, "current_index": 1,
    }

    asyncio.run(handle_message_callback(FakeCallbackQuery(user_id=1, data="msg_prev_1")))

    assert shown == [0]
    assert messages_service.message_states[1]["current_index"] == 0


def test_message_navigation_next_lazy_loads_next_page(monkeypatch, reset_message_states):
    """Дошли до конца загруженного — следующая страница подгружается лениво."""
    from satanbonchbot import messages_service

    shown = []

    class FakeApi:
        async def get_messages_page(self, page):
            return {"messages": [{"id": "m2"}], "total_pages": 2}

    async def fake_show(user_id, chat_id, index):
        shown.append(index)

    monkeypatch.setattr(inbox_mod, "show_message_list", fake_show)
    messages_service.message_states[1] = {
        "api": FakeApi(), "messages": [{"id": "m1"}],
        "total_pages": 2, "loaded_pages": 1, "current_index": 0,
    }

    asyncio.run(handle_message_callback(FakeCallbackQuery(user_id=1, data="msg_next_0")))

    state = messages_service.message_states[1]
    assert state["loaded_pages"] == 2
    assert len(state["messages"]) == 2
    assert shown == [1]


def test_message_navigation_next_page_fetch_fails_does_not_advance_loaded_pages(monkeypatch, reset_message_states):
    """Регрессия: если подгрузка следующей страницы не удалась/вернула пусто
    (сетевой сбой, ЛК отдал ERRNO — lk_client.get_messages_page это глотает и
    возвращает {'messages': []}), loaded_pages раньше всё равно увеличивался.
    Страница молча считалась «загруженной» с нулём сообщений — пользователь
    терял её навсегда (до /messages заново), без единой ошибки на экране."""
    from satanbonchbot import messages_service

    class FailingApi:
        async def get_messages_page(self, page):
            return {"messages": [], "total_pages": 2}

    async def fake_show(user_id, chat_id, index):
        pass

    monkeypatch.setattr(inbox_mod, "show_message_list", fake_show)
    messages_service.message_states[1] = {
        "api": FailingApi(), "messages": [{"id": "m1"}],
        "total_pages": 2, "loaded_pages": 1, "current_index": 0,
    }

    asyncio.run(handle_message_callback(FakeCallbackQuery(user_id=1, data="msg_next_0")))

    state = messages_service.message_states[1]
    assert state["loaded_pages"] == 1
    assert len(state["messages"]) == 1


# --- C.2 №3: cmd_send_lk — парсинг аргументов --------------------------------

def test_send_lk_numeric_id_sends_directly(monkeypatch):
    """Первое слово — число → отправка по ID без поиска получателя."""
    send_calls = []

    async def fake_get_api(uid):
        return object()

    async def fake_send(**kwargs):
        send_calls.append(kwargs)
        return True

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_api)
    monkeypatch.setattr(messages_mod, "lk_send_message", fake_send)

    asyncio.run(cmd_send_lk(FakeMessage(text="/send_lk 113714 Привет бот", user_id=1)))

    assert len(send_calls) == 1
    assert send_calls[0]["recipient_id"] == 113714
    assert send_calls[0]["message_text"] == "Привет бот"


def test_send_lk_name_with_initials_searches_recipient(monkeypatch):
    """«Фамилия И.О. + текст» → граница ФИО/текста после инициалов, идёт поиск."""
    search_calls = []

    async def fake_get_api(uid):
        return object()

    async def fake_search(api, query):
        search_calls.append(query)
        return []

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_api)
    monkeypatch.setattr(messages_mod, "lk_search_recipients", fake_search)

    asyncio.run(cmd_send_lk(FakeMessage(text="/send_lk Платонов Д.И. Реально работает?", user_id=1)))

    assert search_calls == ["Платонов Д.И."]


def test_send_lk_without_args_shows_usage():
    msg = FakeMessage(text="/send_lk", user_id=1)

    asyncio.run(cmd_send_lk(msg))

    assert any("Использование" in a["text"] for a in msg.answers)


# --- C.2 №2 (доп.): открытие/обновление/возврат в handle_message_callback ----

def test_message_open_renders_message(monkeypatch, reset_message_states):
    from satanbonchbot import messages_service

    class FakeApi:
        async def get_message(self, message_id):
            return {"name": "Тема письма", "annotation": "<p>тело письма</p>"}

    async def fake_get_api(user_id):
        return FakeApi()

    monkeypatch.setattr(inbox_mod, "get_message_api", fake_get_api)
    messages_service.message_states[1] = {
        "messages": [{"id": "m1", "title": "Тема письма", "sender": "Деканат"}],
    }

    cb = FakeCallbackQuery(user_id=1, data="msg_open_m1")
    asyncio.run(handle_message_callback(cb))

    assert any("Тема письма" in a["text"] for a in cb.message.answers)


def test_message_open_falls_back_when_name_and_annotation_are_empty_strings(monkeypatch, reset_message_states):
    """Регрессия: ЛК может вернуть 'name'/'annotation' как ПУСТУЮ строку, а не
    отсутствующий ключ (например, уведомление только с вложением, без темы и
    текста). dict.get(key, default) не срабатывает, если ключ есть, — раньше
    открытое сообщение показывало '<b></b>' вместо заголовка из списка
    (msg_info['title']) и вместо плейсхолдера 'Нет текста'."""
    from satanbonchbot import messages_service

    class FakeApi:
        async def get_message(self, message_id):
            return {"name": "", "annotation": ""}

    async def fake_get_api(user_id):
        return FakeApi()

    monkeypatch.setattr(inbox_mod, "get_message_api", fake_get_api)
    messages_service.message_states[1] = {
        "messages": [{"id": "m1", "title": "Тема из списка", "sender": "Деканат"}],
    }

    cb = FakeCallbackQuery(user_id=1, data="msg_open_m1")
    asyncio.run(handle_message_callback(cb))

    text = cb.message.answers[0]["text"]
    assert "Тема из списка" in text
    assert "Нет текста" in text


def test_message_open_uses_html_and_escapes_unbalanced_markdown(monkeypatch, reset_message_states):
    """Тело сообщения с висячим `_` и `<` не должно ронять Telegram-парсер.

    Регрессия inbox.py:190 — при parse_mode='Markdown' любой несбалансированный
    `*`/`_`/`[` в annotation/title/sender вызывал Telegram 400 «can't parse
    entities». Рендер обязан использовать HTML и экранировать <,>,& в
    пользовательском контенте; символы Markdown остаются литералами.
    """
    from satanbonchbot import messages_service

    class FakeApi:
        async def get_message(self, message_id):
            return {
                "name": "Практика_№8 ТЭС",
                # `<...>` стрипается tag-cleanup'ом до отправки — это
                # отдельная фича. Здесь проверяем, что выживший контент
                # с `_` и `&` корректно уходит в HTML-режиме.
                "annotation": "Сдать до 25_05; см. A&B",
            }

    async def fake_get_api(user_id):
        return FakeApi()

    monkeypatch.setattr(inbox_mod, "get_message_api", fake_get_api)
    messages_service.message_states[1] = {
        "messages": [{
            "id": "m1",
            "title": "Практика_№8 ТЭС",
            "sender": "Виноградов <ВБ>",
            "files": [{"name": "task_8.pdf", "url": "https://lk.example/file?id=1&t=2"}],
        }],
    }

    cb = FakeCallbackQuery(user_id=1, data="msg_open_m1")
    asyncio.run(handle_message_callback(cb))

    sent = next(a for a in cb.message.answers if "Практика" in a["text"])
    assert sent.get("parse_mode") == "HTML"
    text = sent["text"]
    # < > & в динамических полях экранированы:
    assert "&lt;ВБ&gt;" in text
    assert "A&amp;B" in text
    # Подчёркивание — литерал, не должно открывать entity:
    assert "Практика_№8" in text
    assert "25_05" in text
    # Ссылка на файл — HTML-якорь с экранированным & в URL:
    assert 'href="https://lk.example/file?id=1&amp;t=2"' in text


def test_message_back_to_list_returns(monkeypatch, reset_message_states):
    from satanbonchbot import messages_service

    shown = []

    async def fake_show(user_id, chat_id, index):
        shown.append(index)

    monkeypatch.setattr(inbox_mod, "show_message_list", fake_show)
    messages_service.message_states[1] = {"messages": [{"id": "m1"}], "current_index": 0}

    asyncio.run(handle_message_callback(FakeCallbackQuery(user_id=1, data="msg_back_to_list")))

    assert shown == [0]


def test_message_refresh_reloads_first_page(monkeypatch, reset_message_states):
    from satanbonchbot import messages_service

    class FakeApi:
        async def get_messages_page(self, page):
            return {"messages": [{"id": "m1"}], "total_pages": 1}

    async def fake_get_api(user_id):
        return FakeApi()

    async def fake_show(user_id, chat_id, index):
        pass

    monkeypatch.setattr(inbox_mod, "get_message_api", fake_get_api)
    monkeypatch.setattr(inbox_mod, "show_message_list", fake_show)

    asyncio.run(handle_message_callback(FakeCallbackQuery(user_id=1, data="msg_refresh")))

    assert 1 in messages_service.message_states


def test_cmd_messages_without_api_prompts_login(monkeypatch, reset_message_states):
    async def fake_get_api(user_id):
        return None

    monkeypatch.setattr(inbox_mod, "get_message_api", fake_get_api)
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_messages(msg))

    assert any("login" in a["text"].lower() for a in msg.answers)


def test_cmd_messages_empty_inbox_reports_no_messages(monkeypatch, reset_message_states):
    class FakeApi:
        cookies = {"sid": "x"}

        async def get_messages_page(self, page):
            return {"messages": [], "total_pages": 1}

    async def fake_get_api(user_id):
        return FakeApi()

    monkeypatch.setattr(inbox_mod, "get_message_api", fake_get_api)
    msg = FakeMessage(user_id=1)

    asyncio.run(cmd_messages(msg))

    status = msg.replies[0]
    assert any("нет входящих" in e["text"].lower() for e in status.edits)


# --- C.2 №3 (доп.): cmd_send_lk — ветки поиска и отправки --------------------

def test_send_lk_id_send_failure_reports_error(monkeypatch):
    async def fake_get_api(uid):
        return object()

    async def fake_send(**kwargs):
        return False

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_api)
    monkeypatch.setattr(messages_mod, "lk_send_message", fake_send)
    msg = FakeMessage(text="/send_lk 100 текст", user_id=1)

    asyncio.run(cmd_send_lk(msg))

    status = msg.replies[0]
    assert any("Не удалось отправить" in e["text"] for e in status.edits)


def test_send_lk_single_search_result_sends(monkeypatch):
    async def fake_get_api(uid):
        return object()

    async def fake_search(api, query):
        return [{"id": 55, "label": "Иванов И.И."}]

    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_api)
    monkeypatch.setattr(messages_mod, "lk_search_recipients", fake_search)
    monkeypatch.setattr(messages_mod, "lk_send_message", fake_send)
    msg = FakeMessage(text="/send_lk Иванов И.И. привет", user_id=1)

    asyncio.run(cmd_send_lk(msg))

    assert sent and sent[0]["recipient_id"] == 55


def test_send_lk_multiple_results_offers_choice(monkeypatch):
    from satanbonchbot import messages_service
    messages_service.pending_lk_messages.clear()

    async def fake_get_api(uid):
        return object()

    async def fake_search(api, query):
        return [{"id": 1, "label": "Иванов И.И."}, {"id": 2, "label": "Иванов И.П."}]

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_api)
    monkeypatch.setattr(messages_mod, "lk_search_recipients", fake_search)
    msg = FakeMessage(text="/send_lk Иванов привет всем", user_id=1)

    asyncio.run(cmd_send_lk(msg))

    assert messages_service.pending_lk_messages  # сообщения отложены до выбора
    messages_service.pending_lk_messages.clear()


def test_lk_send_callback_sends_pending_message(monkeypatch):
    from satanbonchbot import messages_service
    # Ключ (user_id, batch_id, recipient_id): batch_id отвязывает pending-запись
    # от конкретного поиска (см. test_lk_send_overlapping_recipient_* ниже) —
    # значение произвольное, тест сам конструирует и запись, и callback_data.
    messages_service.pending_lk_messages[(1, 0, 77)] = {"text": "тело", "title": "", "label": "Х"}

    async def fake_get_api(uid):
        return object()

    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_api)
    monkeypatch.setattr(messages_mod, "lk_send_message", fake_send)

    asyncio.run(handle_lk_send_callback(FakeCallbackQuery(user_id=1, data="lk_send_0_77")))

    assert sent and sent[0]["recipient_id"] == 77


def test_lk_send_overlapping_recipient_across_two_searches_does_not_cross_contaminate(monkeypatch):
    """Регрессия: pending_lk_messages был ключом (user_id, recipient_id) без
    привязки к конкретному поиску. Второй /send_lk с тем же получателем
    (обычный случай — один и тот же человек попадает в оба списка результатов)
    затирал текст первого, ещё не выбранного, сообщения. Нажатие кнопки на
    ПЕРВОЙ (всё ещё видимой пользователю) клавиатуре отправляло текст ВТОРОГО
    сообщения — не то, что пользователь выбирал."""
    from satanbonchbot import messages_service
    messages_service.pending_lk_messages.clear()

    async def fake_get_api(uid):
        return object()

    async def fake_search(api, query):
        return [{"id": 1, "label": "Иванов И.И."}, {"id": 2, "label": "Иванов И.П."}]

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_api)
    monkeypatch.setattr(messages_mod, "lk_search_recipients", fake_search)

    msg1 = FakeMessage(text="/send_lk Иванов Текст-раз", user_id=1)
    asyncio.run(cmd_send_lk(msg1))
    first_keyboard = msg1.replies[0].edits[-1]["reply_markup"]
    first_callback_data = first_keyboard.inline_keyboard[0][0].callback_data

    msg2 = FakeMessage(text="/send_lk Иванов Текст-два", user_id=1)
    asyncio.run(cmd_send_lk(msg2))

    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(messages_mod, "lk_send_message", fake_send)

    # Пользователь жмёт кнопку с ПЕРВОЙ, ещё не устаревшей на экране клавиатуры.
    asyncio.run(handle_lk_send_callback(FakeCallbackQuery(user_id=1, data=first_callback_data)))

    assert sent and sent[0]["message_text"] == "Текст-раз"
    messages_service.pending_lk_messages.clear()


# --- C.2: остальные шаги диалога «Написать» ----------------------------------

def test_cb_msg_write_unregistered_prompts_login(temp_db):
    cb = FakeCallbackQuery(user_id=1)

    asyncio.run(cb_msg_write(cb, FakeState()))

    assert any("войди в ЛК" in a["text"] for a in cb.message.answers)


def test_cb_msg_write_registered_starts_recipient_step(temp_db):
    temp_db.execute("INSERT INTO users (user_id, email, password) VALUES (1, 'e', 'p')")
    temp_db.commit()
    state = FakeState()

    asyncio.run(cb_msg_write(FakeCallbackQuery(user_id=1), state))

    assert state._state == UIStates.write_recipient


def test_fsm_write_recipient_numeric_id_goes_to_title(monkeypatch):
    async def fake_get_api(uid):
        return object()

    monkeypatch.setattr(messages_mod, "get_message_api", fake_get_api)
    state = FakeState()

    asyncio.run(fsm_write_recipient(FakeMessage(text="12345", user_id=1), state))

    assert state._state == UIStates.write_title
    assert state._data["recipient_id"] == 12345


def test_fsm_write_title_advances_to_text():
    state = FakeState(data={"recipient_id": 5})

    asyncio.run(fsm_write_title(FakeMessage(text="Тема", user_id=1), state))

    assert state._state == UIStates.write_text
    assert state._data["title"] == "Тема"


def test_cb_notitle_advances_to_text_without_title():
    state = FakeState(data={"recipient_id": 5})

    asyncio.run(cb_notitle(FakeCallbackQuery(user_id=1), state))

    assert state._state == UIStates.write_text
    assert state._data["title"] == ""


def test_cb_write_pick_selects_recipient_from_results():
    state = FakeState(data={"results": [{"id": 9, "label": "Иванов И.И."}]})

    asyncio.run(cb_write_pick(FakeCallbackQuery(user_id=1, data="mw:pick:0"), state))

    assert state._data["recipient_id"] == 9
    assert state._state == UIStates.write_title
