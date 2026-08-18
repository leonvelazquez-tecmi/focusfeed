# Rutina: reporte de costo de IA

**Cuándo:** diario, 23:00
**Escribe en:** `context/unit-economics.md`, sección "Medición"
**Avisa si:** el costo por usuario activo al mes supera el 30% del precio del plan

## Prompt

```
Consulta GET /api/usage del entorno de producción y la tabla ai_usage.

Actualiza la sección de medición de context/unit-economics.md con:
- Llamadas al modelo en las últimas 24 horas
- Tokens de entrada y de salida
- Costo total del día
- Usuarios activos y costo por usuario activo
- Proyección a 30 días por usuario

Compara la proyección contra el precio del plan Pro que esté vigente en
ROADMAP.md. Si el costo por usuario supera el 30% de ese precio, escribe
una alerta en la primera línea del archivo explicando qué la disparó.

No edites código. Si el costo subió contra ayer, di qué cambio del repo
en las últimas 24 horas lo explica.
```

## Por qué existe

Esta rutina no viene del video. Un empleado de IA que trabaja 24/7 factura 24/7, y el costo variable es lo que decide si el negocio existe. Va al mismo nivel de prioridad que el brief matutino.
