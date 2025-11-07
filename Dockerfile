# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.11-slim
FROM python:${PYTHON_VERSION} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Dependencias del sistema (tesseract para OCR opcional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    tesseract-ocr \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias Python primero para cache estable
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copiar el código
COPY . /app

# Usuario no-root
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Cloud Run inyecta PORT en tiempo de ejecución (no es necesario EXPOSE)
ENV PORT=8080 \
    WORKERS=2 \
    THREADS=8 \
    TIMEOUT=900

# Lanzar Gunicorn enlazando a :$PORT (requerido por Cloud Run)
# Asegúrate de que el módulo sea app:app (archivo app.py con variable 'app')
CMD exec gunicorn --bind :${PORT} --workers ${WORKERS} --threads ${THREADS} --timeout ${TIMEOUT} app:app
