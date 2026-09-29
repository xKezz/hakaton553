# Образ бота Win-Back MAX MVP (единственный локально запускаемый
# компонент решения; PostgreSQL поднимается отдельным сервисом compose).
#
# Сборка:  docker compose build      (или: docker build -t winback-bot .)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Сначала только зависимости: слой кэшируется и пересобирается лишь при
# изменении requirements.txt — сборка укладывается в 5 минут.
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

# Затем исходный код приложения.
COPY src/ ./src/

# Каталоги для CSV бизнеса и CSV-эмулятора лояльности.
# В compose сюда монтируется том ./data.
RUN mkdir -p /app/data/source_files /app/data/loyalty_mock

# HTTP-портов у решения нет: бот работает через long polling MAX.
CMD ["python", "-m", "src.Bot.bot"]
