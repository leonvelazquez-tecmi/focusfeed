# Roadmap

Actualizado: 11-oct-2026

## Objetivo de este ciclo

Convertir FocusFeed de app personal a producto que otra persona pueda usar sin ver mis datos.

La línea de llegada del ciclo: dos cuentas distintas, cada una con sus fuentes y su historial de lectura, sin filtración entre ellas, verificado en pantalla.

## Terminado

**T1. Corpus compartido y estado por usuario.** Hecho y verificado localmente el 18-ago.
- `supabase_schema_v2.sql`: tablas `subscriptions`, `user_items`, `ai_usage`, perfil por `user_id`, RLS encendido, funciones RPC `get_user_feed` y `get_user_counts`, migración desde v1 con respaldo y rollback.
- `app/storage.py` reescrito: toda función personal recibe `user_id`. `app/ai_engine.py` separa el análisis universal del score personal. `app/auth.py` resuelve el usuario de cada request.
- `tests/test_multitenancy.py`: 18 verificaciones de aislamiento, todas en verde.
- Verificado también sobre HTTP y en pantalla con dos cuentas simultáneas.

**Pendiente humano antes de que esto llegue a producción:** correr `supabase_schema_v2.sql` y `supabase_migration_v4_rls.sql` siguiendo `docs/runbook_T2.md`. El uuid de v2 se deja como está; la reasignación a tu cuenta real va después del login.

**T7. Títulos reales.** Hecho y verificado el 8-oct.
- Causa: `find(a) or find(b)` en `app/ingestion.py`. Un `Element` de ElementTree sin hijos es falsy, así que el `or` descartaba el título, el link, el autor y la fecha de todo YouTube y todo Atom. Por eso salían "Sin título" con fecha "ahora".
- Arreglo: helpers `_first` y `_text` que comparan contra `None`, más respaldo de título con `media:title` o la primera línea del contenido. `tests/test_ingestion.py` con fixtures: 14/14 en verde (el código viejo falla 10).
- `routines/repair_titles.py` repara lo ya guardado: YouTube por oEmbed (sin llave), otros feeds re-descargando por `guid`. Probado local 40/40. La fecha de esos items viejos se queda como la de ingesta: oEmbed no la trae.

**T8. Sincronización rápida.** Hecho y verificado el 8-oct.
- El resumen de IA salió de la descarga: antes cada feed analizaba sus 4 items más nuevos aunque ya existieran. Ahora solo `/api/items/process-pending` analiza, y solo lo nuevo.
- `run_parallel` en `app/server.py`: un feed lento ya no tumba la tanda con un 500.
- Tandas de 40 feeds, 16 descargas en paralelo, 3 tandas a la vez desde el cliente. Feeds sincronizados hace menos de 15 min se saltan (YouTube no manda ETag, publica con `max-age=900`); los que sí mandan ETag o Last-Modified se piden condicionales.
- La app sincroniza sola al abrir si pasaron más de 30 min, sin bloquear el feed.
- Medido local: antes 172 fuentes en 8 s; ahora 1,344 fuentes en 11 s y la repetición en 3 s.

**T10. Feed visual y sin ruido.** Hecho el 11-oct, en tres PRs.
- Imagen en cada tarjeta: RSS/Atom ahora trae media:content, media:thumbnail, enclosure, itunes:image o el primer `<img>`; lo nuevo sin imagen pasa por og:image. Respaldo de color por fuente. `routines/backfill_images.py` para lo ya guardado.
- Shorts de YouTube fuera: `app/shorts.py` (HEAD a `/shorts/<id>`). `routines/clean_shorts.py` archivó los existentes.
- Rediseño editorial estilo radio independiente (NTS, Freaks Only FM). Sale de "fuera de alcance" por decisión de León; ver `context/decisiones.md`.

## En curso

**T9. "Para ti" con JEV.** Spike hecho el 8-oct (`routines/jev_scoring_spike.py`), falta conectarlo.
- 150 items recientes: USD 0.024 por cada 1,000 items, latencia mediana 0.4 s. La heurística actual le da 52 a casi todo; JEV pone arriba IA, agentes y PKM, y hasta abajo late-night shows y nota roja.
- Falta: medir contra likes y dislikes reales de producción, y conectar `score_with_jev` en `app/ai_engine.py` (cambia el prompt de IA y el costo por usuario: pregunta primero).


**T2. Autenticación.** Cuatro PRs, en este orden de deploy. Decisiones en `context/decisiones.md` (11-oct).

- **A. Base cerrada (RLS v4).** Hecho, en borrador: [leonvelazquez-tecmi/focusfeed#14](https://github.com/leonvelazquez-tecmi/focusfeed/pull/14). Scripts, runbook (`docs/runbook_T2.md`) y 100 verificaciones locales en `tests/rls/`. No toca la app.
  Pendiente humano: correr el runbook, pasos 0 a 5, **antes** de mergear el PR B. Mandar a Claude la fila del diagnóstico del paso 1.
- **B. Pantalla de acceso + token.** Por hacer. supabase-js por CDN con versión fija, `GET /api/config` con URL y llave anon, `Authorization: Bearer` en `api()`, lista de correos. Un token presente pero inválido responde 401 y **nunca** cae a `OWNER FALLBACK`. Verificar JWT HS256 y, si el proyecto firma con ES256, validar contra `/auth/v1/user` con caché.
  Pendiente humano: credenciales OAuth de Google, proveedor en Supabase, `SUPABASE_ANON_KEY` y `FF_CORREOS_PERMITIDOS` en Vercel.
- **C. Borrar `OWNER FALLBACK` de `app/auth.py`** y exigir `CRON_SECRET`. Se mergea horas después de B, no días: León entra en cada dispositivo, mergea C, saca respaldo y corre `supabase_reasignar_dueno.sql`.
- **D. Topes por usuario.** 15 fuentes en `free`; 50 análisis de IA al día. `process-pending` hoy analiza el corpus global: tiene que limitarse a lo que sigue cada usuario.

Verificación final de T2: dos cuentas de Google en pantalla, cada una con lo suyo. Desde la nube Claude solo puede hacerlo en local con tokens firmados; la prueba con Google real es de León.

## Cola, en este orden

1. **T3. IA que sí corre, con presupuesto.** Medio hecho.
   Ya está: credenciales por entorno (`GEMINI_API_KEY`), prompt sin nombre propio, palabras clave derivadas del perfil de cada usuario, tabla `ai_usage` con tokens reales del modelo y endpoint `GET /api/usage`.
   Falta: poner la llave en Vercel, elegir modelo definitivo (cargar la skill `claude-api` para IDs y precios vigentes, no adivinar), fijar `AI_PRICE_IN_PER_MTOK` y `AI_PRICE_OUT_PER_MTOK`, y aplicar tope por plan. Salida obligatoria: costo real por usuario activo al mes.
2. **T5. Landing y onboarding.** Meta medible: del registro al primer feed curado en menos de 60 segundos, pegando el OPML de Feedly.
3. **Diez conversaciones.** Mandarlo a diez personas con vault y guardar sus respuestas literales en `customers/`. Hábito semanal, no evento único.
4. **T6. Export al vault como función de primer nivel.** Botón en cada tarjeta y acción de "manda los guardados de esta semana a mi vault".
5. **T4. Stripe y planes.** Hasta el final, cuando ya sepamos qué dijeron esas diez personas.

## Fuera de alcance en este ciclo

Escrito a propósito, para que no se construya de más:

- Equipos, roles y permisos multiusuario
- App nativa
- Integraciones que no sean Obsidian
- Migrar a Next.js
- Más proveedores de IA
- Cobros

## Deuda conocida

- **"Para ti" sube artículos de 2023 a 2025.** El score no pesa la antigüedad. Las imágenes de esos items se piden bajo demanda (`/api/items/fill-images`), no con un llenado del histórico. Arreglar el orden toca el score de IA: pregunta primero.

- **La IA sigue sin correr en producción.** La causa original (las credenciales se perdían en `fetch_profile`) ya está arreglada: ahora la llave se lee de `GEMINI_API_KEY` del entorno. Falta ponerla en Vercel y decidir el modelo definitivo. Sin esa variable, el motor de respaldo entrega resumen sin modelo y score determinista.
- **RLS apagado y `GRANT ALL` a `anon`** en el esquema v1 que sigue vivo en producción. v2 no lo cierra del todo (respaldos legibles con la llave anon, corpus legible por cualquier cuenta). `supabase_migration_v4_rls.sql` lo cierra y el orden para correrlo está en `docs/runbook_T2.md`. Tiene que correr antes de publicar el login.
- **`OWNER FALLBACK` en `app/auth.py`.** Cualquier request sin token válido se atiende como si fuera el dueño. Es intencional para que la app siga funcionando durante T2, y es lo primero que hay que borrar cuando el acceso esté puesto.
- **El cron no escala.** Ya va en tandas de 40 y salta lo fresco, pero sigue siendo una sola función con presupuesto de 45 segundos.
- **Migración pendiente:** `supabase_migration_v3_feed_cache.sql` (columnas `etag` y `last_modified` en `feeds`). Sin ella la sync funciona, pero sin caché condicional en producción. Va incluida en v4.
- **El repo vive en cuenta institucional.** Mover a cuenta personal antes de que `customers/` tenga nombres y correos reales. Ese es el disparador, no una fecha.
- **Sin confirmar: el guardado que se perdió el 18-ago.** El diagnóstico mostró la fila del dislike con `status='inbox'` (bug ya corregido), pero ninguna fila con `status='reading'` entre las 20 más recientes. Como todas las filas de la migración comparten `created_at`, el orden entre ellas es arbitrario y la consulta pudo dejarla fuera. La hipótesis principal es que la escritura falló en silencio, que es justo lo que el manejo de errores nuevo ahora hace visible. Si vuelve a pasar, ahora sale un toast con el error real.
- **`set_item_status` y `feedback` no validan suscripción.** Un usuario puede crear una fila en `user_items` para un item de un feed al que no está suscrito. No filtra datos de nadie (la lectura del feed sí valida la suscripción) y no devuelve contenido ajeno, pero ensucia la tabla. Should fix, no bloquea.
- **`test_ingestion.py` falla en main** ("Atom: título vacío cae a la primera línea"). No es de T2. Should fix.
- **`/api/items` devuelve `debug` con un prefijo de la llave de Supabase y el último error de consulta.** Con llaves nuevas (`sb_secret_…`) filtra 5 caracteres. Se recorta a un booleano en el PR C.
- **Nada de esto está subido.** El rediseño estilo Feedly, los arreglos de guardados y visto persistente, y todo T1 están en local, sin commit ni push.
