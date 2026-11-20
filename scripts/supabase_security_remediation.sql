-- ==============================================================================
-- REGLAS DE ENDURECIMIENTO Y REMEDIACIÓN DE SEGURIDAD (SUPABASE SECURITY ADVISOR)
-- INVENTARIO FRUVER - SUPABASE POSTGRESQL HARDENING
-- ==============================================================================

-- 1. Habilitar Row Level Security (RLS) en tablas sensibles si se exponen vía PostgREST
-- FastAPI se conecta mediante postgres / service_role (DATABASE_URL), el cual tiene BYPASSRLS por defecto.
-- Habilitar RLS previene que roles anónimos o públicos lean tablas directamente por la API REST de Supabase.

ALTER TABLE IF EXISTS public.usuarios ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.sesiones ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.empresas ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auditoria ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.productos ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.facturas ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.factura_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.factura_plantillas ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.alias_productos ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.catalogo_unidades ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.catalogo_empaques ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.catalogo_conceptos ENABLE ROW LEVEL SECURITY;

-- 2. Asegurar que las vistas tengan 'security_invoker = true' en PostgreSQL 15+
-- Esto evita avisos de "View defined with SECURITY DEFINER property" en el Security Advisor
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT table_name 
        FROM information_schema.views 
        WHERE table_schema = 'public'
    ) LOOP
        EXECUTE format('ALTER VIEW public.%I SET (security_invoker = true);', r.table_name);
    END LOOP;
END $$;

-- 3. Revocar permisos de ejecución indebida a roles anónimos en funciones del esquema public
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'rls_auto_enable') THEN
        REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM anon;
        REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM authenticated;
        GRANT EXECUTE ON FUNCTION public.rls_auto_enable() TO postgres;
        GRANT EXECUTE ON FUNCTION public.rls_auto_enable() TO service_role;
    END IF;
END $$;
