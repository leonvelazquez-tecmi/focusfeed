-- ==========================================================
-- Limpiar antes de restaurar el respaldo de T2
-- ==========================================================
-- BORRA DATOS. Solo se corre como parte de "Restaurar el respaldo" en
-- docs/runbook_T2.md, y nunca solo en el SQL editor: va en la MISMA
-- transacción que el restore (psql --single-transaction -f este -f
-- restore.sql). Si el restore falla, esta limpieza también se revierte.
--
-- Solo vale antes de que alguien entre con Google (PR B). Después, el
-- respaldo ya no tiene los datos de esas cuentas: el script se niega.
--
-- Por qué existe: pg_restore --clean no puede borrar items ni feeds
-- mientras subscriptions y user_items (que no existen en un respaldo de
-- v1) les apunten con llave foránea. Se queda a medias y deja una base
-- híbrida. Esto quita todo lo de FocusFeed para que el respaldo entre
-- limpio.
-- ==========================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM auth.users) THEN
        RAISE EXCEPTION 'Ya hay cuentas en auth.users: restaurar el respaldo de antes de T2 borraría sus datos. No hay vuelta atrás automática; mándale el error a Claude';
    END IF;
END $$;

DROP TABLE IF EXISTS private.items_v1_backup, private.feeds_v1_backup, private.user_profile_v1_backup;
-- Sin CASCADE: si private tiene algo más, esto falla y no se borra nada.
DROP SCHEMA IF EXISTS private;

DROP FUNCTION IF EXISTS public.get_user_feed(UUID, TEXT, TEXT, INTEGER);
DROP FUNCTION IF EXISTS public.get_user_counts(UUID);
DROP FUNCTION IF EXISTS public.get_unanalyzed_items(INTEGER);

-- v4 quitó el EXECUTE por default de las funciones nuevas, y el restore no
-- trae default privileges. Sin esto, las RPC restauradas nacerían sin
-- EXECUTE y el backend vería el feed vacío. v4 lo vuelve a quitar al correr.
ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC;

DROP TABLE IF EXISTS
    public.user_items,
    public.subscriptions,
    public.ai_usage,
    public.items_v1_backup,
    public.feeds_v1_backup,
    public.user_profile_v1_backup,
    public.items,
    public.feeds,
    public.user_profile
CASCADE;
