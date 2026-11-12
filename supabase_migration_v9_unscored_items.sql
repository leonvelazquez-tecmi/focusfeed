-- FocusFeed v9: función para encontrar lo que a un usuario le falta calificar.
-- Por qué: la calificación solo miraba los 160 items más recientes de la bandeja y
-- calificaba 40 por llamada. Lo de atrás nunca se calificaba y entraba a "Para ti" con
-- 20, que es más que el score típico de JEV. Datos del 12-nov en producción: 341 de 865
-- items de la última semana sin score.
-- Solo lectura: no toca tablas ni datos. Idempotente: CREATE OR REPLACE.
-- Une items con las suscripciones del usuario (igual que get_user_feed), así que no
-- puede devolver nada de otra persona.
-- Solo cuentan los últimos 30 días: en "Para ti" lo más viejo se ordena solo por fecha (v8).
-- Espejo local: app/storage.py, fetch_unscored_items.
-- Rollback: supabase_rollback_v9_unscored_items.sql. Sin la función, el código vuelve
-- solo al comportamiento anterior (ventana de 160), no falla.

CREATE OR REPLACE FUNCTION public.get_unscored_items(
    p_user_id UUID,
    p_days INTEGER DEFAULT 30,
    p_limit INTEGER DEFAULT 120
)
RETURNS TABLE (
    id BIGINT, guid TEXT, title TEXT, author TEXT, content_type TEXT,
    summary_tldr TEXT, raw_content TEXT, topic_tags TEXT
)
LANGUAGE sql STABLE AS $$
    SELECT i.id, i.guid, i.title, i.author, i.content_type, i.summary_tldr,
           left(i.raw_content, 800), i.topic_tags
      FROM public.items i
      JOIN public.subscriptions s
        ON s.feed_id = i.feed_id AND s.user_id = p_user_id
      LEFT JOIN public.user_items ui
        ON ui.item_id = i.id AND ui.user_id = p_user_id
     WHERE ui.relevance_score IS NULL
       AND COALESCE(ui.status, 'inbox') = 'inbox'
       AND i.published_at > now() - make_interval(days => p_days)
     ORDER BY i.published_at DESC
     LIMIT p_limit;
$$;
