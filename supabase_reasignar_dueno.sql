-- ==========================================================
-- Reasignar los datos del dueño a su cuenta real de Google (T2)
-- ==========================================================
-- v2 migró todo al uuid provisional 00000000-0000-0000-0000-000000000001.
-- Cuando entras con Google por primera vez, Supabase te da otro uuid y la
-- app te ve como cuenta nueva (con las fuentes semilla). Este script mueve
-- tu historial a ese uuid y te pone en plan pro.
--
-- CÓMO
--   1. Sigue el orden de docs/runbook_T2.md, "Después del login".
--   2. Sustituye REEMPLAZA-CON-TU-CORREO abajo por el correo de Google
--      con el que entraste. Se busca por correo, no por uuid copiado a
--      mano: así el historial no puede terminar en la cuenta de otro.
--   3. Corre el archivo completo. Si algo no cuadra, aborta solo.
--
-- QUÉ HACE CON LO QUE YA HICISTE CON LA CUENTA NUEVA
--   Nada se borra a ciegas. Se fusiona:
--     · fuentes: te quedas con la unión de las dos cuentas; si las dos
--       siguen la misma, se queda tu categoría vieja.
--     · items, campo por campo: lo que la app generó sola en la cuenta
--       nueva (score de IA) no pisa tu historial. Si en la cuenta nueva
--       cambiaste el estado, gana ese estado, salvo que el viejo fuera
--       guardado: un guardado nunca se pierde. Si diste un rating, gana ese
--       rating con su comentario. Lo que no tocaste se queda como estaba.
--       El score de IA viejo gana si es real (hecho con tus temas).
--     · perfil: se queda el viejo (temas y preferencias aprendidas). Si en
--       la cuenta nueva ya escribiste temas, el script aborta y te avisa,
--       para que decidas tú. Lo que la cuenta nueva aprendió de tus likes
--       se descarta; los likes mismos sí se conservan en items.
--     · las fuentes semilla de la cuenta nueva se quedan; quítalas en la app
--       si no las quieres.
-- ==========================================================

BEGIN;

DO $$
DECLARE
    -- El uuid donde quedó tu historial. Es este, salvo que el paso 1 del
    -- runbook haya mostrado otro en `duenos_v2`.
    viejo UUID := '00000000-0000-0000-0000-000000000001';
    correo TEXT := 'REEMPLAZA-CON-TU-CORREO';
    nuevo UUID;
    n_subs INTEGER;
BEGIN
    SELECT id INTO nuevo FROM auth.users WHERE lower(email) = lower(correo);
    IF nuevo IS NULL THEN
        RAISE EXCEPTION 'No hay cuenta con el correo %: entra primero con Google', correo;
    END IF;
    IF nuevo = viejo THEN
        RAISE EXCEPTION 'El uuid nuevo es igual al provisional';
    END IF;
    SELECT COUNT(*) INTO n_subs FROM public.subscriptions WHERE user_id = viejo;
    IF n_subs = 0 THEN
        RAISE EXCEPTION 'El uuid provisional no tiene fuentes: ¿ya se reasignó?';
    END IF;

    IF EXISTS (SELECT 1 FROM public.user_profile
                WHERE user_id = nuevo
                  AND COALESCE(NULLIF(focus_topics, ''), '[]') <> '[]') THEN
        RAISE EXCEPTION 'La cuenta nueva ya tiene temas propios en su perfil. Revisa cuál conservar antes de fusionar';
    END IF;

    -- Fuentes: unión. Si las dos cuentas siguen la misma, se queda la
    -- categoría que tú le pusiste en la cuenta vieja.
    UPDATE public.subscriptions n
       SET custom_category = v.custom_category
      FROM public.subscriptions v
     WHERE n.user_id = nuevo AND v.user_id = viejo AND v.feed_id = n.feed_id;
    DELETE FROM public.subscriptions v
     WHERE v.user_id = viejo
       AND EXISTS (SELECT 1 FROM public.subscriptions n
                    WHERE n.user_id = nuevo AND n.feed_id = v.feed_id);
    UPDATE public.subscriptions SET user_id = nuevo WHERE user_id = viejo;

    -- Items en las dos cuentas, campo por campo: la cuenta nueva crea filas
    -- sola (el score de IA al entrar) con valores por default. Un default no
    -- es una decisión tuya y no pisa lo viejo. Un like nuevo no borra un
    -- guardado viejo, ni un "visto" nuevo borra un like viejo.
    -- La app guarda '' (no NULL) como comentario en cada rating.
    UPDATE public.user_items n
       -- Un guardado viejo sobrevive a un "visto" nuevo: en la cuenta nueva
       -- no sabías que ya lo tenías guardado.
       SET status = CASE WHEN v.status = 'reading' THEN 'reading'
                         WHEN n.status = 'inbox' THEN v.status
                         ELSE n.status END,
           user_rating = CASE WHEN COALESCE(n.user_rating, 'none') = 'none'
                              THEN v.user_rating ELSE n.user_rating END,
           -- El comentario va con el rating que gana, no por separado.
           user_feedback_comment = CASE WHEN COALESCE(n.user_rating, 'none') = 'none'
                                        THEN v.user_feedback_comment ELSE n.user_feedback_comment END,
           feedback_at = CASE WHEN COALESCE(n.user_rating, 'none') = 'none'
                              THEN v.feedback_at ELSE n.feedback_at END,
           -- El score nuevo se calculó con los temas por default de la cuenta
           -- nueva; el viejo, con tus temas. Gana el viejo cuando es un score
           -- real (siempre trae nota). El 70 sin nota que v1 ponía al ingerir
           -- es un placeholder: ahí gana el nuevo.
           relevance_score = CASE WHEN COALESCE(v.curator_note, '') <> ''
                                  THEN v.relevance_score
                                  ELSE COALESCE(n.relevance_score, v.relevance_score) END,
           curator_note = CASE WHEN COALESCE(v.curator_note, '') <> ''
                               THEN v.curator_note
                               ELSE COALESCE(n.curator_note, v.curator_note) END
      FROM public.user_items v
     WHERE n.user_id = nuevo AND v.user_id = viejo AND v.item_id = n.item_id;
    DELETE FROM public.user_items v
     WHERE v.user_id = viejo
       AND EXISTS (SELECT 1 FROM public.user_items n
                    WHERE n.user_id = nuevo AND n.item_id = v.item_id);
    UPDATE public.user_items SET user_id = nuevo WHERE user_id = viejo;

    UPDATE public.ai_usage SET user_id = nuevo WHERE user_id = viejo;

    -- Perfil: el viejo, que trae temas y preferencias aprendidas. Si el
    -- uuid viejo no tiene perfil, se conserva el de la cuenta nueva.
    IF EXISTS (SELECT 1 FROM public.user_profile WHERE user_id = viejo) THEN
        DELETE FROM public.user_profile WHERE user_id = nuevo;
        UPDATE public.user_profile SET user_id = nuevo, plan = 'pro' WHERE user_id = viejo;
    ELSIF EXISTS (SELECT 1 FROM public.user_profile WHERE user_id = nuevo) THEN
        UPDATE public.user_profile SET plan = 'pro' WHERE user_id = nuevo;
    ELSE
        INSERT INTO public.user_profile (user_id, plan) VALUES (nuevo, 'pro');
    END IF;

    RAISE NOTICE 'Reasignadas % fuentes a %', n_subs, nuevo;
END $$;

COMMIT;

-- Verificación (sustituye el uuid):
--   SELECT COUNT(*) FROM subscriptions WHERE user_id = 'TU-UUID';   -- tus fuentes
--   SELECT plan FROM user_profile WHERE user_id = 'TU-UUID';        -- pro
--   SELECT COUNT(*) FROM subscriptions
--    WHERE user_id = '00000000-0000-0000-0000-000000000001';       -- 0
