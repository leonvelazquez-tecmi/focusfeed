# routines/

Los prompts recurrentes que corren solos, en `/schedule` o como GitHub Action.

## Planeadas

| Rutina | Cuándo | Escribe en | Regla |
|---|---|---|---|
| Brief matutino | 7:00, entre semana | `context/morning-brief.md` | Prohibido editar código |
| Reporte de costo de IA | Diario | `context/unit-economics.md` | Avisa si el margen baja del umbral |
| Ops semanal | Viernes 15:00 | `context/weekly-ops.md` | Prohibido editar código |
| Revisión de PR | Al abrir un PR | Comentario en el PR | Usa `REVIEW.md` como estándar |

Cada rutina vive aquí como archivo `.md` con su prompt exacto, para poder versionarla y corregirla.
