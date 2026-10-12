-- ==========================================================
-- Rollback de supabase_migration_v4_rls.sql
-- ==========================================================
-- Deja la base como la dejó supabase_schema_v2.sql: mismos GRANT a
-- authenticated, mismas políticas de lectura, respaldos de vuelta en public.
--
-- No restaura el GRANT ALL a anon de v1: eso era el hueco, no un estado
-- al que valga la pena volver. Si hace falta v1 completo, se restaura el
-- pg_dump (ver docs/runbook_T2.md).
--
-- Se queda igual, a propósito:
--   · las columnas etag y last_modified de feeds (v3, inofensivas)
--   · la identidad de user_profile.id (volver a DEFAULT 1 rompe la
--     segunda cuenta que guarde perfil)
--
-- PRECONDICIÓN: el PR B (login) nunca se desplegó, o ya se revirtió y la
-- llave anon/publishable se rotó en Supabase. Con la llave pública en el
-- frontend, cualquiera que se registre volvería a leer el corpus completo.
-- El script solo puede verificar una parte (que no haya cuentas en
-- auth.users); la otra es tuya.
-- Los respaldos vuelven a public pero con los permisos que v4 les quitó:
-- siguen cerrados.
-- También se queda la identidad de user_profile.id y los default
-- privileges de service_role.
-- ==========================================================

BEGIN;

-- Revertir v4 vuelve a abrir el corpus (todos los feeds y sus URLs) a
-- cualquier cuenta con sesión. Con cuentas reales eso es una filtración.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM auth.users) THEN
        RAISE EXCEPTION 'Ya hay cuentas en auth.users: revertir v4 dejaría a cualquiera listar todas las fuentes. No hay vuelta atrás automática; mándale el error a Claude';
    END IF;
END $$;

-- Respaldos de vuelta a public
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['items_v1_backup', 'feeds_v1_backup', 'user_profile_v1_backup']
    LOOP
        IF to_regclass('ff_respaldos.' || t) IS NOT NULL THEN
            IF to_regclass('public.' || t) IS NOT NULL THEN
                RAISE EXCEPTION 'Existen public.% y ff_respaldos.%: revisa a mano cuál conservar', t, t;
            END IF;
            EXECUTE format('ALTER TABLE ff_respaldos.%I SET SCHEMA public', t);
        END IF;
    END LOOP;
END $$;

-- El esquema es solo de v4. Sin CASCADE: si alguien le metió algo, falla.
DROP SCHEMA IF EXISTS ff_respaldos;

-- Permisos de v2
ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC;
GRANT SELECT ON public.feeds TO authenticated;
GRANT SELECT ON public.items TO authenticated;
GRANT ALL ON public.subscriptions, public.user_items, public.user_profile TO authenticated;
GRANT SELECT ON public.ai_usage TO authenticated;

DROP POLICY IF EXISTS read_feeds ON public.feeds;
CREATE POLICY read_feeds ON public.feeds FOR SELECT USING (true);
DROP POLICY IF EXISTS read_items ON public.items;
CREATE POLICY read_items ON public.items FOR SELECT USING (true);

-- Funciones como las dejó v2 (EXECUTE para PUBLIC, el default de Postgres).
-- Solo las tres de FocusFeed: si había otras en public, v4 les quitó
-- EXECUTE y aquí no se les devuelve a ciegas.
GRANT EXECUTE ON FUNCTION public.get_user_feed(UUID, TEXT, TEXT, INTEGER) TO PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_user_counts(UUID)                    TO PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_unanalyzed_items(INTEGER)            TO PUBLIC;

COMMIT;

-- Verificación: debe listar los GRANT de v2 para authenticated
--   SELECT table_name, privilege_type FROM information_schema.role_table_grants
--    WHERE table_schema = 'public' AND grantee = 'authenticated' ORDER BY 1, 2;
