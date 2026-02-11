# Используем легкую версию Python 3.11
FROM python:3.11-slim

# 1. Устанавливаем системные зависимости
# ffmpeg нужен для склейки видео и аудио в yt-dlp
# curl может пригодиться для проверки сети
RUN apt-get update && apt-get install -y \
  ffmpeg \
  curl \
  && rm -rf /var/lib/apt/lists/*

# 2. Создаем рабочую папку
WORKDIR /app

# 3. Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Копируем весь код проекта
COPY . .

# 5. Запускаем бота
CMD ["python", "bot.py"]
