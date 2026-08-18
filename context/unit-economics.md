# Unit economics

Este archivo decide si el negocio existe. Hoy está sin llenar y esa es la deuda más cara del proyecto.

## Números que hay que tener antes de fijar precio

| Métrica | Valor | Cómo se obtiene |
|---|---|---|
| Items nuevos por usuario al día | pendiente | Contar inserciones de `batch_save_items` para una cuenta con 100 fuentes |
| Costo de análisis por item | pendiente | Tokens de entrada y salida por llamada, contra el precio vigente del modelo |
| Items analizados por usuario al día, con corpus compartido | pendiente | Solo los que ningún otro usuario ya analizó, más el scoring personal |
| Costo de IA por usuario activo al mes | pendiente | Producto de lo anterior |
| Margen del plan Pro | pendiente | Precio menos costo de IA menos infraestructura |

## Palancas de costo, en orden de impacto

1. **Corpus compartido.** El resumen de un artículo se calcula una vez para todos. Sin esto, mil usuarios siguiendo Stratechery pagan mil análisis del mismo texto. Es T1.
2. **Scoring personal barato.** El score de relevancia es lo único que cambia entre usuarios. Se resuelve con embeddings del perfil contra embeddings del item, y solo el top del día pasa por el modelo grande.
3. **Tope por plan.** Sin límite de fuentes y de análisis diarios, un solo usuario con 300 fuentes rompe el mes.
4. **Modelo.** Cargar la skill `claude-api` para tomar IDs y precios vigentes antes de elegir. No estimar de memoria.

## Regla

Ningún cambio que suba el costo por usuario entra sin declarar cuánto lo sube. Está en `REVIEW.md`.
