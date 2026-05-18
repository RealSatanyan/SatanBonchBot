"""FSM-состояния диалогов бота (aiogram).

Извлечено из main.py (задача 4.1, декомпозиция). Чистый декларативный модуль.
"""
from aiogram.fsm.state import State, StatesGroup


class UIStates(StatesGroup):
    login_email = State()
    login_password = State()
    ask_group = State()
    ask_teacher = State()
    ask_classroom = State()
    write_recipient = State()
    write_pick = State()
    write_title = State()
    write_text = State()
