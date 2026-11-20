# Política de Seguridad y Divulgación Responsable

La seguridad de **Fruver POS & Inventory Manager** y la protección de los datos comerciales y de facturación de los usuarios es una prioridad absoluta.

---

## 🛡️ Versiones Compatibles

Actualmente se proporciona soporte de seguridad y parches para las siguientes versiones:

| Versión | Soporte de Seguridad |
| :--- | :---: |
| 2.6.x (Actual) | ✅ Activo |
| 2.5.x | ⚠️ Solo parches críticos |
| < 2.5.0 | ❌ No soportado |

---

## 🚨 Notificación de Vulnerabilidades

Si descubres una posible vulnerabilidad de seguridad en este proyecto:

1. **NO abras un Issue público en GitHub** con detalles que puedan comprometer instalaciones existentes.
2. Envía un reporte privado detallado a través de **GitHub Security Advisories** en la pestaña *Security > Report a vulnerability* del repositorio.
3. Incluye:
   - Descripción detallada de la vulnerabilidad.
   - Pasos exactos o prueba de concepto (PoC) para reproducirla.
   - Versión del software afectada y sistema operativo.
   - Impacto estimado (e.g. elevación de privilegios, inyección, divulgación de datos).

---

## 🔒 Prácticas de Hardening Incluidas

Este proyecto implementa defensas en profundidad por diseño:
- **Hashing Criptográfico**: Autenticación estricta con Bcrypt (`bcrypt.hashpw` y `bcrypt.checkpw`) sin retrogradación a algoritmos obsoletos (MD5/SHA1).
- **Protección contra Path Traversal**: Sanitización estricta de rutas de almacenamiento en local y S3 con normalización y verificación de límites de directorio.
- **Validación de Firmas Binarias (Magic Bytes)**: Inspección de los primeros bytes de cada archivo recibido para impedir el cambio de extensión malicioso (spoofing).
- **Control de Acceso Basado en Roles (RBAC)**: Autorización granular validada en backend en cada ruta crítica (principio de mínimo privilegio).
- **Aislamiento de Secretos**: Ninguna credencial real, archivo `.env` o base de datos de producción se incluye en el control de versiones. Se suministran exclusivamente plantillas `.env.example`.
