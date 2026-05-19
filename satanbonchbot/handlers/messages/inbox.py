"""Обработчики aiogram: чтение входящих сообщений ЛК.

Разрезано из handlers/messages.py (задача C.1) — поддомен «чтение»: список
входящих, навигация по сообщениям, открытие конкретного сообщения. Поведение
хэндлеров не менялось.

Разделяемое состояние сервиса сообщений (message_states) читается/пишется
модуль-квалифицированно через messages_service.*.
"""

import html
import re
import logging
import time as time_module

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from satanbonchbot.keyboards import  login_prompt_kb
from satanbonchbot.db import  is_registered
from satanbonchbot.lk_messages import  get_message_api
from satanbonchbot import messages_service
from satanbonchbot.messages_service import  (
    MESSAGES_CACHE_TTL_SEC,
    show_message_list,
    _messages_cache_fresh,
    _build_message_state,
)

router = Router()


@router.message(Command("messages"))
async def cmd_messages(message: types.Message, uid: int = None):
    """
    Команда для просмотра входящих сообщений.
    """
    user_id = uid if uid is not None else message.from_user.id
    logging.info(f"Пользователь {user_id} запросил просмотр сообщений")

    try:
        # Тёплый кэш: повторный заход в пределах TTL — показываем без перезапроса
        # первой страницы из ЛК (экономит ~2–3 с). Кнопка «🔄 Обновить список»
        # остаётся; кэш сбрасывается после отправки сообщения.
        cached = messages_service.message_states.get(user_id)
        if _messages_cache_fresh(cached, time_module.time(), MESSAGES_CACHE_TTL_SEC):
            logging.info(f"Сообщения для {user_id} показаны из кэша (без перезапроса ЛК)")
            await show_message_list(user_id, message.chat.id, 0)
            return

        # Получаем API для работы с сообщениями
        message_api = await get_message_api(user_id)
        if not message_api:
            await message.answer("❌ Не удалось авторизоваться. Пожалуйста, выполните /login для авторизации.")
            return

        # Ленивая загрузка: тянем только первую страницу (~20 свежих сообщений),
        # остальные подгружаются по мере листания. Так /messages открывается за
        # пару секунд вместо ~10 на все 35 страниц.
        status_msg = await message.answer("⏳ Загружаю сообщения...")
        first_page = await message_api.get_messages_page(1)
        messages = first_page['messages']

        if not messages:
            # Проверяем, может быть проблема с авторизацией
            if not hasattr(message_api, 'cookies') or not message_api.cookies:
                await status_msg.edit_text("❌ Ошибка авторизации. Пожалуйста, выполните /login еще раз.")
            else:
                await status_msg.edit_text("📭 У вас нет входящих сообщений.\n\n💡 Если сообщения должны быть, проверьте авторизацию через /login")
            return

        # Сохраняем состояние для навигации (включая api для подгрузки страниц)
        messages_service.message_states[user_id] = _build_message_state(message_api, first_page)

        try:
            await status_msg.delete()
        except Exception:
            pass
        # Отображаем первое сообщение
        await show_message_list(user_id, message.chat.id, 0)

    except Exception as e:
        logging.error(f"Ошибка при получении сообщений для пользователя {user_id}: {e}", exc_info=True)
        await message.answer("❌ Не удалось загрузить сообщения. Попробуй позже.")


@router.callback_query(F.data.startswith("msg_"))
async def handle_message_callback(callback_query: CallbackQuery):
    """
    Обработчик callback для навигации по сообщениям.
    """
    user_id = callback_query.from_user.id
    data = callback_query.data

    try:
        if data.startswith("msg_prev_"):
            # Переход к предыдущему сообщению
            index = int(data.split("_")[-1]) - 1
            if user_id in messages_service.message_states and index >= 0:
                messages_service.message_states[user_id]['current_index'] = index
                await callback_query.answer()
                await callback_query.message.delete()
                await show_message_list(user_id, callback_query.message.chat.id, index)
            else:
                await callback_query.answer("Это первое сообщение", show_alert=True)

        elif data.startswith("msg_next_"):
            # Переход к следующему сообщению
            index = int(data.split("_")[-1]) + 1
            state = messages_service.message_states.get(user_id)
            if not state:
                await callback_query.answer("Список устарел — открой «Сообщения» заново", show_alert=True)
                return
            # Дошли до конца загруженного — лениво подгружаем следующую страницу.
            if index >= len(state['messages']) and state.get('loaded_pages', 1) < state.get('total_pages', 1):
                next_page = state.get('loaded_pages', 1) + 1
                page_data = await state['api'].get_messages_page(next_page)
                state['messages'].extend(page_data['messages'])
                state['loaded_pages'] = next_page
            if index < len(state['messages']):
                state['current_index'] = index
                await callback_query.answer()
                await callback_query.message.delete()
                await show_message_list(user_id, callback_query.message.chat.id, index)
            else:
                await callback_query.answer("Это последнее сообщение", show_alert=True)

        elif data.startswith("msg_open_"):
            # Открытие конкретного сообщения
            message_id = data.split("_")[-1]
            await callback_query.answer("⏳ Загружаю сообщение...")

            message_api = await get_message_api(user_id)
            if not message_api:
                await callback_query.message.answer("❌ Ошибка авторизации")
                return

            message_data = await message_api.get_message(message_id)

            if not message_data:
                await callback_query.message.answer("❌ Не удалось загрузить сообщение")
                return

            # Находим информацию о сообщении из списка
            msg_info = None
            if user_id in messages_service.message_states:
                for msg in messages_service.message_states[user_id]['messages']:
                    if msg['id'] == message_id:
                        msg_info = msg
                        break

            # Формируем текст сообщения
            title = message_data.get("name", msg_info.get("title", "Без названия") if msg_info else "Без названия")
            annotation = message_data.get("annotation", "Нет текста")

            # Декодируем HTML и удаляем теги
            if annotation:
                annotation = html.unescape(annotation)
                annotation = re.sub(r'<[^>]+>', '', annotation)

            text = f"📋 *{title}*\n\n"
            if msg_info:
                text += f"📅 *Дата:* {msg_info.get('date', 'Не указана')}\n"
                text += f"👤 *Отправитель:* {msg_info.get('sender', 'Неизвестно')}\n"
                text += "━━━━━━━━━━━━━━━━━━━━\n\n"

            text += f"{annotation}\n\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n"

            if msg_info and msg_info.get("files"):
                text += "\n📎 *Файлы:*\n"
                for file_info in msg_info["files"]:
                    file_name = file_info.get("name", "Файл")
                    file_url = file_info.get("url", "")
                    if file_url:
                        # Используем Markdown формат для ссылки: [текст](url)
                        # Экранируем специальные символы в URL и имени файла для Markdown
                        file_name_escaped = file_name.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]")
                        text += f"  • [{file_name_escaped}]({file_url})\n"
                    else:
                        text += f"  • {file_name}\n"

            text += f"\n🆔 ID: `{message_id}`"

            keyboard = [[InlineKeyboardButton(text="🔙 Назад к списку", callback_data="msg_back_to_list")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

            await callback_query.message.answer(text, parse_mode="Markdown", reply_markup=reply_markup)
            await callback_query.message.delete()

        elif data == "msg_refresh":
            # Обновление списка сообщений — заново тянем первую страницу.
            await callback_query.answer("🔄 Обновляю список...")

            message_api = await get_message_api(user_id)
            if not message_api:
                await callback_query.message.answer("❌ Ошибка авторизации")
                return

            first_page = await message_api.get_messages_page(1)
            if not first_page['messages']:
                await callback_query.message.answer("📭 У вас нет входящих сообщений.")
                await callback_query.message.delete()
                return

            messages_service.message_states[user_id] = _build_message_state(message_api, first_page)

            await callback_query.message.delete()
            await show_message_list(user_id, callback_query.message.chat.id, 0)

        elif data == "msg_back_to_list":
            # Возврат к списку сообщений
            if user_id in messages_service.message_states:
                current_index = messages_service.message_states[user_id].get('current_index', 0)
                await callback_query.message.delete()
                await show_message_list(user_id, callback_query.message.chat.id, current_index)
            else:
                await callback_query.message.answer("❌ Состояние навигации потеряно. Используйте /messages для обновления.")

    except Exception as e:
        logging.error(f"Ошибка при обработке callback сообщений для пользователя {user_id}: {e}", exc_info=True)
        await callback_query.answer("⚠️ Что-то пошло не так. Попробуй позже.", show_alert=True)


@router.callback_query(F.data == "m:msg:inbox")
async def cb_msg_inbox(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback_query.from_user.id
    if not is_registered(user_id):
        await callback_query.answer()
        await callback_query.message.answer("Сначала войди в ЛК.", reply_markup=login_prompt_kb())
        return
    await callback_query.answer("Загружаю...")
    await cmd_messages(callback_query.message, uid=user_id)
