-- ==========================================================
-- FocusFeed v4: cerrar la base antes de publicar la llave anon
-- ==========================================================
--
-- POR QUÉ
--   T2 pone el login con Google en el navegador. Para eso la llave `anon`
--   de Supabase tiene que viajar al frontend, y deja de ser secreta.
--   v1 dejó GRANT ALL a anon y v2 no lo limpia del todo:
--     · los *_v1_backup quedan en `public`, sin RLS y con permisos de anon
--       (heredados del ALTER DEFAULT PRIVILEGES de v1). Ahí está el perfil.
--     · `authenticated` puede leer `feeds` e `items` completos por REST:
--       cualquier cuenta listaría las fuentes de todos.
--     · las funciones RPC se pueden ejecutar con la llave anon.
--     · user_profile.id sigue con DEFAULT 1: la segunda cuenta que guarde
--       perfil choca con la llave primaria.
--     · user_profile.user_name trae un nombre propio como default.
--
-- EL PRINCIPIO
--   El navegador solo usa Supabase para iniciar sesión. Toda lectura y
--   escritura de datos pasa por el backend con service_role, que filtra
--   por user_id. Así que anon y authenticated no necesitan ningún permiso
--   sobre tablas ni funciones. RLS queda encendido como segunda barrera.
--
-- PRECONDICIONES
--   1. supabase_schema_v2.sql ya corrió (este archivo lo verifica).
--   2. Vercel tiene SUPABASE_SERVICE_ROLE_KEY. Sin ella el backend usa la
--      llave anon y, después de esto, no podría leer nada.
--   3. Hay respaldo con pg_dump. Ver docs/runbook_T2.md.
--
-- Idempotente: se puede correr más de una vez.
-- Rollback: supabase_rollback_v4.sql
-- ==========================================================

BEGIN;

-- ----------------------------------------------------------
-- 0. Precondición: v2 aplicado
-- ----------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.subscriptions') IS NULL
       OR to_regclass('public.user_items') IS NULL THEN
        RAISE EXCEPTION 'Falta v2: corre supabase_schema_v2.sql antes que este archivo';
    END IF;
END $$;

-- ----------------------------------------------------------
-- 1. Incluye v3 (caché condicional de feeds), pendiente en producción
-- ----------------------------------------------------------
ALTER TABLE public.feeds ADD COLUMN IF NOT EXISTS etag TEXT;
ALTER TABLE public.feeds ADD COLUMN IF NOT EXISTS last_modified TEXT;

-- ----------------------------------------------------------
-- 2. Respaldos fuera del alcance de la API
--    El esquema `private` no está expuesto por PostgREST.
-- ----------------------------------------------------------
-- Si `private` ya existe con objetos que no son estos respaldos, es de
-- alguien más (helpers de Supabase, otra migración): no se toca.
DO $$
BEGIN
    IF to_regnamespace('private') IS NOT NULL AND (
        EXISTS (SELECT 1 FROM pg_class
                 WHERE relnamespace = to_regnamespace('private')
                   AND relname NOT IN ('items_v1_backup', 'feeds_v1_backup', 'user_profile_v1_backup'))
        OR EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace = to_regnamespace('private'))
    ) THEN
        RAISE EXCEPTION 'El esquema private ya tiene otros objetos: revisa a mano antes de seguir';
    END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS private;
REVOKE ALL ON SCHEMA private FROM PUBLIC, anon, authenticated;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['items_v1_backup', 'feeds_v1_backup', 'user_profile_v1_backup']
    LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            IF to_regclass('private.' || t) IS NOT NULL THEN
                RAISE EXCEPTION 'Ya existe private.%: revisa a mano antes de seguir', t;
            END IF;
            EXECUTE format('ALTER TABLE public.%I SET SCHEMA private', t);
        END IF;
        IF to_regclass('private.' || t) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON private.%I FROM PUBLIC, anon, authenticated', t);
        END IF;
    END LOOP;
END $$;

-- ----------------------------------------------------------
-- 3. anon y authenticated sin permisos sobre tablas
-- ----------------------------------------------------------
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;

-- Lo que se cree después tampoco nace abierto (deshace el default de v1).
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;
-- Postgres da EXECUTE a PUBLIC en toda función nueva, y un default por esquema
-- no lo puede quitar: tiene que ser global. Aplica a lo que cree el rol que
-- corre este archivo (postgres en el SQL editor). Sin esto, una RPC nueva con
-- SECURITY DEFINER sería ejecutable con la llave anon.
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- El backend sigue con todo.
GRANT USAGE ON SCHEMA public TO service_role;
GRANT ALL ON ALL TABLES    IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- Las políticas de lectura abierta de v2 ya no tienen uso: sin GRANT no
-- alcanzan a nadie. Se quitan para que nadie las reactive por accidente.
DROP POLICY IF EXISTS read_feeds ON public.feeds;
DROP POLICY IF EXISTS read_items ON public.items;

-- RLS encendido también en el corpus, sin políticas: nadie fuera de
-- service_role lee filas aunque alguien vuelva a dar un GRANT.
ALTER TABLE public.feeds         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.items         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_items    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_usage      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_profile  ENABLE ROW LEVEL SECURITY;

-- ----------------------------------------------------------
-- 4. Funciones RPC: solo el backend
-- ----------------------------------------------------------
-- Todas, no solo las tres de v2: cualquier RPC creada a mano en el
-- dashboard también quedaría expuesta con la llave anon.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO service_role;

-- ----------------------------------------------------------
-- 5. user_profile listo para más de una cuenta
-- ----------------------------------------------------------
-- id deja de valer 1 por default: pasa a identidad, arrancando después
-- del máximo actual para no chocar con la fila existente.
DO $$
DECLARE siguiente BIGINT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'user_profile'
           AND column_name = 'id' AND is_identity = 'YES'
    ) THEN
        SELECT COALESCE(MAX(id), 0) + 1 INTO siguiente FROM public.user_profile;
        ALTER TABLE public.user_profile ALTER COLUMN id DROP DEFAULT;
        EXECUTE format(
            'ALTER TABLE public.user_profile ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (START WITH %s)',
            siguiente);
    END IF;
END $$;

-- Una cuenta nueva no hereda el nombre de nadie.
ALTER TABLE public.user_profile ALTER COLUMN user_name DROP DEFAULT;

COMMIT;

-- ==========================================================
-- Verificación después de correr: docs/runbook_T2.md, paso 5
-- (una sola consulta; el SQL editor solo muestra la última).
-- ==========================================================
