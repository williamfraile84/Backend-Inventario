# Utilizar imagen oficial liviana de Python 3.12
FROM python:3.12-slim

# Evitar prompts interactivos y buffering de Python
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DIAN_HEADLESS_BROWSER=false \
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

# Instalar Chromium y todas las librerías del sistema operativo requeridas (Playwright con root)
RUN playwright install --with-deps chromium

# Copiar el código del backend a /app
COPY . /app/

# Exponer el puerto por defecto
EXPOSE 8000

# Arrancar uvicorn envuelto en xvfb-run para proveer una pantalla virtual real de 1280x800
CMD ["sh", "-c", "exec xvfb-run --auto-servernum --server-args='-screen 0 1280x800x24 -ac' uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

