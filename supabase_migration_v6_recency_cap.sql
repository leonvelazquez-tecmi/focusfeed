-- FocusFeed v6: el castigo por antigüedad llega a 60 puntos (30 días), no a 40.
-- Datos del 17-oct tras aplicar v5: items de agosto con scores viejos de 83 a 86 seguían en el
-- top 10 porque 86 - 40 = 46 le ganaba al 40 de lo sin calificar. Con tope 60, todo lo de más de
-- un mes con score de 99 o menos queda por debajo de lo desconocido.
-- Mantiene el 40 por defecto de v5. El score guardado no cambia.
-- Sin fecha: tope completo (60), para que no suba por accidente.
-- Idempotente: CREATE OR REPLACE, se puede correr más de una vez.
-- Espejo local: app/storage.py, rama SQLite de fetch_items.

CREATE OR REPLACE, se puede correr más de una vez.
-- Espejo local: app/storage.py, rama SQLite de fetch_items.

CREATE OR REPLACE FUNCTION public.get_user_feed(
    p_user_id UUID,
    p_tab TEXT DEFAULT 'curated',
    p_type TEXT DEFAULT 'all',
    p_limit INTEGER DEFAULT 120
)
RETURNS TABLE (
    id BIGINT, feed_id BIGINT, guid TEXT, title TEXT, url TEXT, author TEXT,
    published_at TIMESTAMPTZ, content_type TEXT, video_id TEXT, thumbnail_url TEXT,
    raw_content TEXT, summary_tldr TEXT, key_takeaways TEXT, topic_tags TEXT,
    ai_processed INTEGER,
    status TEXT, relevance_score INTEGER, curator_note TEXT, user_rating TEXT,
    user_feedback_comment TEXT
)
LANGUAGE sql STABLE AS $$
    SELECT i.id, i.feed_id, i.guid, i.title, i.url, i.author,
           i.published_at, i.content_type, i.video_id, i.thumbnail_url,
           i.raw_content, i.summary_tldr, i.key_takeaways, i.topic_tags,
           i.ai_processed,
           COALESCE(ui.status, 'inbox')      AS status,
           ui.relevance_score,
           ui.curator_note,
           COALESCE(ui.user_rating, 'none')  AS user_rating,
           ui.user_feedback_comment
      FROM public.items i
      JOIN public.subscriptions s
        ON s.feed_id = i.feed_id AND s.user_id = p_user_id
      LEFT JOIN public.user_items ui
        ON ui.item_id = i.id AND ui.user_id = p_user_id
     WHERE (p_type = 'all' OR i.content_type = p_type)
       AND (
            (p_tab = 'curated'  AND COALESCE(ui.status,'inbox') <> 'archived')
         OR (p_tab = 'inbox'    AND COALESCE(ui.status,'inbox') = 'inbox')
         OR (p_tab = 'reading'  AND ui.status = 'reading')
         OR (p_tab = 'archived' AND ui.status = 'archived')
       )
     ORDER BY
        CASE WHEN p_tab = 'curated' THEN
            COALESCE(ui.relevance_score, 40)
            - COALESCE(
                LEAST(60.0, 2.0 * GREATEST(0.0, EXTRACT(EPOCH FROM (now() - i.published_at)) / 86400.0)),
                60.0)
        END DESC NULLS LAST,
        i.published_at DESC
     LIMIT p_limit;
$$;

