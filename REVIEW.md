# Estándar de revisión

Contra esto se juzga cualquier cambio antes de subirlo. Claude debe usar este archivo cuando se le pida revisar.

## Siempre

- ¿Coincide con el objetivo vigente de `ROADMAP.md`?
- ¿Es lo bastante chico para revisarlo en un diff de una sentada?
- ¿Tocó archivos fuera del alcance del ticket? Si sí, explicar por qué.
- ¿Sigue funcionando el flujo principal: sincronizar, ver el feed, marcar visto, guardar, exportar al vault?
- ¿Se ve correcto a 375px?
- ¿Consola limpia, sin scroll horizontal?
- ¿Se agregó complejidad que nadie pidió?

## Específico de este producto

- ¿Un visitante nuevo entiende la oferta en 5 segundos?
- ¿Algún dato de un usuario puede terminar en la pantalla de otro?
- ¿Este cambio sube el costo de IA por usuario? Si sí, cuánto y por qué vale la pena.
- ¿El export al vault sigue generando frontmatter válido, sin duplicar notas?
- ¿Se rompe la PWA ya instalada en iPhone?
- ¿La copy usa el lenguaje que el comprador usa, o el que nosotros usamos internamente?

## Riesgo

- Si el cambio toca autenticación, cobros o datos de producción: ultra review antes de subir.
- Si hay migración de esquema: debe existir el rollback escrito antes de correr la migración.
- Si se toca el prompt de IA: reportar el efecto esperado en costo.

## Formato de salida

Separar los hallazgos en tres grupos, en este orden:

- **Must fix**: bloquea el deploy.
- **Should fix**: no bloquea, pero se registra en `ROADMAP.md` como deuda.
- **Ok to ship**: observado y aceptado.
