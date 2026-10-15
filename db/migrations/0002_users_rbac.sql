-- ============================================================
-- Migración 0002 — CORE-USERS-01 (R0)  [revisada: auditoría RLS + objetos preexistentes]
--
-- OBJETOS NUEVOS de R0 (los crea esta migración y el rollback los elimina):
--   * public.profile_audit_log            (tabla de auditoría de usuarios)
--   * idx_profile_audit_target / _actor   (índices de esa tabla)
--   * idx_profiles_department             (índice sobre profiles.department_id:
--                                          NO existe en prod -> lo crea R0)
--
-- OBJETOS PREEXISTENTES en producción (NO los crea R0; las sentencias de abajo
-- son NO-OP en prod y solo sirven para entornos locales/fresh; el rollback NO los toca):
--   * public.profiles.department_id       (ya existe en Supabase prod)
--   * profiles_department_id_fkey (FK)    (ya existe en prod)
--   * public.profiles.branch_id           (ya existe en prod)
--
-- PENDIENTE DE APROBACIÓN. NO ejecutar en Supabase productivo.
-- ADITIVA, NULLABLE e IDEMPOTENTE (re-ejecutable sin error). No modifica datos.
--
-- Rol de conexión del backend: debe conectarse con un rol PRIVILEGIADO (p.ej. el
-- service_role de Supabase / el rol dueño en Render), que NO es anon/authenticated.
-- Ese rol omite RLS y conserva el acceso de escritura administrativa controlada.
-- La API REST (PostgREST), que usa anon/authenticated, queda SIN acceso a la auditoría.
-- ============================================================

BEGIN;

-- 1) profiles.department_id (PREEXISTENTE en prod -> IF NOT EXISTS lo hace no-op).
ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS department_id uuid NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'profiles_department_id_fkey'
          AND conrelid = 'public.profiles'::regclass
    ) THEN
        ALTER TABLE public.profiles
            ADD CONSTRAINT profiles_department_id_fkey
            FOREIGN KEY (department_id) REFERENCES public.departments(id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- Índice de apoyo (no-op si ya existe).
CREATE INDEX IF NOT EXISTS idx_profiles_department
    ON public.profiles (department_id);

-- 2) Auditoría de usuarios (NUEVA de R0).
CREATE TABLE IF NOT EXISTS public.profile_audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id uuid NULL,
    actor_role text NULL,
    actor_organization_id uuid NULL,
    actor_company_id uuid NULL,
    target_user_id uuid NULL,
    target_organization_id uuid NULL,
    target_company_id uuid NULL,
    action text NOT NULL,
    field text NOT NULL,
    old_value text NULL,
    new_value text NULL,
    result text NOT NULL DEFAULT 'ok',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_profile_audit_target
    ON public.profile_audit_log (target_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_profile_audit_actor
    ON public.profile_audit_log (actor_id, created_at DESC);

-- 2.a) SEGURIDAD (RLS + permisos mínimos). En Supabase las tablas nuevas de
--      'public' pueden heredar permisos de anon/authenticated: hay que cortarlo.
ALTER TABLE public.profile_audit_log ENABLE ROW LEVEL SECURITY;

-- Quitar cualquier privilegio directo de los roles expuestos por PostgREST.
-- (Guardado por existencia de rol para que corra también en entornos sin Supabase.)
REVOKE ALL ON public.profile_audit_log FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON public.profile_audit_log FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON public.profile_audit_log FROM authenticated';
    END IF;
    -- El backend (service_role) conserva acceso administrativo (y además omite RLS).
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        EXECUTE 'GRANT SELECT, INSERT ON public.profile_audit_log TO service_role';
    END IF;
END $$;

-- Con RLS habilitado y SIN políticas para anon/authenticated, aunque tuvieran
-- privilegios de tabla no verían/escribirían filas. La doble barrera (REVOKE + RLS)
-- es intencional. NO se crean políticas para anon/authenticated.

COMMIT;

-- ============================================================
-- Rollback (si fuese necesario): elimina SOLO lo NUEVO de R0
-- (profile_audit_log + sus índices + idx_profiles_department).
-- NO toca profiles.department_id / su FK / profiles.branch_id, porque son
-- PREEXISTENTES en producción (no fueron creados por R0).
-- BEGIN;
-- DROP INDEX IF EXISTS public.idx_profile_audit_actor;
-- DROP INDEX IF EXISTS public.idx_profile_audit_target;
-- DROP TABLE IF EXISTS public.profile_audit_log;
-- DROP INDEX IF EXISTS public.idx_profiles_department;   -- creado por R0 (no existía en prod)
-- COMMIT;
-- ============================================================
