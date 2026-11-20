import json
from typing import Optional, Dict, Any, Tuple, List
from fastapi import Request
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_, desc, func
from app.models.auditoria import Auditoria, AuditoriaCambio
from app.models.usuario import Usuario


class AuditoriaService:
    """
    Servicio empresarial de auditoría inmutable en 3NF.
    Registra eventos globales, cambios a nivel de atributo y consultas paginadas con exportación.
    """

    @staticmethod
    def get_client_ip(request: Optional[Request]) -> Optional[str]:
        if not request:
            return None
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return None

    @staticmethod
    def get_user_agent(request: Optional[Request]) -> Optional[str]:
        if not request:
            return None
        return request.headers.get("User-Agent")

    @classmethod
    def registrar_evento(
        cls,
        db: Session,
        accion: str,
        modulo: str,
        usuario: Optional[Any] = None,
        username: Optional[str] = None,
        entidad: Optional[str] = None,
        entidad_id: Optional[int] = None,
        detalles: Optional[Dict[str, Any]] = None,
        request: Optional[Request] = None,
    ) -> Auditoria:
        """Registra un evento inmutable en 'auditorias' y sus deltas en 'auditoria_cambios'."""
        ip = cls.get_client_ip(request)
        ua = cls.get_user_agent(request)

        user_id = None
        user_name = username or "Sistema"

        if usuario:
            if isinstance(usuario, dict):
                user_id = usuario.get("id")
                user_name = usuario.get("username") or usuario.get("full_name") or user_name
            elif hasattr(usuario, "id"):
                user_id = getattr(usuario, "id")
                user_name = getattr(usuario, "username", getattr(usuario, "nombre_completo", user_name))

        detalles_json = json.dumps(detalles, ensure_ascii=False) if detalles else None

        entry = Auditoria(
            usuario_id=user_id,
            username=user_name,
            modulo=modulo.upper(),
            accion=accion.upper(),
            entidad=entidad,
            entidad_id=entidad_id,
            ip_origen=ip,
            user_agent=ua[:255] if ua else None,
            detalles=detalles_json,
        )
        db.add(entry)
        db.flush()

        # Detalle de cambios campo a campo (3NF estricta)
        if detalles and isinstance(detalles, dict):
            for campo, valor in detalles.items():
                val_str = json.dumps(valor, ensure_ascii=False) if isinstance(valor, (dict, list)) else str(valor) if valor is not None else None
                cambio = AuditoriaCambio(
                    auditoria_id=entry.id,
                    campo=str(campo),
                    valor_anterior=None,
                    valor_nuevo=val_str,
                )
                db.add(cambio)

        db.commit()
        db.refresh(entry)
        return entry

    @staticmethod
    def consultar_logs(
        db: Session,
        modulo: Optional[str] = None,
        accion: Optional[str] = None,
        usuario_id: Optional[int] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[int, List[Auditoria]]:
        """Consulta registros inmutables con filtros y eager loading de deltas."""
        base_query = db.query(Auditoria)

        if modulo:
            base_query = base_query.filter(Auditoria.modulo == modulo.upper())
        if accion:
            base_query = base_query.filter(Auditoria.accion == accion.upper())
        if usuario_id is not None:
            base_query = base_query.filter(Auditoria.usuario_id == usuario_id)
        if search:
            pattern = f"%{search}%"
            base_query = base_query.filter(
                or_(
                    Auditoria.username.ilike(pattern),
                    Auditoria.modulo.ilike(pattern),
                    Auditoria.accion.ilike(pattern),
                    Auditoria.entidad.ilike(pattern),
                )
            )

        total = base_query.count()
        items = (
            base_query.options(selectinload(Auditoria.cambios))
            .order_by(desc(Auditoria.id))
            .offset(offset)
            .limit(limit)
            .all()
        )
        return total, items

    @staticmethod
    def consultar_total_logs(db: Session) -> int:
        """Conteo ultrarrápido sin joins para badges y estadísticas."""
        return db.query(func.count(Auditoria.id)).scalar() or 0

