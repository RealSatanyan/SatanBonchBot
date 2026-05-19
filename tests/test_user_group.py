"""Тесты привязки пользователь → учебная группа (задача C.0).

Группа определяется из ЛК (страница raspisanie.php) и хранится в users.group_name.
Нужна для уведомлений об изменении расписания (C.1).
"""
import asyncio

import db
import parsers
from login_service import detect_user_group


RASPISANIE_HTML = """
<html><body>
<table class="simple-little-table"><tbody></tbody></table>
<div><br>Ваши учебные группы по расписанию на текущий семестр:
  <b><a target="_blank"
        href="https://lk.sut.ru/cabinet/project/cabinet/forms/spisok_stud_sql.php?groups=56252">
    <b>ИБТС-41</b></a></b>
</div>
</body></html>
"""


# --- parse_user_group --------------------------------------------------------

def test_parse_user_group_extracts_name():
    assert parsers.parse_user_group(RASPISANIE_HTML) == "ИБТС-41"


def test_parse_user_group_none_when_no_group_link():
    assert parsers.parse_user_group("<html><body>нет группы</body></html>") is None


def test_parse_user_group_empty_html():
    assert parsers.parse_user_group("") is None
    assert parsers.parse_user_group(None) is None


# --- db.get_user_group / set_user_group --------------------------------------

def test_user_group_none_by_default(temp_db):
    temp_db.execute("INSERT INTO users (user_id, email, password) VALUES (1, 'e', 'p')")
    temp_db.commit()
    assert db.get_user_group(1) is None


def test_set_and_get_user_group(temp_db):
    temp_db.execute("INSERT INTO users (user_id, email, password) VALUES (1, 'e', 'p')")
    temp_db.commit()

    db.set_user_group(1, "ИКВТ-21")

    assert db.get_user_group(1) == "ИКВТ-21"


# --- detect_user_group -------------------------------------------------------

class _FakeApi:
    def __init__(self, html):
        self._html = html

    async def get_raw_timetable(self):
        return self._html


def test_detect_user_group_stores_group(temp_db):
    temp_db.execute("INSERT INTO users (user_id, email, password) VALUES (5, 'e', 'p')")
    temp_db.commit()

    asyncio.run(detect_user_group(5, _FakeApi(RASPISANIE_HTML)))

    assert db.get_user_group(5) == "ИБТС-41"


def test_detect_user_group_no_group_keeps_none(temp_db):
    temp_db.execute("INSERT INTO users (user_id, email, password) VALUES (6, 'e', 'p')")
    temp_db.commit()

    asyncio.run(detect_user_group(6, _FakeApi("<html>пусто</html>")))

    assert db.get_user_group(6) is None


def test_detect_user_group_swallows_errors(temp_db):
    class _BrokenApi:
        async def get_raw_timetable(self):
            raise ConnectionError("ЛК недоступен")

    temp_db.execute("INSERT INTO users (user_id, email, password) VALUES (7, 'e', 'p')")
    temp_db.commit()

    # Не должно бросать исключение — определение группы не ломает вход.
    asyncio.run(detect_user_group(7, _BrokenApi()))

    assert db.get_user_group(7) is None
