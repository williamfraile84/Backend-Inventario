# Utilizar imagen oficial liviana de Python 3.12
FROM python:3.12-slim

# Evitar prompts interactivos y buffering de Python
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DIAN_HEADLESS_BROWSER=false \
    DISPLAY=:99 \
    PYTHONPATH=/app

WORKDIR /app

# Instalar utilidades mínimas del sistema y Xvfb (pantalla virtual en memoria RAM)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    xvfb \
    xauth \
    x11-utils \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependencias del backend e instalarlas
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Instalar Google Chrome oficial, Chromium y todas las librerías del sistema operativo requeridas
RUN playwright install --with-deps chrome chromium

# Copiar el código del backend a /app
COPY . /app/

# Exponer el puerto por defecto
EXPOSE 8000

# Iniciar Xvfb en background con pantalla virtual persistente y arrancar uvicorn como proceso principal
CMD ["sh", "-c", "Xvfb :99 -screen 0 1280x800x24 -ac -noreset > /dev/null 2>&1 & exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

