import sys
import os
import asyncio

# Configurar encoding UTF-8 en stdout/stderr de Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    if sys.version_info < (3, 14):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass

import uvicorn
from app.core.config import settings

if __name__ == "__main__":
    print(f"Iniciando {settings.APP_NAME} en http://{settings.SERVER_HOST}:{settings.SERVER_PORT}")
    uvicorn.run(
        "app.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=False,
        loop="asyncio"
    )

