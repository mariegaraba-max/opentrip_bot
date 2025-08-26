# Используем официальный Python-образ
FROM python:3.11-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Копируем зависимости (если есть requirements.txt)
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Переменные окружения (например, токен можно задавать в Render Dashboard)
ENV PYTHONUNBUFFERED=1

# Команда для запуска бота
# (замени bot.py на твой основной файл)
CMD ["python", "bot.py"]
