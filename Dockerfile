# Используем легковесный базовый образ Python.
# Нужен Python 3.10+: код использует синтаксис аннотаций вида `tuple[str, str] | None`.
FROM python:3.12-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем шрифты явно (включая NotoColorEmoji.ttf)
COPY *.ttf ./
COPY *.otf ./

# Копируем исходный код
COPY . .

# Указываем команду для запуска бота
CMD ["python", "-u", "main.py"]