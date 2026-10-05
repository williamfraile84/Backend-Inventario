import io
import csv
import json
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import require_permission, get_current_user
from app.models.schemas import AuditoriaListResponse, AuditoriaResponse
from app.services.auditoria_service import AuditoriaService

router = APIRouter(prefix="/auditoria", tags=["Auditoría del Sistema"])


@router.get("", response_model=AuditoriaListResponse)
def get_audit_logs(
    modulo: Optional[str] = Query(None, description="Filtrar por módulo del sistema"),
    accion: Optional[str] = Query(None, description="Filtrar por tipo de acción"),
    usuario_id: Optional[int] = Query(None, description="Filtrar por ID de usuario"),
    search: Optional[str] = Query(None, description="Búsqueda libre por texto"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(require_permission("can_view_audit")),
    db: Session = Depends(get_db),
):
    """Consulta registros del log inmutable de auditoría del sistema."""
    total, items = AuditoriaService.consultar_logs(
        db=db,
        modulo=modulo,
        accion=accion,
        usuario_id=usuario_id,
        search=search,
        limit=limit,
        offset=offset,
    )

    formatted_items = []
    for item in items:
        detalles_dict = None
        if item.detalles:
            try:
                detalles_dict = json.loads(item.detalles) if isinstance(item.detalles, str) else item.detalles
            except Exception:
                detalles_dict = {"raw": str(item.detalles)}
        
        formatted_items.append(
            AuditoriaResponse(
                id=item.id,
                usuario_id=item.usuario_id,
                usuario_nombre=item.usuario_nombre,
                usuario_rol=item.usuario_rol,
                accion=item.accion,
                modulo=item.modulo,
                entidad=item.entidad,
                entidad_id=str(item.entidad_id) if item.entidad_id is not None else None,
                detalles=detalles_dict,
                ip_address=item.ip_address,
                creado_en=item.creado_en,
            )
        )

    return {
        "total": total,
        "items": formatted_items,
    }


@router.get("/total-count")
def get_audit_total_count(
    current_user: Dict[str, Any] = Depends(require_permission("can_view_audit")),
    db: Session = Depends(get_db),
):
    """Retorna el conteo total de logs en auditoría de forma rápida y optimizada."""
    total = AuditoriaService.consultar_total_logs(db=db)
    return {"total": total}


@router.get("/export-csv")
def export_audit_csv(
    modulo: Optional[str] = Query(None),
    accion: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(require_permission("can_view_audit")),
    db: Session = Depends(get_db),
):
    """Exporta el historial de auditoría en formato CSV para reportes de trazabilidad."""
    _, items = AuditoriaService.consultar_logs(
        db=db,
        modulo=modulo,
        accion=accion,
        search=search,
        limit=5000,
        offset=0,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID",
        "Fecha y Hora (UTC)",
        "Usuario ID",
        "Usuario",
        "Rol",
        "Modulo",
        "Accion",
        "Entidad",
        "Entidad ID",
        "IP Origen",
        "Detalles JSON"
    ])

    for log in items:
        writer.writerow([
            log.id,
            log.creado_en.isoformat() if log.creado_en else "",
            log.usuario_id or "",
            log.usuario_nombre or "Sistema",
            log.usuario_rol or "",
            log.modulo,
            log.accion,
            log.entidad or "",
            log.entidad_id or "",
            log.ip_address or "",
            log.detalles or ""
        ])

    csv_data = output.getvalue()
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"auditoria_{date_str}.csv"

    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

