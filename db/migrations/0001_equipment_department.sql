-- ============================================================
-- Migración 0001 — equipment.department_id
-- CORE Ommeganet / ITAM-02
--
-- Agrega la relación equipment.department_id -> departments.id
-- (el departamento representa el área responsable/propietaria del activo).
--
-- PENDIENTE DE APROBACIÓN. NO ejecutar automáticamente.
-- Es PREREQUISITO del despliegue del backend ITAM-02: debe aplicarse ANTES
-- de desplegar el código (el ORM selecciona equipment.department_id).
--
-- Verificaciones previas sugeridas (solo lectura), antes de aplicar:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema='public' AND table_name='equipment' AND column_name='department_id';  -- debe estar vacío
--   SELECT count(*) FROM public.equipment;        -- impacto
--   SELECT count(*) FROM public.departments;      -- catálogo disponible
--
-- Es aditiva y NULLABLE: preserva todos los activos existentes (quedan con
-- department_id = NULL). No modifica datos.
--
-- IDEMPOTENTE / RE-EJECUTABLE: todas las operaciones usan IF NOT EXISTS o un
-- guard DO/pg_constraint, de modo que volver a aplicarla no produce error
-- "already exists" ni efectos secundarios.
-- ============================================================

BEGIN;

-- 1) Columna (aditiva, nullable) — IF NOT EXISTS la hace repetible.
ALTER TABLE public.equipment
    ADD COLUMN IF NOT EXISTS department_id uuid NULL;

-- 2) FK -> departments. PostgreSQL no soporta ADD CONSTRAINT IF NOT EXISTS,
--    así que la agregamos solo si no existe ya (por nombre, en esta tabla).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'equipment_department_id_fkey'
          AND conrelid = 'public.equipment'::regclass
    ) THEN
        ALTER TABLE public.equipment
            ADD CONSTRAINT equipment_department_id_fkey
            FOREIGN KEY (department_id) REFERENCES public.departments(id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- 3) Índice — IF NOT EXISTS la hace repetible.
CREATE INDEX IF NOT EXISTS idx_equipment_department
    ON public.equipment (department_id);

COMMIT;

-- Rollback (si fuese necesario):
-- BEGIN;
-- DROP INDEX IF EXISTS public.idx_equipment_department;
-- ALTER TABLE public.equipment DROP CONSTRAINT IF EXISTS equipment_department_id_fkey;
-- ALTER TABLE public.equipment DROP COLUMN IF EXISTS department_id;
-- COMMIT;
