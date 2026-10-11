-- ==========================================================
-- Rollback de supabase_schema_v2.sql (de vuelta a v1, una sola cuenta)
-- ==========================================================
-- Úsalo solo si v2 acaba de correr y la app quedó rota. La salida
-- preferida es restaurar el pg_dump (docs/runbook_T2.md); esto es la vía
-- rápida que conserva lo que pasó después de la migración.
--
-- Antes de correrlo:
--   1. Si ya corrió v4, corre primero supabase_rollback_v4.sql.
--   2. El código desplegado tiene que ser uno que lea el esquema v1
--      (anterior a T1). El código actual de main NO funciona con v1.
--   3. Si ya reasignaste tus datos a tu uuid real, cambia el uuid de
--      la línea `dueno` abajo (si no, el script aborta).
--   4. Si alguna vez se publicó la llave anon (PR B en producción), rótala
--      en Supabase antes: v1 le da acceso total.
--
-- Qué hace:
--   · Copia el estado personal del dueño (user_items) de vuelta a items.
--   · Renombra las columnas legacy_* a su nombre original.
--   · Borra subscriptions, user_items y las funciones RPC.
--   · Copia la categoría de cada fuente de subscriptions a feeds.
--   · Conserva ai_usage y los respaldos.
--   · Limitación: v1 muestra todos los feeds de la tabla. Una fuente de la
--     que te diste de baja después de v2 vuelve a aparecer (no se borra:
--     borrar un feed borra sus items).
--   · Aborta si hay datos de otra cuenta o si el uuid del dueño no coincide.
-- ==========================================================

BEGIN;

DO $$
DECLARE
    dueno UUID := '00000000-0000-0000-0000-000000000001';
BEGIN
    IF to_regclass('ff_respaldos.items_v1_backup') IS NOT NULL THEN
        RAISE EXCEPTION 'v4 sigue aplicado: corre supabase_rollback_v4.sql primero';
    END IF;

    -- Si el uuid no coincide, el UPDATE de abajo no copiaría nada y el DROP
    -- borraría todo el estado desde v2 sin rastro.
    -- PL/pgSQL planea el IF completo: si la tabla no existe, un AND no
    -- alcanza a protegerlo. Por eso los IF van anidados.
    IF to_regclass('public.user_items') IS NULL OR to_regclass('public.subscriptions') IS NULL THEN
        RAISE EXCEPTION 'No hay tablas de v2: no hay nada que revertir';
    END IF;

    IF EXISTS (SELECT 1 FROM public.user_items)
       AND NOT EXISTS (SELECT 1 FROM public.user_items WHERE user_id = dueno) THEN
        RAISE EXCEPTION 'Ningún user_item es de %: ¿ya reasignaste a tu uuid real? Cambia la línea dueno', dueno;
    END IF;

    -- v1 es de una sola cuenta. Si hay datos de alguien más, volver a v1
    -- los dejaría legibles con la llave anon. Eso no se hace en automático.
    IF EXISTS (SELECT 1 FROM public.subscriptions WHERE user_id <> dueno) THEN
        RAISE EXCEPTION 'Hay suscripciones de otras cuentas: v1 no puede aislarlas. No hay vuelta atrás automática; mándale el error a Claude';
    END IF;
    IF EXISTS (SELECT 1 FROM public.user_items WHERE user_id <> dueno) THEN
        RAISE EXCEPTION 'Hay estado de lectura de otras cuentas: el DROP lo borraría. No hay vuelta atrás automática; mándale el error a Claude';
    END IF;
    IF EXISTS (SELECT 1 FROM public.user_profile WHERE user_id IS DISTINCT FROM dueno) THEN
        RAISE EXCEPTION 'Hay perfiles de otras cuentas: v1 los dejaría legibles con la llave anon';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'items'
                  AND column_name = 'legacy_status') THEN
        ALTER TABLE public.items RENAME COLUMN legacy_status           TO status;
        ALTER TABLE public.items RENAME COLUMN legacy_user_rating      TO user_rating;
        ALTER TABLE public.items RENAME COLUMN legacy_feedback_comment TO user_feedback_comment;
        ALTER TABLE public.items RENAME COLUMN legacy_feedback_at      TO feedback_at;
        ALTER TABLE public.items RENAME COLUMN legacy_relevance_score  TO relevance_score;
        ALTER TABLE public.items RENAME COLUMN legacy_curator_note     TO curator_note;
    END IF;

    UPDATE public.items i
       SET status                = ui.status,
           user_rating           = ui.user_rating,
           user_feedback_comment = ui.user_feedback_comment,
           feedback_at           = ui.feedback_at,
           relevance_score       = COALESCE(ui.relevance_score, i.relevance_score),
           curator_note          = COALESCE(ui.curator_note, i.curator_note)
      FROM public.user_items ui
     WHERE ui.item_id = i.id AND ui.user_id = dueno;

    -- Items que llegaron después de v2 y nunca tuvieron score del dueño:
    -- v1 les ponía 70 al ingerir. Sin esto quedarían en 0, hasta abajo.
    -- (Incluye los que el dueño guardó antes de que tuvieran score.)
    UPDATE public.items i
       SET relevance_score = 70
     WHERE COALESCE(i.relevance_score, 0) = 0
       AND NOT EXISTS (SELECT 1 FROM public.user_items ui
                        WHERE ui.item_id = i.id AND ui.user_id = dueno
                          AND ui.relevance_score IS NOT NULL);

    -- Después de v2 la categoría de cada fuente vive en subscriptions.
    UPDATE public.feeds f
       SET custom_category = s.custom_category
      FROM public.subscriptions s
     WHERE s.feed_id = f.id AND s.user_id = dueno;

    -- v1 lee el perfil con id = 1
    UPDATE public.user_profile SET id = 1 WHERE user_id = dueno AND id <> 1
       AND NOT EXISTS (SELECT 1 FROM public.user_profile WHERE id = 1);
END $$;

DROP FUNCTION IF EXISTS public.get_user_feed(UUID, TEXT, TEXT, INTEGER);
DROP FUNCTION IF EXISTS public.get_user_counts(UUID);
DROP FUNCTION IF EXISTS public.get_unanalyzed_items(INTEGER);
DROP TABLE IF EXISTS public.user_items;
DROP TABLE IF EXISTS public.subscriptions;

-- Estado de permisos de v1. Inseguro a propósito: es lo que v1 necesita
-- para funcionar con la llave anon. No publiques la llave anon con esto.
DROP POLICY IF EXISTS read_feeds ON public.feeds;
DROP POLICY IF EXISTS read_items ON public.items;
DROP POLICY IF EXISTS own_profile ON public.user_profile;
ALTER TABLE public.feeds        DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.items        DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_profile DISABLE ROW LEVEL SECURITY;
GRANT ALL ON public.feeds, public.items, public.user_profile TO anon, authenticated, service_role;

COMMIT;

-- Verificación:
--   SELECT status, COUNT(*) FROM items GROUP BY 1;   -- debe parecerse al respaldo:
--   SELECT status, COUNT(*) FROM public.items_v1_backup GROUP BY 1;
