---
name: ff-customers
description: Lee las notas de clientes de FocusFeed y extrae lenguaje literal, objeciones repetidas y disparadores de compra. Usar antes de escribir copy de landing, guiones de demo o anuncios, y cuando haya que priorizar el roadmap con evidencia.
---

# Lenguaje del comprador

Lee todo `customers/`. No parafrasees: el valor está en las palabras exactas.

Devuelve cuatro bloques:

**Cómo describen el problema.** Frases literales, con quién las dijo. Ordena por frecuencia.

**Objeciones.** Lo que impide que paguen, con la cita textual. Marca cuáles son de producto y cuáles de precio o confianza.

**Disparadores de compra.** Qué estaba pasando cuando decidieron buscar una solución.

**Palabras que nosotros usamos y ellos no.** Contrasta el vocabulario de `app/static/index.html` y de la landing contra el de las notas. Esta lista es la que corrige la copy.

Si `customers/` tiene menos de tres conversaciones, dilo al inicio y no inventes patrones a partir de una sola.
