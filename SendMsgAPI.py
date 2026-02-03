import os, re, aiofiles, aiohttp, asyncio
from time import strftime, localtime

class Log:
    BLACK   = '\x1b[0;90m'
    RED     = '\x1b[0;91m'
    GREEN   = '\x1b[0;92m'
    YELLOW  = '\x1b[0;93m'
    BLUE    = '\x1b[0;94m'
    PURPLE  = '\x1b[0;95m'
    CYAN    = '\x1b[0;96m'
    WHITE   = '\x1b[0;97m'
    RESET   = '\x1b[0m'

    @staticmethod
    def print(str, color):
        print(f'{color}[{Log.current_time()}] {str}{Log.RESET}')

    @staticmethod
    def current_time():
        return strftime("%H:%M:%S", localtime())

    @staticmethod
    def info(message):
        Log.print(message, Log.WHITE)

    @staticmethod
    def error(message):
        Log.print(message, Log.RED)

    @staticmethod
    def warning(message):
        Log.print(message, Log.YELLOW)

    @staticmethod
    def success(message):
        Log.print(message, Log.GREEN)

    @staticmethod
    def cls():
        os.system('cls' if os.name=='nt' else 'clear')

class SendMsgAPI:
    def __init__(self):
        options = {}
        
        with open('options.txt', 'r', encoding='utf-8') as file:
            for line in file:
                clean_line = line.split('#')[0].strip()
                if not clean_line or '=' not in clean_line: continue
                key, value = clean_line.split('=', 1)
                options[key.strip()] = value.strip()

        self.email = options.get('login', '')
        self.password = options.get('password', '')

    async def login(self) -> bool:
        AUTH = f'https://lk.sut.ru/cabinet/lib/autentificationok.php?users={self.email}&parole={self.password}'
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
                                Log.success('успешная авторизация')
                                return True
                        else:
                            Log.error('ошибка при авторизации')
                            return False
        except Exception as e:
            Log.error(f'ошибка при попытке авторизации | {type(e).__name__} {e}')
            return False

    async def send_msg(self, id: int, title: str, message: str, idinfo: int = 0) -> bool:
        URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message.php'
        
        data = {
            "idinfo": str(idinfo),
            "item": '0',
            "title": title,
            "mes_otvet": message,
            "adresat": str(id),
            "saveotv": ''
        }

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(10)) as session:
                async with session.post(URL, cookies=self.cookies, data=data) as response:
                    response.raise_for_status()
                    text = await response.text()
                    if text == '':
                        Log.success('успешная отправка сообщения')
                        return True
                    else:
                        Log.error('ошибка при отправке сообщения')
                        return False
        except Exception as e:
            Log.error(f'ошибка при отправке сообщения | {type(e).__name__} {e}')
            return False

    async def upload_file(self, filename: str, id: int = 0) -> int:
        URL = 'https://lk.sut.ru/cabinet/project/cabinet/forms/message_create_stud.php'

        try:
            async with aiofiles.open(filename, 'rb') as f:
                file = await f.read()

            data = aiohttp.FormData()
            data.add_field("id", str(id))
            data.add_field("upload", "")
            data.add_field('userfile', file, filename=filename)

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(10)) as session:
                async with session.post(URL, cookies=self.cookies, data=data) as response:
                    response.raise_for_status()
                    text = await response.text()
                    match = re.search(r"data\.idinfo = \"(\d+)\"", text)
                    idinfo = match.group(1)
                    Log.success('успешная загрузка файла')
                    return int(idinfo)
        except Exception as e:
            Log.error(f'ошибка при отправке сообщения | {type(e).__name__} {e}')
            return 0

async def main():
    api = SendMsgAPI()
    await api.login()
    ids = [113714]
    idinfo = await api.upload_file('8_marta.jpg')
    for id in ids:
        await api.send_msg(id, '', 'С 8 марта! Йоу!', idinfo)

    #api.send_msg(113714, 'Отчисление из СПбГУТ', 'Здравствуйте, спешим вас обрадовать, вы отчислены из СПбГУТ!')

if __name__ == '__main__':
    '''
    Укажите в файле options.txt:
        login=your_login
        password=your_password

    Узнать id человека:
        https://lk.sut.ru/cabinet/subconto/search.php?value=Рябинкин%20М.А.
    '''

    asyncio.run(main())