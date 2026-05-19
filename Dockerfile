# Используем легковесный базовый образ Python.
# Нужен Python 3.10+: код использует синтаксис аннотаций вида `tuple[str, str] | None`.
FROM python:3.12-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем шрифты отдельным слоём (assets/fonts/ — меняются редко, выгодно для кэша)
COPY assets/ ./assets/

# Копируем исходный код
COPY . .

# Указываем команду для запуска бота
CMD ["python", "-u", "-m", "satanbonchbot"]