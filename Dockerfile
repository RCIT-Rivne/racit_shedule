FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    TZ=Europe/Kyiv

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Вподобання викладачів і циклові комісії (імпортуються в БД командою import-rules)
COPY config ./config
# Приклад вхідних даних (використовується, якщо файл не завантажено)
COPY ["info/Розклад на 2026 р (інформація).xlsx", "./info/"]

RUN useradd --create-home appuser && mkdir -p /data && chown appuser /data
USER appuser
VOLUME /data
EXPOSE 8000

# Один процес: черга задач живе в пам'яті, розв'язувач сам використовує всі ядра.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", "--timeout", "120", "app:create_app()"]
