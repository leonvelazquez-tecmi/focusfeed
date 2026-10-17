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

**Pendiente humano antes de que esto llegue a producción:** correr `supabase_schema_v2.sql` en el SQL editor de Supabase, sustituyendo el uuid del bloque de migración por el de la cuenta real.

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
- **Disparador de la calibración (17-oct):** con 15 calificaciones con score de JEV ya se ve que ordena bien pero puntúa bajo. Repetir la medición cuando haya unas 50: correr en el SQL Editor la consulta de `user_items` con `ai_usage.kind = 'jev_score'` agrupada por `user_rating`, y `python3 routines/jev_eval.py` en la computadora de León (necesita `OPENROUTER_API_KEY` y las variables de Supabase). Cambiar el criterio de JEV es cambio de prompt: plan y visto bueno primero.


**T2. Autenticación.**
Supabase Auth con Google OAuth. Reusar el patrón de kanban-publico.
Lo que ya quedó listo del lado del código: `app/auth.py` verifica JWT HS256 de Supabase y falla cerrado si no hay secreto configurado. Falta la parte que depende del dashboard.

Bloqueado por trabajo humano:
1. Habilitar el proveedor de Google en Supabase Auth y cargar las credenciales de OAuth.
2. Poner `SUPABASE_JWT_SECRET` y `FOCUSFEED_OWNER_ID` en las variables de Vercel.
3. Decidir si la pantalla de acceso va antes o después de mostrar el primer feed.

Cuando eso exista, falta escribir: pantalla de acceso en `index.html`, envío del token en cada llamada, y **eliminar el respaldo `OWNER FALLBACK` de `app/auth.py`**, que hoy deja pasar cualquier request sin token como si fuera el dueño.

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

- **"Para ti" sube artículos de 2023 a 2025.** Resuelto el 17-oct. El orden resta 2 puntos por día de antigüedad (tope 60), lo sin calificar arranca en 20 y el score guardado no cambia. La tarjeta muestra mes y año con más de 11 meses.
  - Aplicadas por León en Supabase y verificadas con su consulta real: v4 a v7. Para un entorno nuevo basta `supabase_migration_v7_unscored_20.sql`; v4 a v6 quedan superseded. Rollback: `supabase_rollback_get_user_feed.sql`.
  - Código: rama SQLite de `app/storage.py` y `tests/test_feed_recency.py`.
  - Calibración de JEV con 15 calificaciones: corazones promedian 22 (de 9 a 45), pulgares abajo 8 (de 4 a 22). Ordena bien pero puntúa bajo. Ver T9.
  - Deuda que dejó la revisión (Should fix): las constantes (20, 2 por día, 60) viven duplicadas en la migración, en `storage.py` y en la prueba; la rama SQLite no castiga fechas nulas ni RFC 822 como lo hace Postgres; el corte de 330 días de `relTime` es un número suelto y el formato de mes y año no se probó en Safari de iPhone.

- **"Por qué" que no correspondía al artículo.** Resuelto el 17-oct. Dos causas: (1) `score_pending_for_user` guardaba el score de JEV con la nota del motor de palabras clave, dos motores en una tarjeta; (2) ese motor daba por coincidido un tema largo con una sola palabra suelta ("visual", "cultural", "post" de "post-punk"). Ahora un item lo califica un solo motor: con score de JEV no hay "Por qué", y el respaldo pide dos palabras distintas del tema, completas. Prueba: `tests/test_porque_honesto.py`. Falta que León corra el SQL de limpieza en Supabase para borrar las notas viejas de lo ya calificado por JEV.

- **La IA sigue sin correr en producción.** La causa original (las credenciales se perdían en `fetch_profile`) ya está arreglada: ahora la llave se lee de `GEMINI_API_KEY` del entorno. Falta ponerla en Vercel y decidir el modelo definitivo. Sin esa variable, el motor de respaldo entrega resumen sin modelo y score determinista.
- **RLS apagado y `GRANT ALL` a `anon`** en el esquema v1 que sigue vivo en producción. `supabase_schema_v2.sql` ya lo corrige, pero nadie lo ha corrido todavía.
- **`OWNER FALLBACK` en `app/auth.py`.** Cualquier request sin token válido se atiende como si fuera el dueño. Es intencional para que la app siga funcionando durante T2, y es lo primero que hay que borrar cuando el acceso esté puesto.
- **El cron no escala.** Ya va en tandas de 40 y salta lo fresco, pero sigue siendo una sola función con presupuesto de 45 segundos.
- **Migración v3 aplicada por León el 17-oct:** `supabase_migration_v3_feed_cache.sql` (columnas `etag` y `last_modified` en `feeds`). La sync ya puede usar caché condicional en producción.
- **El repo vive en cuenta institucional.** Se migra a cuenta personal (repo, Vercel, Drive) cuando se decida lanzar. Supabase ya está en la cuenta personal. Ver `context/decisiones.md`, 11 y 17-oct-2026.
- **Sin confirmar: el guardado que se perdió el 18-ago.** El diagnóstico mostró la fila del dislike con `status='inbox'` (bug ya corregido), pero ninguna fila con `status='reading'` entre las 20 más recientes. Como todas las filas de la migración comparten `created_at`, el orden entre ellas es arbitrario y la consulta pudo dejarla fuera. La hipótesis principal es que la escritura falló en silencio, que es justo lo que el manejo de errores nuevo ahora hace visible. Si vuelve a pasar, ahora sale un toast con el error real.
- **`set_item_status` y `feedback` no validan suscripción.** Un usuario puede crear una fila en `user_items` para un item de un feed al que no está suscrito. No filtra datos de nadie (la lectura del feed sí valida la suscripción) y no devuelve contenido ajeno, pero ensucia la tabla. Should fix, no bloquea.
- **Nada de esto está subido.** El rediseño estilo Feedly, los arreglos de guardados y visto persistente, y todo T1 están en local, sin commit ni push.
