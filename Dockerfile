FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN addgroup --system --gid 10001 royalbot \
    && adduser --system --uid 10001 --ingroup royalbot royalbot

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY README.md ./README.md
RUN mkdir -p /app/data && chown -R royalbot:royalbot /app
USER royalbot

CMD ["python", "-m", "app.main"]
