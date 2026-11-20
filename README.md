# ⚡ Fruver POS Backend API & Core Services

[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00?style=for-the-badge&logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL%20Mode-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supported-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/Tests-40%2F40%20Passed%20(100%25)-22c55e?style=for-the-badge&logo=pytest&logoColor=white)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-F59E0B?style=for-the-badge&logo=open-source-initiative&logoColor=white)](LICENSE)

Backend de alto rendimiento desarrollado con **FastAPI** para el sistema **Fruver POS**. Proporciona una API REST modular, autenticación y autorización basada en roles (**RBAC** con JWT y Bcrypt), motor de precios comerciales con regla de **redondeo a centenas ($100 COP)**, asistente de facturación electrónica (**DIAN VPFE**), motor OCR dual (Gemini Vision + PaddleOCR ONNX) y catálogo maestro de 1.101 unidades de medida oficiales DIAN.

---

## 📸 Módulos del Sistema Gestionados por la API

| Control de Acceso (JWT & Bcrypt) | Catálogo Maestro & Búsqueda Omnidireccional |
| :---: | :---: |
| ![Seguridad y Login](docs/screenshots/01_login_dark.png) | ![Catálogo de Productos](docs/screenshots/04_catalogo_productos.png) |
| *Sesiones seguras, hashing Bcrypt estricto y roles RBAC* | *Búsqueda en tiempo real, filtros dinámicos y control de inventario* |

| Motor de Precios ($100 COP) | Facturas Electrónicas DIAN & OCR |
| :---: | :---: |
| ![Motor de Precios](docs/screenshots/05_product_form_modal.png) | ![Procesador de Facturas](docs/screenshots/07_facturas_processor.png) |
| *Cálculo de base gravable, IVA, fletes y regla a la centena* | *Scraping headless de DIAN VPFE, OCR dual y prorrateo de costos* |

---

## 🏗️ Arquitectura de la API

```mermaid
flowchart TD
    Client["📱 Frontend / POS Terminal (REST HTTP)"] --> Auth["🛡️ Middleware de Seguridad & CORS"]
    Auth --> Routers{"🔀 FastAPI Routers"}

    Routers --> R_Auth["/api/auth (Login, JWT, Sesiones)"]
    Routers --> R_Prod["/api/products (Búsqueda Omnidireccional)"]
    Routers --> R_Cat["/api/catalog (CRUD, Departamentos, Stock)"]
    Routers --> R_Inv["/api/invoices (Facturación DIAN & OCR)"]
    Routers --> R_Roles["/api/roles & /api/admin (RBAC & Auditoría)"]
    Routers --> R_Emp["/api/empresa (Datos fiscales de emisor)"]

    R_Inv --> DIAN_Scraper["🏛️ DIAN VPFE Scraper (Chromium Headless)"]
    R_Inv --> OCR_Dual["👁️ OCR Engine (Gemini Vision + PP-OCRv4)"]
    R_Inv --> Unit_Catalog["📐 DIAN Unit Service (1.101 Unidades)"]
    R_Cat --> Pricing["🧮 Pricing Engine (Redondeo $100 COP)"]

    Routers --> ORM["💾 SQLAlchemy 2.0 ORM (Modelos 3NF)"]
    ORM --> DB[("🗄️ Base de Datos\nSQLite WAL / PostgreSQL")]
    R_Inv --> Storage["📦 Storage Service (Local / S3 / R2)"]
```

---

## 🧮 Motor de Precios & Regla Comercial de Redondeo

El servicio `app.services.pricing_engine` implementa las reglas de cálculo financiero y redondeo comercial colombiano:

```mermaid
flowchart TD
    A["Costo de Compra en Factura ($C)"] --> B["Aplicar Descuentos Comerciales (% o $)"]
    B --> C["Base Gravable Neta"]
    C --> D["Calcular Impuestos (IVA 19%, IVA 5%, ICUI)"]
    D --> E["Prorratear Flete y Cargue por Kilo (si aplica)"]
    E --> F["Costo Unitario Total Real"]
    F --> G["Aplicar Margen Deseado: Sugerido = Costo / (1 - Margen)"]
    G --> H["Evaluar Residuo: R = Sugerido % 100"]
    H --> I{"¿Residuo R >= 50 COP?"}
    I -- "Sí" --> J["Redondear a CENTENA SUPERIOR\n(Ej: $3.550 ➔ $3.600)"]
    I -- "No" --> K["Redondear a CENTENA INFERIOR\n(Ej: $3.540 ➔ $3.500)"]
    J --> L["Precio Comercial Final Publicado en POS"]
    K --> L
    L --> M["Bitácora Inmutable de Auditoría en BD"]
```

---

## 🧾 Flujo del Asistente DIAN VPFE & Liquidación

```mermaid
sequenceDiagram
    autonumber
    actor Cliente as 💻 Cliente Frontend
    participant API as ⚡ API FastAPI
    participant QR as 🔍 DIAN QR Decoder
    participant Scraper as 🏛️ DIAN VPFE Scraper
    participant Units as 📐 Catálogo DIAN (1.101 Unidades)
    participant DB as 🗄️ SQLAlchemy / DB

    Cliente->>API: POST /api/invoices/process-qr (URL o Imagen QR)
    API->>QR: Extraer CUFE y URL oficial VPFE DIAN
    QR-->>API: URL DIAN VPFE Normalizada
    API->>Scraper: Consultar catálogo DIAN (Headless Chromium)
    Scraper-->>API: Ítems, Cantidades, Unidades, Precios, IVA
    API->>Units: Mapear códigos DIAN (KGM, LBR, DZN, BX, etc.)
    Units-->>API: Factores de Conversión a Unidad de Venta
    API->>API: Prorratear Flete y Cargue por Kilo
    API-->>Cliente: Liquidación Estructurada y Precios Sugeridos
    Cliente->>API: POST /api/invoices/confirm-reconciliation
    API->>DB: Actualizar Costos, Precios y Registrar Auditoría
    DB-->>API: Transacción Confirmada (ACID)
    API-->>Cliente: 200 OK con Resumen de Actualización
```

---

## 🔐 Matriz de Permisos RBAC

```mermaid
flowchart LR
    User["👤 Petición HTTP"] --> JWT["Firma JWT Verificada"]
    JWT --> Role{"Rol en Token"}
    
    Role -- "cajero" --> R1["🟢 Consulta Omnidireccional\n🟢 Escáner de Códigos\n🟢 Vista de Catálogo"]
    Role -- "supervisor" --> R2["🟢 Todo lo de Cajero +\n🟡 Edición Individual y Masiva de Precios\n🟡 Procesamiento de Facturas DIAN/OCR\n🟡 Sincronización POS"]
    Role -- "admin" --> R3["🟢 Todo lo de Supervisor +\n🔴 Gestión de Usuarios y Roles\n🔴 Auditoría Inmutable del Sistema\n🔴 Configuración Global"]

    style R1 fill:#dcfce7,stroke:#16a34a,color:#14532d
    style R2 fill:#fef3c7,stroke:#d97706,color:#78350f
    style R3 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
```

| Permiso | Endpoint Scope | Admin | Supervisor | Cajero |
| :--- | :--- | :---: | :---: | :---: |
| `can_lookup` | `GET /api/products/*`, `GET /api/catalog/*` | ✅ | ✅ | ✅ |
| `can_edit_single` | `PUT /api/products/price/*`, `PUT /api/catalog/products/*` | ✅ | ✅ | ❌ |
| `can_edit_bulk` | `POST /api/products/bulk-update` | ✅ | ✅ | ❌ |
| `can_process_invoices` | `POST /api/invoices/*`, `POST /api/invoices/process-qr` | ✅ | ✅ | ❌ |
| `can_view_audit` | `GET /api/auditoria/*` | ✅ | ✅ | ❌ |
| `can_sync_pos` | `POST /api/pos/sync` | ✅ | ✅ | ❌ |
| `can_manage_users` | `GET/POST/PUT/DELETE /api/admin/users/*` | ✅ | ❌ | ❌ |

---

## 📁 Estructura del Repositorio

```text
fruver-pos-backend/
├── app/
│   ├── core/                       # Configuración (.env), SQLAlchemy engine, JWT, Bcrypt
│   ├── models/                     # Modelos relacionales 3NF y esquemas Pydantic
│   ├── routers/                    # Endpoints modulares (auth, products, catalog, invoices, admin)
│   ├── services/                   # Motores de pricing, DIAN units, scrapers y OCR
│   ├── data/                       # Catálogos maestros (1.101 códigos de unidades DIAN)
│   └── main.py                     # Instancia principal FastAPI y middlewares
├── tests/                          # Suite de 40 pruebas automatizadas con Pytest
├── scripts/                        # Scripts de migración de BD, almacenamiento y utilidades
├── models_ocr/                     # Directorio reservado para pesos OCR locales (PP-OCRv4)
├── storage/                        # Almacenamiento local desacoplado (invoices, exports, temp)
├── backups/                        # Respaldos automáticos de base de datos
├── docs/screenshots/               # Capturas visuales de referencia
├── .dockerignore                   # Exclusiones de Docker
├── .env.example                    # Plantilla documentada de variables de entorno
├── .gitignore                      # Exclusiones estrictas de Git
├── Dockerfile                      # Imagen ligera de producción con Python 3.12-slim
├── LICENSE                         # Licencia abierta MIT
├── pytest.ini                      # Configuración de pruebas
└── requirements.txt                # Dependencias de producción
```

---

## 🚀 Instalación y Puesta en Marcha

### Prerrequisitos
- **Python 3.10, 3.11 o 3.12**.
- **Pip** y **Git**.

### 1. Clonar el Repositorio
```bash
git clone https://github.com/williamfraile84/Backend-Inventario.git
cd Backend-Inventario
```

### 2. Crear y Activar Entorno Virtual
```bash
# En Windows:
python -m venv venv
.\venv\Scripts\activate

# En Linux / macOS:
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalar Dependencias
```bash
pip install -r requirements.txt
```

### 4. Configurar Variables de Entorno
```bash
# En Windows:
copy .env.example .env

# En Linux / macOS:
cp .env.example .env
```
Edita `.env` para personalizar tu clave secreta JWT (`SECRET_KEY`), motor de base de datos (`DB_ENGINE=sqlite` o `DB_ENGINE=postgresql`), etc.

### 5. Iniciar la API en Modo Desarrollo
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **Swagger UI interactivo**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Documentación ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

---

## 🐳 Despliegue con Docker

Construye y levanta el contenedor con un solo comando:

```bash
# Construir imagen
docker build -t fruver-pos-backend .

# Ejecutar contenedor
docker run -d -p 8000:8000 --name fruver-backend --env-file .env fruver-pos-backend
```

---

## 🧪 Pruebas Automatizadas (Pytest)

Ejecuta la suite completa de 40 pruebas automatizadas:

```bash
pytest -v
```

---

## 📄 Licencia
Este proyecto está bajo la Licencia abierta [MIT](LICENSE).
