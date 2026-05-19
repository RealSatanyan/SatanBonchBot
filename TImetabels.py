import json, aiohttp, asyncio, logging, html
from bs4 import BeautifulSoup
import parsers
from datetime import datetime, timedelta, time

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
