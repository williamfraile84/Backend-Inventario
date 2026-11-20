import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Configurar entorno de pruebas
os.environ["TESTING"] = "1"
os.environ["APP_ENV"] = "testing"
os.environ["DIAN_RECEPTOR_NIT"] = "40327379"

import pytest
from app.core.config import settings
settings.APP_ENV = "testing"
settings.DIAN_RECEPTOR_NIT = "40327379"

from app.core.database import init_db

@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Garantiza que la base de datos y sus tablas maestras estén inicializadas para la suite de pruebas."""
    init_db()
