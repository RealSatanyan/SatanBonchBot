"""Обработчики aiogram: отправка сообщения в ЛК.

Разрезано из handlers/messages.py (задача C.1) — поддомен «отправка»: команда
/send_lk, выбор получателя, ввод темы/текста, прикрепление файла, FSM-диалог
«✏️ Написать». Содержит хэндлер-хелперы start_recipient_pick и
_send_lk_message_from_state. Поведение хэндлеров не менялось.

Разделяемое состояние сервиса сообщений (pending_lk_messages) читается/пишется
модуль-квалифицированно через messages_service.*.
"""

import os
import logging
import tempfile

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
# InlineKeyboardMarkup / InlineKeyboardButton импортируются локально в cmd_send_lk.

from states import UIStates
from keyboards import (
    login_prompt_kb,
    cancel_kb,
    main_menu_kb,
    title_kb,
    file_skip_kb,
    recipients_page_kb,
)
from db import is_registered
from lk_client import (
    get_message_api,
    lk_search_recipients,
    lk_send_message,
    lk_upload_file,
    LK_MAX_FILE_SIZE_MB,
)
import messages_service
from messages_service import _invalidate_messages_cache

router = Router()


@router.message(Command("send_lk"))
async def cmd_send_lk(message: types.Message, override_text: str = None):
    """
    Команда для отправки сообщения в ЛК.
    Варианты:
      /send_lk <id_в_ЛК> <текст>        — отправка по известному ID
      /send_lk <Фамилия[ И.О.]> <текст> — поиск получателя по ФИО и выбор из списка
    """
    user_id = message.from_user.id

    try:
        # Текст из меню (override_text) либо из самой команды /send_lk
        if override_text is not None:
            text_after_command = override_text.strip()
        else:
            text_after_command = message.text[len("/send_lk"):].strip()
        if not text_after_command:
            await message.answer(
                "Использование:\n"
                "/send_lk <id_в_ЛК> <текст сообщения>\n"
                "или\n"
                "/send_lk <Фамилия[ И.О.]> <текст сообщения>\n\n"
                "Примеры:\n"
                "/send_lk 113714 Привет из Telegram бота!\n"
                "/send_lk Платонов Д.И. Реально работает?"
            )
            return

        # Разбиваем на слова
        words = text_after_command.split()

        # Если первое слово — число, это ID
        if words[0].isdigit():
            recipient_raw = words[0]
            text = " ".join(words[1:]).strip()
            if not text:
                await message.answer("Текст сообщения не может быть пустым.")
                return
        else:
            # Ищем границу между ФИО и текстом сообщения
            # ФИО обычно: Фамилия И.О. (1-3 слова)
            # Эвристика: после инициалов (формат "X.X.") следующее слово с заглавной — это начало текста
            recipient_words = []
            text_start_idx = None

            def is_initials(word):
                """Проверяет, является ли слово инициалами (формат X.X. или X.X)"""
                if not word:
                    return False
                # Убираем точку в конце, если есть
                word_clean = word.rstrip('.')
                # Проверяем формат: одна буква, точка, одна буква (и опционально точка в конце)
                if len(word_clean) == 3 and word_clean[1] == '.':
                    return word_clean[0].isupper() and word_clean[2].isupper()
                return False

            for i, word in enumerate(words):
                # Если слово заканчивается на знак препинания (кроме точки в инициалах) — это начало текста
                if word and word[-1] in "!?," and i > 0:
                    text_start_idx = i
                    break

                # Если предыдущее слово было инициалами, а текущее начинается с заглавной — это начало текста
                if i > 0 and is_initials(words[i-1]) and word and word[0].isupper():
                    text_start_idx = i
                    break

                # Если уже есть 2+ слова и текущее слово длинное (более 8 символов) — это начало текста
                if i >= 2 and len(word) > 8:
                    text_start_idx = i
                    break

                # Если уже есть 3 слова — считаем, что ФИО закончилось
                if i >= 3:
                    text_start_idx = i
                    break

                recipient_words.append(word)

            # Если не нашли границу, пробуем взять первые 2-3 слова как ФИО, остальное — текст
            if text_start_idx is None:
                if len(words) >= 3:
                    # Если есть 3+ слова, берём первые 2 (фамилия + инициалы), остальное — текст
                    recipient_words = words[:2]
                    text_start_idx = 2
                elif len(words) == 2:
                    # Если только 2 слова, возможно это ФИО без текста или текст без ФИО
                    # Проверяем, является ли второе слово инициалами
                    if is_initials(words[1]):
                        await message.answer(
                            "Не указан текст сообщения.\n\n"
                            "Использование:\n"
                            "/send_lk <id_в_ЛК> <текст сообщения>\n"
                            "или\n"
                            "/send_lk <Фамилия[ И.О.]> <текст сообщения>\n\n"
                            "Примеры:\n"
                            "/send_lk 113714 Привет из Telegram бота!\n"
                            "/send_lk Платонов Д.И. Реально работает?"
                        )
                        return
                    else:
                        # Возможно, это фамилия + текст (без инициалов)
                        recipient_words = words[:1]
                        text_start_idx = 1
                else:
                    # Только одно слово — это либо фамилия без текста, либо ошибка
                    await message.answer(
                        "Не удалось определить ФИО и текст сообщения.\n\n"
                        "Использование:\n"
                        "/send_lk <id_в_ЛК> <текст сообщения>\n"
                        "или\n"
                        "/send_lk <Фамилия[ И.О.]> <текст сообщения>\n\n"
                        "Примеры:\n"
                        "/send_lk 113714 Привет из Telegram бота!\n"
                        "/send_lk Платонов Д.И. Реально работает?"
                    )
                    return

            recipient_raw = " ".join(recipient_words)
            text = " ".join(words[text_start_idx:]).strip()
            if not text:
                await message.answer("Текст сообщения не может быть пустым.")
                return

        # Получаем API с авторизацией и cookies
        message_api = await get_message_api(user_id)
        if not message_api:
            await message.answer("❌ Не удалось авторизоваться в ЛК. Выполните /login и попробуйте снова.")
            return

        # Если передан числовой ID — отправляем сразу
        if recipient_raw.isdigit():
            recipient_id = int(recipient_raw)
            status_msg = await message.answer("⏳ Отправляю сообщение в ЛК по ID...")

            ok = await lk_send_message(
                message_api=message_api,
                recipient_id=recipient_id,
                title="",
                message_text=text,
                idinfo=0,
            )

            if ok:
                await status_msg.edit_text(f"✅ Сообщение успешно отправлено в ЛК (id={recipient_id}).")
            else:
                await status_msg.edit_text("❌ Не удалось отправить сообщение в ЛК. Проверьте ID адресата и авторизацию.")
            return

        # Иначе ищем получателя по ФИО через subconto
        query = recipient_raw
        search_msg = await message.answer(f"⏳ Ищу получателя по запросу: {query!r}...")
        results = await lk_search_recipients(message_api, query)

        if not results:
            await search_msg.edit_text(f"❌ Получатели по запросу {query!r} не найдены.")
            return

        # Если ровно один результат — отправляем сразу
        if len(results) == 1:
            recipient_id = results[0]["id"]
            label = results[0]["label"]
            await search_msg.edit_text(f"⏳ Найден получатель: {label}\nОтправляю сообщение...")

            ok = await lk_send_message(
                message_api=message_api,
                recipient_id=recipient_id,
                title="",
                message_text=text,
                idinfo=0,
            )

            if ok:
                await search_msg.edit_text(f"✅ Сообщение успешно отправлено в ЛК получателю: {label}")
            else:
                await search_msg.edit_text(f"❌ Не удалось отправить сообщение в ЛК получателю: {label}")
            return

        # Если несколько результатов — предлагаем выбрать из списка
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        # Ограничим до 10 верхних результатов
        choices = results[:10]

        keyboard = []
        for r in choices:
            rid = r["id"]
            label = r["label"]
            keyboard.append(
                [InlineKeyboardButton(text=label, callback_data=f"lk_send_{rid}")]
            )

        # Сохраняем текст сообщения для последующей отправки после выбора
        for r in choices:
            key = (user_id, r["id"])
            messages_service.pending_lk_messages[key] = {
                "text": text,
                "title": "",
                "label": r["label"],
            }

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await search_msg.edit_text(
            "🔎 Найдено несколько получателей.\n"
            "Выберите нужного, чтобы отправить сообщение:",
            reply_markup=reply_markup,
        )

    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения в ЛК для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("❌ Не удалось отправить сообщение. Попробуй позже.")


@router.callback_query(F.data.startswith("lk_send_"))
async def handle_lk_send_callback(callback_query: CallbackQuery):
    """
    Обработчик выбора получателя для отправки сообщения в ЛК.
    """
    user_id = callback_query.from_user.id

    try:
        data = callback_query.data
        recipient_id = int(data.split("_")[-1])
        key = (user_id, recipient_id)

        if key not in messages_service.pending_lk_messages:
            await callback_query.answer("Сохраненное сообщение не найдено, попробуйте снова через /send_lk.", show_alert=True)
            return

        payload = messages_service.pending_lk_messages.pop(key)
        text = payload.get("text", "")
        title = payload.get("title", "")
        label = payload.get("label", f"id={recipient_id}")

        message_api = await get_message_api(user_id)
        if not message_api:
            await callback_query.answer("❌ Ошибка авторизации в ЛК. Выполните /login.", show_alert=True)
            return

        await callback_query.answer("⏳ Отправляю сообщение...", show_alert=False)

        ok = await lk_send_message(
            message_api=message_api,
            recipient_id=recipient_id,
            title=title,
            message_text=text,
            idinfo=0,
        )

        if ok:
            await callback_query.message.edit_text(f"✅ Сообщение успешно отправлено в ЛК получателю: {label}")
        else:
            await callback_query.message.edit_text(f"❌ Не удалось отправить сообщение в ЛК получателю: {label}")

    except Exception as e:
        logging.error(f"Ошибка при обработке callback отправки ЛК сообщения для пользователя {user_id}: {e}", exc_info=True)
        await callback_query.answer("⚠️ Что-то пошло не так. Попробуй позже.", show_alert=True)


@router.callback_query(F.data == "m:msg:write")
async def cb_msg_write(callback_query: CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    if not is_registered(user_id):
        await callback_query.answer()
        await callback_query.message.answer("Сначала войди в ЛК.", reply_markup=login_prompt_kb())
        return
    await callback_query.answer()
    await state.set_state(UIStates.write_recipient)
    await callback_query.message.answer(
        "✏️ Кому отправить?\n\nВведи ID получателя в ЛК или его фамилию (можно с инициалами):",
        reply_markup=cancel_kb(),
    )


async def start_recipient_pick(target_message: types.Message, user_id: int, query: str, state: FSMContext):
    """Ищет получателя: ID или единственный — сразу к тексту, несколько — список с прокруткой."""
    message_api = await get_message_api(user_id)
    if not message_api:
        await state.clear()
        await target_message.answer(
            "❌ Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return

    if query.isdigit():
        await state.update_data(recipient_id=int(query), recipient_label=f"id={query}")
        await state.set_state(UIStates.write_title)
        await target_message.answer(
            f"Получатель: id={query}\n\nВведи тему сообщения:",
            reply_markup=title_kb(),
        )
        return

    status = await target_message.answer(f"⏳ Ищу получателя «{query}»...")
    results = await lk_search_recipients(message_api, query)

    if not results:
        await state.set_state(UIStates.write_recipient)
        await status.edit_text(
            f"❌ Не нашёл получателя «{query}».\n"
            "Введи фамилию ещё раз (без инициалов) или числовой ID:",
            reply_markup=cancel_kb(),
        )
        return

    if len(results) == 1:
        r = results[0]
        await state.update_data(recipient_id=r["id"], recipient_label=r["label"])
        await state.set_state(UIStates.write_title)
        await status.edit_text(
            f"Получатель: {r['label']}\n\nВведи тему сообщения:",
            reply_markup=title_kb(),
        )
        return

    await state.update_data(results=results)
    await state.set_state(UIStates.write_pick)
    await status.edit_text(
        f"🔎 Нашёл {len(results)} получателей по запросу «{query}».\n"
        "Выбери нужного (или введи фамилию точнее):",
        reply_markup=recipients_page_kb(results, 0),
    )


@router.message(UIStates.write_recipient)
async def fsm_write_recipient(message: types.Message, state: FSMContext):
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введи ID или фамилию получателя:", reply_markup=cancel_kb())
        return
    await start_recipient_pick(message, message.from_user.id, query, state)


@router.message(UIStates.write_pick)
async def fsm_write_pick_refine(message: types.Message, state: FSMContext):
    query = (message.text or "").strip()
    if not query:
        return
    await start_recipient_pick(message, message.from_user.id, query, state)


@router.callback_query(F.data == "mw:noop")
async def cb_write_noop(callback_query: CallbackQuery):
    await callback_query.answer()


@router.callback_query(F.data.startswith("mw:page:"))
async def cb_write_page(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    page = int(callback_query.data.split(":")[2])
    data = await state.get_data()
    results = data.get("results", [])
    if not results:
        return
    try:
        await callback_query.message.edit_reply_markup(reply_markup=recipients_page_kb(results, page))
    except Exception:
        pass


@router.callback_query(F.data.startswith("mw:pick:"))
async def cb_write_pick(callback_query: CallbackQuery, state: FSMContext):
    idx = int(callback_query.data.split(":")[2])
    data = await state.get_data()
    results = data.get("results", [])
    if idx < 0 or idx >= len(results):
        await callback_query.answer("Список устарел, начни заново.", show_alert=True)
        return
    r = results[idx]
    await state.update_data(recipient_id=r["id"], recipient_label=r["label"])
    await state.set_state(UIStates.write_title)
    await callback_query.answer()
    try:
        await callback_query.message.edit_text(f"✅ Получатель: {r['label']}")
    except Exception:
        pass
    await callback_query.message.answer("Введи тему сообщения:", reply_markup=title_kb())


@router.message(UIStates.write_title)
async def fsm_write_title(message: types.Message, state: FSMContext):
    title = (message.text or "").strip()
    await state.update_data(title=title)
    await state.set_state(UIStates.write_text)
    await message.answer("Тема принята. Теперь введи текст сообщения:", reply_markup=cancel_kb())


@router.callback_query(F.data == "mw:notitle")
async def cb_notitle(callback_query: CallbackQuery, state: FSMContext):
    await state.update_data(title="")
    await state.set_state(UIStates.write_text)
    await callback_query.answer()
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback_query.message.answer("Без темы. Введи текст сообщения:", reply_markup=cancel_kb())


@router.message(UIStates.write_text)
async def fsm_write_text(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст пустой. Введи текст сообщения:", reply_markup=cancel_kb())
        return
    # Текст принят — предлагаем прикрепить файл (шаг write_file) либо отправить без него.
    await state.update_data(text=text)
    await state.set_state(UIStates.write_file)
    await message.answer(
        "Текст принят. Прикрепи файл (документ или фото) одним сообщением "
        "или отправь сообщение без файла:",
        reply_markup=file_skip_kb(),
    )


async def _send_lk_message_from_state(
    target_message: types.Message,
    user_id: int,
    state: FSMContext,
    idinfo: int = 0,
    uploaded_name: str = None,
):
    """
    Завершает диалог «Написать»: достаёт получателя/тему/текст из FSM-данных
    и отправляет сообщение в ЛК. idinfo>0 — к сообщению прикреплён файл.
    """
    data = await state.get_data()
    recipient_id = data.get("recipient_id")
    recipient_label = data.get("recipient_label") or (f"id={recipient_id}" if recipient_id else "—")
    title = data.get("title", "")
    text = data.get("text", "")
    await state.clear()
    if recipient_id is None:
        await target_message.answer(
            "Получатель не выбран. Начни заново: ✉️ Сообщения → Написать.",
            reply_markup=main_menu_kb(),
        )
        return
    message_api = await get_message_api(user_id)
    if not message_api:
        await target_message.answer(
            "❌ Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return
    status = await target_message.answer(f"⏳ Отправляю сообщение: {recipient_label}...")
    ok = await lk_send_message(
        message_api=message_api, recipient_id=int(recipient_id),
        title=title, message_text=text, idinfo=idinfo,
    )
    attach_note = f"\n📎 Вложение: {uploaded_name}" if (ok and idinfo and uploaded_name) else ""
    await status.edit_text(
        f"✅ Сообщение отправлено: {recipient_label}{attach_note}" if ok
        else f"❌ Не удалось отправить сообщение: {recipient_label}"
    )
    if ok:
        # Список сообщений мог измениться — сбрасываем тёплый кэш.
        _invalidate_messages_cache(user_id)


@router.message(UIStates.write_file, F.document | F.photo)
async def fsm_write_file_attach(message: types.Message, state: FSMContext):
    """Шаг write_file: пользователь прислал документ/фото — грузим его в ЛК."""
    user_id = message.from_user.id

    # Определяем файл и его размер ДО скачивания — чтобы не качать заведомо большое.
    if message.document:
        file_obj = message.document
        file_size = message.document.file_size or 0
        file_name = message.document.file_name or "file"
    else:
        file_obj = message.photo[-1]  # последний элемент — максимальное разрешение
        file_size = message.photo[-1].file_size or 0
        file_name = "photo.jpg"

    max_bytes = LK_MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        await message.answer(
            f"❌ Файл больше {LK_MAX_FILE_SIZE_MB} МБ — ЛК его не примет.\n"
            "Прикрепи файл поменьше или отправь сообщение без файла:",
            reply_markup=file_skip_kb(),
        )
        return

    message_api = await get_message_api(user_id)
    if not message_api:
        await state.clear()
        await message.answer(
            "❌ Не удалось войти в ЛК. Попробуй войти заново.",
            reply_markup=login_prompt_kb(),
        )
        return

    status = await message.answer("⏳ Загружаю файл в ЛК...")
    idinfo = 0
    tmp_dir = tempfile.mkdtemp(prefix="lk_upload_")
    tmp_path = os.path.join(tmp_dir, os.path.basename(file_name) or "file")
    try:
        await message.bot.download(file_obj, destination=tmp_path)
        idinfo = await lk_upload_file(message_api, tmp_path)
    except Exception:
        logging.error("Ошибка при скачивании/загрузке вложения для %s", user_id, exc_info=True)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass

    if not idinfo:
        await status.edit_text(
            "❌ Не удалось загрузить файл в ЛК.\n"
            "Прикрепи другой файл или отправь сообщение без файла:",
            reply_markup=file_skip_kb(),
        )
        return

    try:
        await status.delete()
    except Exception:
        pass
    await _send_lk_message_from_state(message, user_id, state, idinfo=idinfo, uploaded_name=file_name)


@router.message(UIStates.write_file)
async def fsm_write_file_invalid(message: types.Message, state: FSMContext):
    """Шаг write_file: пришёл не файл — подсказываем варианты."""
    await message.answer(
        "Это не файл. Прикрепи документ или фото одним сообщением "
        "или отправь сообщение без файла:",
        reply_markup=file_skip_kb(),
    )


@router.callback_query(F.data == "mw:nofile")
async def cb_write_nofile(callback_query: CallbackQuery, state: FSMContext):
    """Шаг write_file: «Отправить без файла» — шлём сообщение без вложения."""
    await callback_query.answer()
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _send_lk_message_from_state(
        callback_query.message, callback_query.from_user.id, state, idinfo=0,
    )
