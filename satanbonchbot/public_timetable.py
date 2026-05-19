"""Публичное расписание СПбГУТ с cabinet.sut.ru (без авторизации).

После задачи A.1 модуль содержит ТОЛЬКО публичную работу с расписанием:
загрузка списка групп и расписания, фильтры по преподавателю/аудитории,
сохранение/загрузка JSON. Авторизованная работа с ЛК (login, сообщения)
живёт в lk_client.DebuggableBonchAPI — единый клиент ЛК.
"""
import json, aiohttp, asyncio, logging
from satanbonchbot import parsers
from datetime import datetime, timedelta, time

# Браузерные заголовки для запросов в sut.ru — единый источник в config.py.
# Реэкспортируем имя ради обратной совместимости (public_timetable.BROWSER_HEADERS).
from satanbonchbot.config import  BROWSER_HEADERS


class BonchAPI:
    def __init__(self, first_day: str, limit: int = 6):
        self.first_day = datetime.strptime(first_day, '%Y-%m-%d')
        self.set_current_week()
        self.limit = limit
        self.days_of_week_str_to_int = {'Понедельник': 0, 'Вторник': 1, 'Среда': 2, 'Четверг': 3, 'Пятница': 4, 'Суббота': 5}
        self.days_of_week = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']

    def set_current_week(self):
        today = datetime.now()
        if today < self.first_day:
            self.cur_week = 0
        else:
            cur_day = self.first_day
            self.cur_week = 0
            while today > cur_day:
                self.cur_week += 1
                cur_day = cur_day + timedelta(days=7)
            cur_day = cur_day - timedelta(days=7)
            self.cur_week -= 1

    async def get_schet(self):
        # На новом cabinet.sut.ru параметр "schet" больше не используется.
        # Метод оставлен как no-op для обратной совместимости с вызывающим кодом.
        self.schet = None

    @staticmethod
    def _parse_id_name_pairs(text: str) -> dict:
        """Разбирает ответ cabinet.sut.ru вида 'id1,name1;id2,name2;...' в {id: name}."""
        return parsers.parse_id_name_pairs(text)

    @staticmethod
    async def _read_id_name_pairs(response) -> dict:
        """Устойчиво читает ответ cabinet.sut.ru со списком факультетов/групп.

        Под нагрузкой эндпоинт изредка отдаёт битое тело (не декодируется в
        UTF-8) или HTTP-ошибку. get_groups запускается первым в загрузке
        расписания — раньше такой ответ ронял весь рефреш с трейсбеком. Читаем
        байты и декодируем устойчиво (errors='replace'), любую ошибку гасим в
        пустой словарь с понятным логом — как в фиксе _get_timetable_once.
        """
        try:
            response.raise_for_status()
            raw = await response.read()
            text = raw.decode('utf-8', errors='replace')
            return BonchAPI._parse_id_name_pairs(text)
        except Exception as e:
            logging.warning("Не удалось разобрать список факультетов/групп: %s", e)
            return {}

    async def get_groups(self):
        # cabinet.sut.ru отдаёт группы по факультетам через POST-эндпоинт:
        # сначала запрашиваем список факультетов, затем группы каждого факультета.
        URL = 'https://cabinet.sut.ru/raspisanie_all_new.php'

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(20), trust_env=True, headers=BROWSER_HEADERS) as session:
            async with session.post(URL, data={'choice': '1', 'type_z': '1', 'kurs': ''}) as response:
                faculties = await self._read_id_name_pairs(response)

            groups = {}
            for faculty_id in faculties:
                async with session.post(URL, data={'choice': '1', 'type_z': '1', 'kurs': '', 'faculty': faculty_id}) as response:
                    groups.update(await self._read_id_name_pairs(response))

        # Битый ответ на список групп не должен затирать ранее загруженные группы.
        self.groups_id = groups or getattr(self, 'groups_id', {})

    async def get_timetable(self, session: aiohttp.ClientSession, type_z: str, group_id: str) -> list:
        # cabinet.sut.ru при нагрузке иногда не отдаёт расписание — повторяем до 3 раз.
        result = 'Ошибка сервера'
        for attempt in range(3):
            result = await self._get_timetable_once(session, type_z, group_id)
            if result != 'Ошибка сервера':
                return result
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
        return result

    async def _get_timetable_once(self, session: aiohttp.ClientSession, type_z: str, group_id: str) -> list:
        URL = 'https://cabinet.sut.ru/raspisanie_all_new'
        data = {'type_z': str(type_z), 'group': str(group_id), 'ok': 'Показать'}

        try:
            async with session.post(URL, data=data) as response:
                if response.status != 200:
                    return 'Ошибка сервера'

                # Под нагрузкой cabinet.sut.ru изредка отдаёт тело, которое не
                # декодируется как UTF-8 (усечённый/ошибочный ответ). Читаем
                # байты и декодируем устойчиво — без падения в UnicodeDecodeError;
                # битый ответ просто не распарсится и группа уйдёт в ретрай.
                raw = await response.read()
                text = raw.decode('utf-8', errors='replace')

                group_name = self.groups_id.get(group_id, group_id)
                return parsers.parse_timetable_table(text, group_name, self.first_day)
        except Exception as e:
            logging.warning("Не удалось получить расписание группы %s: %s", group_id, e)
            return 'Ошибка сервера'

    @staticmethod
    def parse_lesson_time(time_str) -> datetime:
        if not time_str: return time(0, 0)
        start_time_str = time_str.split('-')[0]
        try: return datetime.strptime(start_time_str, '%H:%M').time()
        except ValueError: return time(0, 0)

    @staticmethod
    def teacher_timetable(timetable: dict, teacher: str) -> list:
        teacher_lessons = []
        for _, lessons in timetable.items():
            for lesson in lessons:
                if lesson['ФИО преподавателя'] and teacher in lesson['ФИО преподавателя']:
                    teacher_lessons.append(lesson)

        return sorted(teacher_lessons, key=lambda x: (x['Номер недели'], x['Номер дня недели'], BonchAPI.parse_lesson_time(x['Время занятия'])))

    @staticmethod
    def classroom_timetable(timetable: dict, classroom: str) -> list:
        classroom_lessons = []
        for _, lessons in timetable.items():
            for lesson in lessons:
                if lesson['Номер кабинета'] and classroom in lesson['Номер кабинета']:
                    classroom_lessons.append(lesson)

        return sorted(classroom_lessons, key=lambda x: (x['Номер недели'], x['Номер дня недели'], BonchAPI.parse_lesson_time(x['Время занятия'])))

    @staticmethod
    def format_output(timetable: list, week_number: int = None) -> str:
        output = ''

        if not timetable:
            output += 'Нет занятий для отображения\n'
            return output

        lessons_by_week = {}
        if week_number is None:
            for lesson in timetable:
                week = lesson['Номер недели']
                if week not in lessons_by_week:
                    lessons_by_week[week] = []
                lessons_by_week[week].append(lesson)
        else:
            lessons_by_week[week_number] = [lesson for lesson in timetable if lesson['Номер недели'] == week_number]
            if not lessons_by_week[week_number]:
                output += f'Нет занятий для недели {week_number}\n'
                return output

        for week, week_lessons in lessons_by_week.items():
            output += f'Неделя №{week}\n\n'

            grouped_lessons = {}
            for lesson in week_lessons:
                key = (lesson['Число'], lesson['Номер занятия'], lesson['Предмет'], lesson['Номер кабинета'])
                if key not in grouped_lessons:
                    grouped_lessons[key] = {
                        'Число': lesson['Число'],
                        'День недели': lesson['День недели'],
                        'Время занятия': lesson['Время занятия'],
                        'Номер занятия': lesson['Номер занятия'],
                        'Предмет': lesson['Предмет'],
                        'Группы': {lesson['Группа']},
                        'Преподаватели': set(),
                        'Тип занятия': lesson.get('Тип занятия', ''),
                        'Номер кабинета': lesson['Номер кабинета'],
                    }

                    teacher_string = lesson.get('ФИО преподавателя', '')
                    if teacher_string:
                        teachers = [t.strip() for t in teacher_string.split(';')]
                        for teacher in teachers:
                            grouped_lessons[key]['Преподаватели'].add(teacher)
                    else:
                        grouped_lessons[key]['Преподаватели'] = set()

                else:
                    grouped_lessons[key]['Группы'].add(lesson['Группа'])

                    teacher_string = lesson.get('ФИО преподавателя', '')
                    if teacher_string:
                        teachers = [t.strip() for t in teacher_string.split(';')]
                        for teacher in teachers:
                            grouped_lessons[key]['Преподаватели'].add(teacher)

            lessons_list = list(grouped_lessons.values())
            lessons_list.sort(key=lambda x: (x['Число'], BonchAPI.parse_lesson_time(x['Время занятия'])))

            if lessons_list:
                prev_day = lessons_list[0]['Число']
                day_of_week = lessons_list[0]['День недели']
                output += f'{prev_day} | {day_of_week}\n\n'

                for lesson in lessons_list:
                    cur_day = lesson['Число']
                    if cur_day != prev_day:
                        day_of_week = lesson['День недели']
                        prev_day = cur_day
                        output += f'{prev_day} | {day_of_week}\n\n'

                    lesson_time = lesson['Время занятия']
                    lesson_number = lesson['Номер занятия']
                    subject = lesson['Предмет']
                    groups = ', '.join(sorted(lesson['Группы']))
                    lesson_type = lesson.get('Тип занятия', '')
                    teachers = '; '.join(sorted(lesson['Преподаватели']))
                    room = lesson['Номер кабинета']

                    output += f'{lesson_time}\n'
                    output += f'{lesson_number}. {subject} | {groups}\n'
                    if lesson_type: output += f'{lesson_type}\n'
                    if teachers: output += f'{teachers}\n'
                    if room: output += f'{room}\n'
                    output += '\n'
            else:
                output += "Нет занятий на этой неделе\n\n"

        output = output[:-1]
        return output

    @staticmethod
    def save_to_json(timetable: dict, filepath: str = 'timetable.json'):
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(timetable, f, indent=4, ensure_ascii=False)
            logging.info('Расписание сохранено в файл: %s', filepath)
        except Exception as e:
            logging.error('Ошибка при сохранении расписания в файл: %s', e, exc_info=True)

    @staticmethod
    def load_from_json(filepath: str = 'timetable.json') -> dict:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                # object_hook интернирует строки прямо при парсинге (задача D.1):
                # снижает и резидентную память, и пик при загрузке ~71 МБ JSON.
                timetable = json.load(f, object_hook=parsers.intern_strings)
            logging.info('Расписание загружено из файла: %s', filepath)
            return timetable
        except Exception as e:
            logging.error('Ошибка при загрузке расписания из файла: %s', e, exc_info=True)
            return None
