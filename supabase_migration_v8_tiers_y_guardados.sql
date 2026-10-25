-- FocusFeed v8: lo guardado sale de "Para ti" y la antigüedad manda por escalones.
-- 1) Guardar (o dar corazón) saca la tarjeta de "Para ti": vive solo en "Guardados".
-- 2) Orden de "Para ti" por escalones de antigüedad: hasta 7 días, hasta 30, más viejo.
--    Un item de un escalón más viejo nunca va antes que uno de un escalón más nuevo.
--    Dentro de los dos primeros, score de JEV menos 2 puntos por día (como v7).
--    En el más viejo, o sin fecha, solo por fecha.
-- Por qué: con v7, 191 videos con score 70 y fecha del 17-ago (69 días) iban antes que videos
-- de septiembre y octubre sin calificar, porque 70 - 60 = 10 le gana a 20 - 2*días en cuanto
-- el item sin calificar pasa de 5 días. Datos del 25-oct en producción.
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
            (p_tab = 'curated'  AND COALESCE(ui.status,'inbox') NOT IN ('archived','reading'))
         OR (p_tab = 'inbox'    AND COALESCE(ui.status,'inbox') = 'inbox')
         OR (p_tab = 'reading'  AND ui.status = 'reading')
         OR (p_tab = 'archived' AND ui.status = 'archived')
       )
     ORDER BY
        CASE WHEN p_tab = 'curated' THEN
            CASE WHEN i.published_at IS NULL THEN 2
                 WHEN (EXTRACT(EPOCH FROM (now() - i.published_at)) / 86400.0) <= 7  THEN 0
                 WHEN (EXTRACT(EPOCH FROM (now() - i.published_at)) / 86400.0) <= 30 THEN 1
                 ELSE 2 END
        END ASC NULLS LAST,
        CASE WHEN p_tab = 'curated' AND i.published_at IS NOT NULL AND (EXTRACT(EPOCH FROM (now() - i.published_at)) / 86400.0) <= 30 THEN
            COALESCE(ui.relevance_score, 20) - 2.0 * GREATEST(0.0, (EXTRACT(EPOCH FROM (now() - i.published_at)) / 86400.0))
        END DESC NULLS LAST,
        i.published_at DESC
     LIMIT p_limit;
$$;

