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
