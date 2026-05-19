"""Пакет обработчиков расписания.

Разрезан из единого handlers/schedule.py (задача A.2b, продолжение
декомпозиции 4.1) по поддоменам — каждый файл заводит свой Router().
``handlers.schedule.router`` агрегирует их, поэтому внешний интерфейс пакета
не изменился: main.py по-прежнему делает include_router(handlers.schedule.router)
на том же месте — precedence относительно других пакетов не сдвинулась.

Хэндлеры расписания взаимоисключающие — у каждого уникальный фильтр (свой
префикс callback / команда / FSM-состояние), поэтому порядок include
саброутеров на маршрутизацию не влияет.

Поддомены:
- personal — личное расписание из ЛК (/timetable, навигация, пресеты дня)
- group    — расписание группы (/groups, /group_timetable, навигация, картинка)
- teacher  — расписание преподавателя (/teacher_timetable, /teachers)
- room     — расписание аудитории (/classroom_timetable, /classrooms)
- common   — перезагрузка кэша расписания всех групп (/reload_timetable)
"""

from aiogram import Router

from handlers.schedule import personal, group, teacher, room, common

router = Router()
router.include_router(personal.router)
router.include_router(group.router)
router.include_router(teacher.router)
router.include_router(room.router)
router.include_router(common.router)

__all__ = ["router"]
