# Guía de Contribución al Backend de Fruver POS

¡Gracias por contribuir al desarrollo del backend de **Fruver POS**!

---

## 📋 Directrices Fundamentales

1. **Preservación Funcional Innegociable**:
   - Todo cambio debe respetar los contratos de los endpoints existentes.
   - La regla comercial de redondeo a centenas ($100 COP) y la matriz de permisos RBAC deben mantenerse intactas.
2. **Pruebas Automatizadas Obligatorias**:
   - Cualquier nueva funcionalidad o corrección de errores debe incluir pruebas unitarias o de integración en `tests/`.
   - Todas las pruebas deben superar el 100%: `pytest -v`.
3. **Seguridad por Diseño**:
   - Nunca expongas credenciales o secretos en el código fuente.
   - Aplica validación estricta de esquemas Pydantic y tipado estricto.

---

## 🛠️ Flujo de Trabajo

1. Haz un Fork del repositorio.
2. Crea una rama descriptiva:
   ```bash
   git checkout -b feature/nombre-funcionalidad
   # o
   git checkout -b fix/nombre-error
   ```
3. Realiza tus cambios y ejecuta las pruebas:
   ```bash
   pytest -v
   ```
4. Envía tu Pull Request con un resumen claro del cambio y su justificación técnica.

---

## 📄 Licencia
Al contribuir, aceptas que tus contribuciones se licencien bajo la [Licencia MIT](LICENSE).
