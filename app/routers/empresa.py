from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Header, Query, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import require_permission, get_current_user
from app.models.empresa import Empresa, EmpresaAtributo
from app.models.schemas import (
    EmpresaCreate,
    EmpresaUpdate,
    EmpresaResponse,
    EmpresaStatusResponse
)
from app.services.auditoria_service import AuditoriaService

router = APIRouter(prefix="/empresa", tags=["Empresa y Configuración"])


def seed_default_empresa_if_empty(db: Session) -> Optional[Empresa]:
    """Crea una empresa por defecto si la base de datos no tiene ninguna registrada."""
    empresa = db.query(Empresa).first()
    if not empresa:
        empresa = Empresa(
            nombre="FRUVER POS",
            nit="900.000.000-1",
            direccion="Calle Principal # 10 - 20",
            telefono="300 000 0000",
            email="pos@fruver.com",
            activo=True
        )
        db.add(empresa)
        db.commit()
        db.refresh(empresa)

        # Atributos por defecto en 3NF
        attrs = [
            EmpresaAtributo(empresa_id=empresa.id, clave="margen_defecto", valor="30"),
            EmpresaAtributo(empresa_id=empresa.id, clave="base_redondeo", valor="100"),
            EmpresaAtributo(empresa_id=empresa.id, clave="moneda", valor="COP"),
        ]
        db.add_all(attrs)
        db.commit()
        db.refresh(empresa)
    return empresa


def sync_empresa_atributos(db: Session, empresa_id: int, campos_extra: Optional[List[Any]]):
    """Sincroniza la lista de atributos dinámicos en la tabla 3NF empresa_atributos."""
    if campos_extra is None:
        return
    db.query(EmpresaAtributo).filter(EmpresaAtributo.empresa_id == empresa_id).delete()
    for attr in campos_extra:
        if isinstance(attr, dict) and attr.get("clave") and attr.get("valor") is not None:
            db.add(EmpresaAtributo(
                empresa_id=empresa_id,
                clave=str(attr["clave"]).strip(),
                valor=str(attr["valor"]).strip()
            ))
    db.commit()


@router.get("/status", response_model=EmpresaStatusResponse)
def get_empresa_status(
    x_empresa_id: Optional[str] = Header(None, alias="X-Empresa-Id"),
    empresa_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Retorna el estado de configuración de empresas, indicando la empresa activa,
    el total de empresas registradas y la lista de empresas disponibles.
    """
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre.asc()).all()
    if not empresas:
        default_emp = seed_default_empresa_if_empty(db)
        empresas = [default_emp] if default_emp else []

    target_id = empresa_id
    if target_id is None and x_empresa_id:
        try:
            target_id = int(x_empresa_id)
        except (ValueError, TypeError):
            pass

    empresa_activa = None
    if target_id:
        empresa_activa = next((e for e in empresas if e.id == target_id), None)

    if not empresa_activa and empresas:
        empresa_activa = empresas[0]

    return {
        "configurada": len(empresas) > 0,
        "empresa": empresa_activa,
        "total_empresas": len(empresas),
        "empresas": empresas,
    }


@router.get("", response_model=List[EmpresaResponse])
@router.get("/todas", response_model=List[EmpresaResponse])
def get_todas_las_empresas(db: Session = Depends(get_db)):
    """Lista todas las empresas activas registradas en el sistema."""
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre.asc()).all()
    if not empresas:
        default_emp = seed_default_empresa_if_empty(db)
        empresas = [default_emp] if default_emp else []
    return empresas


@router.get("/{empresa_id}", response_model=EmpresaResponse)
def get_empresa_by_id(empresa_id: int, db: Session = Depends(get_db)):
    """Obtiene el detalle de una empresa por ID."""
    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa no encontrada.")
    return empresa


@router.post("", response_model=EmpresaResponse, status_code=status.HTTP_201_CREATED)
def create_or_save_empresa(
    payload: EmpresaCreate,
    request: Request,
    current_user: Dict[str, Any] = Depends(require_permission("can_manage_users")),
    db: Session = Depends(get_db),
):
    """Crea una nueva empresa con atributos configurados en 3NF."""
    existente = db.query(Empresa).filter(Empresa.nit == payload.nit.strip()).first()
    if existente:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ya existe una empresa registrada con el NIT '{payload.nit}'."
        )

    empresa = Empresa(
        nombre=payload.nombre.strip(),
        nit=payload.nit.strip(),
        direccion=payload.direccion,
        telefono=payload.telefono,
        email=payload.email,
        logo_path=payload.logo_path,
        activo=True
    )
    db.add(empresa)
    db.commit()
    db.refresh(empresa)

    if payload.campos_extra:
        sync_empresa_atributos(db, empresa.id, payload.campos_extra)
        db.refresh(empresa)

    AuditoriaService.registrar_evento(
        db=db,
        accion="CREAR_EMPRESA",
        modulo="EMPRESA",
        usuario=current_user,
        entidad="Empresa",
        entidad_id=empresa.id,
        detalles={"nombre": empresa.nombre, "nit": empresa.nit},
        request=request
    )

    return empresa


@router.put("/{empresa_id}", response_model=EmpresaResponse)
def update_empresa(
    empresa_id: int,
    payload: EmpresaUpdate,
    request: Request,
    current_user: Dict[str, Any] = Depends(require_permission("can_manage_users")),
    db: Session = Depends(get_db),
):
    """Actualiza los datos y atributos configurados de una empresa."""
    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa no encontrada.")

    data = payload.model_dump(exclude_unset=True)
    campos_extra = data.pop("campos_extra", None)

    for field, val in data.items():
        setattr(empresa, field, val)

    db.commit()

    if campos_extra is not None:
        sync_empresa_atributos(db, empresa.id, campos_extra)

    db.refresh(empresa)

    AuditoriaService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_EMPRESA",
        modulo="EMPRESA",
        usuario=current_user,
        entidad="Empresa",
        entidad_id=empresa.id,
        detalles=data,
        request=request
    )

    return empresa

