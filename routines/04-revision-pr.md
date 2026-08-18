# Rutina: revisión automática de pull request

**Cuándo:** al abrir un PR
**Escribe en:** comentario del PR

## Prompt

```
Revisa este pull request usando REVIEW.md como estándar.

Comenta solo sobre lo que pueda causar bugs, romper flujos de usuario,
crear riesgos de seguridad o filtrar datos entre cuentas.

Presta atención especial a:
- Consultas que lean o escriban sin filtrar por user_id
- Cambios que suban el costo de IA por usuario
- Migraciones de esquema sin rollback escrito

Cierra con un resumen corto: qué se ve bien, qué necesita atención,
y si está listo para revisión humana.
```

## Nota

Se implementa como GitHub Action cuando el repo esté en la cuenta personal. Mientras tanto se corre a mano con `/code-review` antes de cada push.
