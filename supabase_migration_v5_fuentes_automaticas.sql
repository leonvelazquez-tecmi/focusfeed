-- FocusFeed v5: fuentes detectadas solas (YouTube y Gmail vía Google, buzón corporativo vía un agente autorizado).
-- Idempotente: se puede correr más de una vez. Aplicar ANTES de desplegar el código nuevo.
--
-- 1. feeds / items privados: owner_user_id null = corpus público (como hasta hoy);
--    con valor = visible solo para esa persona (fuentes de correo).
-- 2. subscriptions.origin: de dónde salió la suscripción.
-- 3. dismissed_sources: lo que la persona quitó o excluyó no se vuelve a agregar solo.
-- 4. google_connections: refresh token de Google por persona. Solo lo lee el servidor.
-- 5. get_unanalyzed_items ignora lo privado: ningún usuario dispara IA sobre el
--    correo de otro.

BEGIN;

ALTER TABLE public.feeds ADD COLUMN IF NOT EXISTS owner_user_id UUID;
ALTER TABLE public.items ADD COLUMN IF NOT EXISTS owner_user_id UUID;
CREATE INDEX IF NOT EXISTS idx_feeds_owner ON public.feeds(owner_user_id) WHERE owner_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_items_owner ON public.items(owner_user_id) WHERE owner_user_id IS NOT NULL;

ALTER TABLE public.subscriptions ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'manual';

CREATE TABLE IF NOT EXISTS public.dismissed_sources (
    user_id    UUID NOT NULL,
    source_key TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, source_key)
);

CREATE TABLE IF NOT EXISTS public.google_connections (
    user_id       UUID PRIMARY KEY,
    refresh_token TEXT NOT NULL,
    scopes        TEXT,
    connected_at  TIMESTAMPTZ DEFAULT now(),
    last_scan_at  TIMESTAMPTZ,
    gmail_cursor  TEXT
);

-- RLS: sin policies = solo service_role (el servidor) puede leer o escribir.
ALTER TABLE public.google_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dismissed_sources  ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.google_connections FROM anon, authenticated;
REVOKE ALL ON public.dismissed_sources  FROM anon, authenticated;

-- Lectura del corpus: lo público para cualquiera, lo privado solo para su dueño.
DROP POLICY IF EXISTS read_feeds ON public.feeds;
CREATE POLICY read_feeds ON public.feeds FOR SELECT
    USING (owner_user_id IS NULL OR owner_user_id = auth.uid());

DROP POLICY IF EXISTS read_items ON public.items;
CREATE POLICY read_items ON public.items FOR SELECT
    USING (owner_user_id IS NULL OR owner_user_id = auth.uid());

-- Mismo contrato que antes; solo agrega el filtro de lo privado.
CREATE OR REPLACE FUNCTION public.get_unanalyzed_items(p_limit INTEGER DEFAULT 10)
RETURNS TABLE (id BIGINT)
LANGUAGE sql STABLE AS $$
    SELECT i.id FROM public.items i
     WHERE COALESCE(i.ai_processed, 0) = 0
       AND i.owner_user_id IS NULL
     ORDER BY i.published_at DESC
     LIMIT p_limit;
$$;

COMMIT;

-- Verificación después de correr
-- SELECT column_name FROM information_schema.columns WHERE table_name='feeds' AND column_name='owner_user_id';
-- SELECT column_name FROM information_schema.columns WHERE table_name='subscriptions' AND column_name='origin';
-- SELECT COUNT(*) FROM google_connections;   -- 0 hasta que alguien conecte Google
