-- FocusFeed v5: lo que JEV aún no califica arranca en 40, no en 70.
-- Datos del 17-oct: de 721 items calificados en una semana, 707 sacaron menos de 40 y ninguno 60 o más.
-- Con 70 por defecto todo lo sin calificar ganaba a todo lo calificado. Con 40, lo que JEV aprobó
-- (40 o más) gana a lo desconocido, y lo desconocido gana a lo que JEV descartó (menos de 40).
-- Mantiene el decaimiento de v4. El score guardado no cambia.
-- Sin fecha: tope completo (40), para que no suba por accidente.
-- Idempotente: CREATE OR REPLACE, se puede correr más de una vez.
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
                LEAST(40.0, 2.0 * GREATEST(0.0, EXTRACT(EPOCH FROM (now() - i.published_at)) / 86400.0)),
                40.0)
        END DESC NULLS LAST,
        i.published_at DESC
     LIMIT p_limit;
$$;

