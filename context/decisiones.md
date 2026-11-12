# Decisiones

Se agrega al final. Una decisión revertida se marca, no se borra.

## 2026-08-17 · Comprador: profesionales del conocimiento con vault

Consultores, investigadores, analistas y gente de PKM en Obsidian o Notion.

Por qué: el diferenciador ya construido es el export a Obsidian con frontmatter (`app/pkm_exporter.py`), y ese comprador ya paga por herramientas de conocimiento. Es además un perfil con el que se puede hacer research de primera mano.

Descartados: directivos y equipos (ticket mayor, pero exige multiusuario, facturación y ciclo de venta largo) y creadores de contenido (mercado ruidoso contra Feedly AI y Perplexity).

## 2026-08-17 · Monetización: freemium, el producto absorbe el costo de IA

Gratis hasta 15 fuentes sin IA. Pro con curación, export y brief semanal.

Por qué: es el modelo con mayor techo y el que no le pide al usuario conseguir una API key. El costo se controla con corpus compartido, modelo barato y tope por plan.

Consecuencia: el precio no se fija hasta tener medido el costo real por usuario activo al mes. Ver `unit-economics.md`.

## 2026-08-17 · Cobros hasta después de las diez conversaciones

Stripe queda al final de la cola.

Por qué: construir cobro antes de saber qué dicen diez usuarios reales es adelantarse. El precio y hasta la definición del plan Pro dependen de esa conversación.

## 2026-08-17 · El repo se queda por ahora en la cuenta institucional

`github.com/leonvelazquez-tecmi/focusfeed`.

Disparador para mover a cuenta personal: el momento en que `customers/` tenga nombres y correos de personas reales, o cuando exista la primera línea de Stripe. Lo que se mueve entonces: repo, proyecto de Vercel y cuenta de Stripe.

## 2026-08-17 · No se migra a Next.js en este ciclo

El backend seguirá siendo `http.server` de Python dentro de una función de Vercel, validando el JWT de Supabase en cada request.

Por qué: la migración es un proyecto en sí mismo y no acerca al primer peso cobrado. Se revisa si el producto crece.

## 2026-10-11 · Rediseño editorial, aunque estaba fuera de alcance

León pidió un diseño "más cool" tomando como referencia nts.live y freaksonly.fm. El rediseño visual estaba en "fuera de alcance" del ciclo; se hace igual por decisión explícita suya.

Qué se tomó de cada uno, medido en el navegador:
- **NTS:** Univers Condensed en mayúsculas para titulares, filetes de 1 a 2 px en vez de sombras suaves, etiquetas en caja, botón de play cuadrado sobre la imagen, imagen grande como protagonista.
- **Freaks Only FM:** JetBrains Mono para todo lo funcional, #0a0a0a con un solo acento rojo (#dc2828), sombras duras desplazadas (4 a 8 px, sin difuminar), cero radios, ticker en marquesina de "now playing" y punto en vivo que pulsa.

Aplicación: Archivo Narrow (sustituto libre de Univers Condensed) para titulares, JetBrains Mono para metadatos y botones, papel #f3f1ec en claro y negro #0a0a0a en oscuro, rojo señal como único acento. Imagen 16:10 a todo el ancho de cada tarjeta, rejilla de 1, 2 o 3 columnas (375, 760 y 1100 px). Movimiento: entrada escalonada de tarjetas, zoom de imagen y subrayado rojo al pasar, ticker con los titulares del lote. Todo se apaga con `prefers-reduced-motion`.

## 2026-10-11 · Migrar a cuenta personal al decidir el lanzamiento

Ajusta el disparador del 17-ago ("el repo se queda por ahora en la cuenta institucional"). El disparador ya no es que `customers/` tenga nombres ni la primera línea de Stripe: es la decisión de lanzar. Hasta entonces repo, Vercel, Supabase y Drive siguen en la cuenta institucional.

Qué se mueve: repo de GitHub, proyecto de Vercel, proyecto de Supabase y la carpeta de Drive. Stripe o Ko-fi se abren directo en la cuenta personal.

Condición previa y detalle: en el brief de pago voluntario v1.1, fuera del repo.

## 2026-10-17 · Corrección: el proyecto de Supabase vive en la cuenta personal

El proyecto de Supabase de FocusFeed está en la cuenta de Supabase personal de León (la de su Gmail), organización **Personal**. No está en las organizaciones Tecmilenio ni Tecmilenio Anual. Corrige lo que decía la entrada del 11-oct, que lo ubicaba en la cuenta institucional.

Para qué sirve: cualquier migración o consulta SQL se corre en el dashboard de esa cuenta. Repo y Vercel siguen en la cuenta institucional.

## 2026-10-17 · Sin "Por qué" cuando el score es de JEV

El recuadro "Por qué" de la tarjeta lo escribía el motor de palabras clave aunque el score viniera de JEV, y a veces decía lo contrario del score. Se decidió no mostrar motivo cuando califica JEV: un motivo falso cuesta más confianza que ningún motivo.

Qué se descartó por ahora: que un modelo pequeño escriba una línea sobre el contenido real para el top del día. Cambia el prompt de IA y cuesta unos USD 0.03 al mes por usuario [Need to verify]. Se retoma si el recuadro vacío se extraña.

Principio que deja: un score y su explicación salen siempre del mismo motor.

## 2026-10-17 · Ícono propio: la marca "FF" del encabezado

La app no tenía ícono propio (el manifest apuntaba a una imagen externa y `/favicon.ico` daba 404). No existía un logo aparte, así que el ícono sale de la marca "FF" del encabezado: tinta clara sobre #0a0a0a con sombra roja dura #dc2828, dibujada con rectángulos para no depender de fuentes. León lo aprobó al verlo en el iPhone.

## 2026-11-10 · JEV recibe lo que el lector aprendió, y Guardar cuenta como señal

Diagnóstico: tras semanas de uso "Para ti" seguía irrelevante porque el score de JEV solo veía los temas declarados, 8 autores en orden arbitrario y las primeras 8 etiquetas descartadas. Los temas aprendidos con corazón, relevante y clic vivían en el perfil pero solo los leía el motor de respaldo; Guardar y Visto no enseñaban nada; y el pulgar abajo nunca penalizaba a la fuente.

Decisión (cambia el prompt de IA, autorizado por León): la pregunta a JEV incluye hasta 8 temas por peso neto a favor y en contra, fuentes que más valora y que suele descartar (saldo de +3 o más, o de -5 o menos) y los últimos 5 títulos que le gustaron y que descartó. Guardar suma (fuente +2, temas +1) y quitarlo lo deshace. El pulgar abajo resta 3 a la fuente. Todo vive en `learned_preferences`; no hay migración.

Costo de IA (decisión de negocio): mismas llamadas, preguntas más largas, unos 150 a 250 tokens más por llamada [Inference, sin llave de OpenRouter en la sesión]. Se mide después del deploy con `ai_usage.kind = 'jev_score'`.

Privacidad: del buzón corporativo nunca se guarda un título como ejemplo, y su remitente y etiquetas se marcan como privados y no entran a la pregunta. Límite conocido: lo del buzón que se calificó antes de este cambio no se puede identificar hacia atrás.

Lo que no resuelve: los items que ya tienen score no se recalculan, así que la mejora se nota con contenido nuevo. Recalificar y calificar desde el cron suben el costo; el corte de relevancia en "Para ti" requiere migración. Se deciden con los datos de producción.

## 2026-11-12 · Calificar todo lo reciente (30 días), no solo los 160 más nuevos

Datos de producción de León (12-nov): 865 items en "Para ti" en 7 días, 341 sin score (39%) y 778 sin resumen de IA (90%). Score mediano de lo calificado: 13. Lo sin calificar entra con 20 (v8), así que lo que JEV nunca evaluó queda por encima de lo que evaluó como poco relevante.

Decisión: buscar directamente lo pendiente en vez de una ventana de la bandeja. Solo cuentan los últimos 30 días porque en "Para ti" el score no ordena lo más viejo (v8). Se califican 120 items por llamada con tope de 25 s, y el cliente encadena hasta 3 rondas por carga (máximo 360 items). Solo la primera ronda analiza resúmenes con Gemini: el costo de resúmenes no cambia.

Costo de IA (decisión de negocio): más llamadas a JEV por usuario, no más caras. Con los números de León, unas +65% de llamadas; con el precio del spike (USD 0.024 por 1,000 items) son unos USD 0.008 una sola vez para ponerse al corriente y del orden de USD 0.04 al mes en régimen [Inference, sin llave de OpenRouter en la sesión]. Se mide con `ai_usage.kind = 'jev_score'`.

Migración v9 (función de solo lectura `get_unscored_items`): la aplica León a mano. Sin ella el código usa el comportamiento anterior; no falla. Rollback: `supabase_rollback_v9_unscored_items.sql`.

No resuelve: el contenido sin resumen sigue sin etiquetas (el aprendizaje por temas no puede actuar sobre él), y el criterio fijo del prompt de JEV ("no vale: entretenimiento, reseña de ocio") contradice los temas de ocio de León (cine, música, enología). Eso es el PR 3 y cambia el prompt.

## 2026-11-12 · El prompt de JEV respeta los temas de ocio que el lector declara

Datos de León (12-nov): score mediano de lo calificado 13; promedio 50 en lo que amó, 33 en lo que descartó y 27 en lo que abrió. JEV ordena en la dirección correcta pero con poca separación. El "no vale" fijo decía "entretenimiento… reseña de ocio", lo que contradice tres de sus seis temas (cine de autor, música, enología), sus fuentes con boost (críticos de cine y música) y el tema aprendido "Reseña". [Inference: explica parte de la compresión; no se puede probar sin llamar a JEV real.]

Decisión (cambia el prompt de IA, autorizado por León con plan): el texto base es genérico y no nombra los temas de nadie. "Sí vale": trata de alguno de sus temas con análisis, crítica con criterio, ideas propias o conocimiento útil. "No vale": ruido (noticia efímera, clickbait, promoción o anuncio, contenido genérico sin criterio propio, tema ajeno a sus intereses declarados). Un tema declarado no es ruido ni "solo entretenimiento" si se trata con análisis o crítica. `DEFAULT_CRITERIA` no cambia, porque aplica a otros usuarios.

Además: las etiquetas aprendidas solo viajan a JEV con saldo neto de 2 o más (un clic o un "relevante" solos son +1 y quedan fuera), y "Actualizar temas" ya no pisa el criterio con vacío (`save_profile_data(criteria=None)` no toca la columna).

Costo de IA (decisión de negocio): mismo número de llamadas; la pregunta crece unas decenas de tokens por el texto nuevo y se acorta algo por las etiquetas filtradas [Inference, sin llave en la sesión]. Se mide con `ai_usage.kind = 'jev_score'`.

Riesgo: el efecto sobre el score de JEV no se pudo medir con JEV real. Se valida con `routines/jev_eval.py --comparar` (prompt anterior contra nuevo sobre las calificaciones reales) o, tras el deploy, con SQL sobre lo calificado después. Se revierte por git. Esto solo afecta lo que se califique de ahora en adelante; recalificar lo de los últimos 30 días es un paso aparte que lleva `UPDATE` (tabla de respaldo, consulta de prueba y rollback) y lo aplica León.

## 2026-11-12 · Los likes de YouTube enseñan gustos (señal, no contenido)

León pidió que FocusFeed aprenda también de lo que le da like en YouTube: es una señal deliberada y de mucho más volumen que sus calificaciones dentro de la app (~300 en total).

Ruta elegida: la API de YouTube con la conexión de Google que ya existe y el mismo permiso (`youtube.readonly`), que cubre `videos.list?myRating=like`. Descartados: Google Takeout (exportación manual que nunca se refresca) y yt-dlp con cookies (guardar la sesión de la persona en el servidor, frágil y contra los términos de YouTube). El flujo de recetas con yt-dlp que se mencionó no está en este repo [Need to verify].

Diseño: los likes NO se importan como items (entrarían a una bandeja que ya tiene 18 mil). Se guarda una foto de conteos por canal y por etiqueta dentro de `learned_preferences.yt`; cada corrida reemplaza lo anterior, así que es idempotente, no necesita tabla de "ya vistos" ni migración, y un like quitado se desaprende. Se leen los 300 más recientes (6 páginas), sin Shorts (≤ 60 s). No se guarda título, descripción ni id.

Peso (más fuerte que un clic, más débil que un corazón, porque en YouTube el like casi no cuesta): +2 por like al canal con tope +6, +1 por etiqueta con tope +6; en el motor de respaldo +4 por like con tope +12. A JEV le llegan dos frases nuevas al final de "Sí vale": canales con 2 likes o más y temas con peso 2 o más. Nunca viajan títulos. Un canal que ya está en "más valora" no se repite.

Costo de IA (decisión de negocio): sin llamadas nuevas ni cambios en Gemini; la pregunta a JEV crece unas 50 a 70 tokens [Inference]. Cuota de YouTube: ≤ 6 unidades por importación, una al día como máximo.

Privacidad: estos nombres de canales y temas se incluyen en la pregunta a OpenRouter (sin retención de datos); `privacidad.html` lo dice y ofrece "Olvidar mis likes de YouTube". Pendiente humano: revisar la descripción de uso del permiso en la pantalla de consentimiento de Google Cloud [Need to verify si Google lo exige].

Fuera: etiquetar cada video con Gemini (mejor calidad de temas, costo por video), usar títulos como ejemplos, refresco por cron, recalificar lo reciente.

## 2026-11-12 · Google Cloud: dónde vive el cliente OAuth y qué falta antes de abrir a otras personas

Revisado con León en la consola (cuenta personal, Gmail):
- Proyecto **"My First Project"** (`graphite-argon-457414-n5`). Cliente OAuth web **"FocusFeed"**, creado el 24-oct-2026 (un día antes de la conexión en `google_connections`, 25-oct). El otro cliente, "Cliente web 1" (7-jun), no es de FocusFeed.
- Estado de publicación: **En producción**, 1 usuario (reportado por León). Centro de verificación: sin verificación enviada ni requerida, porque en "Acceso a los datos" no hay permisos sensibles declarados aunque la app pide `youtube.readonly` y `gmail.readonly` al conectar. La información de marca no se muestra a los usuarios.
- Consecuencia: para los likes de YouTube no hay nada que cambiar en Google (mismo permiso, sin descripción de uso que actualizar).
- El cliente tiene **dos secretos activos** (aviso de seguridad de Google). **No borrar ninguno** hasta confirmar cuál usa `GOOGLE_CLIENT_SECRET` en Vercel; después dejar solo uno.
- **Antes de abrir FocusFeed a otras personas (T5):** declarar los permisos en "Acceso a los datos", completar la información de marca (con enlace a `/privacidad`) y pasar la verificación de Google; la de Gmail (permiso restringido) suele ser la más exigente [Need to verify].

## 2026-11-12 · Primer aprendizaje de likes: etiquetas en inglés y fragmentadas, se espera a medir

Primera corrida real (León, 12-nov): 300 likes (el tope) → 134 canales y 100 temas. Las etiquetas vienen en inglés y fragmentadas ("movie", "movies" y "film" por separado; "comedy" ×15 y "artificial intelligence" ×12 arriba; "new" ×9 es basura). Las pone cada creador.

Decisión de León: **esperar unos días y medir** cómo se comporta JEV con lo aprendido antes de limpiar. Si se limpia: unir sinónimos y descartar etiquetas basura, sin costo de IA. Para subir el tope de 300 likes basta cambiar `YT_LIKES_MAX` (1 unidad de cuota por cada 50).

