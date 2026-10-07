# Utilizar imagen oficial liviana de Python 3.12
FROM python:3.12-slim

# Evitar prompts interactivos y buffering de Python
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DIAN_HEADLESS_BROWSER=true \
    PYTHONPATH=/app

WORKDIR /app

# Instalar utilidades mínimas del sistema requeridas por Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependencias del backend e instalarlas
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Instalar Google Chrome oficial, Chromium y dependencias nativas del sistema
RUN playwright install --with-deps chrome chromium

# Copiar el código del backend a /app
COPY . /app/

# Exponer el puerto por defecto
EXPOSE 8000

# Arrancar uvicorn de forma directa, liviana y eficiente
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

