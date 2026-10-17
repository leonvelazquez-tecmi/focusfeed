-- Rollback de las migraciones v4 a v7 de get_user_feed ("Para ti" con antigüedad).
-- Restaura la función original de supabase_schema_v2.sql: orden por score y luego fecha,
-- con 70 por defecto para lo sin calificar y sin castigo por antigüedad.
-- Idempotente: CREATE OR REPLACE, se puede correr más de una vez.
-- No toca tablas ni datos. Para volver a la versión nueva, correr de nuevo la v7.

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
        CASE WHEN p_tab = 'curated' THEN COALESCE(ui.relevance_score, 70) END DESC NULLS LAST,
        i.published_at DESC
     LIMIT p_limit;
$$;
