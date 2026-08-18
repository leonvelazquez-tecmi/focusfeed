---
name: ff-costo
description: Calcula el costo de IA por usuario activo de FocusFeed y lo contrasta contra el precio del plan. Usar antes de fijar precio, después de cambiar el prompt o el modelo, y cuando haya que decidir si una función nueva cabe en el margen.
---

# Costo por usuario

1. Consulta `GET /api/usage` y la tabla `ai_usage`.
2. Calcula: llamadas, tokens de entrada y salida, costo total, usuarios activos, costo por usuario.
3. Proyecta a 30 días por usuario activo.
4. Contrasta contra el precio del plan Pro vigente en `ROADMAP.md`.

Reporta también las tres palancas y su estado actual:

- **Corpus compartido**: ¿cuántos items se analizaron una vez y sirvieron a más de un suscriptor? Consulta cuántos suscriptores tiene cada feed.
- **Scoring barato**: ¿el score personal está pasando por el modelo grande o por la ruta determinista de `score_for_user`?
- **Tope por plan**: ¿hay algún usuario cuyo consumo pase del límite de su plan?

Actualiza `context/unit-economics.md` con los números. Si el costo por usuario supera el 30% del precio, di explícitamente qué recortarías primero.
