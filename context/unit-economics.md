# Unit economics

Este archivo decide si el negocio existe. Primera medición real: 11-oct-2026 (versión anterior en `unit-economics_v1.0.md`).

## Números base

Usuario de referencia: 100 fuentes, el punto medio del comprador (50 a 200). Sin corpus compartido, porque con un solo usuario el traslape es cero.

| Métrica | Valor | Cómo se obtuvo | Confianza |
|---|---|---|---|
| Items nuevos por fuente al día | 1.05 | `items.created_at` y `published_at` de los últimos 14 días: 175 a 185 al día para 172 fuentes (se excluye el 29-sep, que fue backfill) | Alta |
| Items nuevos por usuario al día | ~105 (unos 3,200 al mes) | Lo anterior por 100 fuentes | Alta |
| Tokens por análisis de item | 398 de entrada, 297 de salida | `ai_usage`, 43 filas `item_analysis` (Gemini 3.6, 3.7 y 3.8 Flash) | Baja: 4 horas de datos y sin tokens de razonamiento (ver abajo) |
| Costo de análisis por item | USD 0.00141 (2026) / 0.00282 (desde 1-ene-2027) | Gemini 3.8 Flash: 0.75 / 3.75 USD por MTok hasta el 31-dic-2026, 1.50 / 7.50 después (ai.google.dev/pricing, 11-oct-2026) | Media |
| Costo de score JEV por item | USD 0.000026 | `ai_usage`, 257 filas `jev_score`, USD 0.00675 en total | Alta |
| Items analizados por usuario al día, con corpus compartido | Igual a los nuevos (105) | Con un usuario no hay nada que compartir; la palanca aparece con decenas de usuarios que sigan las mismas fuentes | Inferencia |
| **Costo de IA por usuario activo al mes** | **USD 4.5 (2026) / 8.9 (2027)** con el diseño actual | 3,200 análisis más 3,200 scores JEV | Media, y es un piso |
| Infraestructura | USD 0 hoy; USD 45 al mes si se cobra | Vercel Hobby y Supabase Free hoy. Vercel Pro USD 20 y Supabase Pro USD 25 (vercel.com/pricing y supabase.com/pricing, 11-oct-2026) | Media: confirmar el plan real en ambos dashboards |
| Margen del plan Pro | Sin calcular | No hay precio fijado todavía | n/a |

## El costo medido es un piso

`app/ai_engine.py:243` registra `candidatesTokenCount` y deja fuera `thoughtsTokenCount`. Gemini 3.x razona por default y ese razonamiento se factura como salida. Además `AI_PRICE_IN_PER_MTOK` y `AI_PRICE_OUT_PER_MTOK` no están configurados, así que `ai_usage.cost_usd` sale en 0 para todo Gemini. Hasta corregir las dos cosas, el costo real por usuario es mayor que la tabla.

También hubo 20 errores de IA en 4 horas (`gemini-3.8-flash`, `analyze_content`, canary).

## Escenarios por usuario activo al mes (100 fuentes)

| Escenario | 2026 | 2027 |
|---|---|---|
| Analizar todo con Gemini 3.8 Flash, más JEV (hoy) | USD 4.53 | USD 8.97 |
| Analizar solo el top 20 diario de JEV, con Gemini 3.8 Flash | USD 0.93 | USD 1.77 |
| Top 20 diario con Claude Haiku 5.5 (0.10 / 0.50 por MTok, skill `claude-api`, caché 6-oct-2026) | USD 0.19 | USD 0.19 |
| Top 20 diario con Gemini 2.5 Flash-Lite (0.10 / 0.40) | USD 0.18 | USD 0.18 |

Los escenarios con otro modelo usan los tokens de Gemini; hay que re-medir con `count_tokens` porque el tokenizador cambia. Cambiar de modelo o el criterio de qué se analiza es cambio al prompt de IA: pregunta primero.

## Dato medido previo

- **Score personal con JEV** (`typesafe/jev-1.13`, 8-oct-2026): USD 0.024 por 1,000 items, 0.4 s de latencia mediana. Producción del 11-oct lo confirma: USD 0.026 por 1,000.

## Palancas de costo, en orden de impacto

1. **Analizar solo lo que JEV pone arriba.** Baja el costo cerca de 5 veces sin cambiar de modelo. JEV ya califica todo por USD 0.08 al mes.
2. **Modelo.** Haiku 5.5 o Flash-Lite bajan otras 5 veces. El precio de Gemini 3.x Flash se duplica el 1-ene-2027.
3. **Corpus compartido.** El resumen de un artículo se calcula una vez para todos. Pesa con escala, no con diez usuarios.
4. **Tope por plan.** Sin límite de fuentes, un usuario con 1,300 fuentes cuesta 13 veces el de referencia.

## Medición 11-oct-2026

- Producción: 1 usuario (el dueño), 172 fuentes, 16,095 items, 774 con análisis de IA.
- `ai_usage`: 320 filas, todas del 11-oct entre 15:36 y 19:25 UTC. La tabla empezó a registrar ese día; la rutina `routines/02-costo-ia.md` da la serie a partir de ahora.

## Regla

Ningún cambio que suba el costo por usuario entra sin declarar cuánto lo sube. Está en `REVIEW.md`.
