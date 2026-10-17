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
