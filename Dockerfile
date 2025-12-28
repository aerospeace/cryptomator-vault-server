FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV FLASK_APP=app.main:create_app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
# By default, run as appuser, but allow override via docker-compose or docker run
USER ${APP_UID:-1000}:${APP_GID:-1000}

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app.main:create_app"]
