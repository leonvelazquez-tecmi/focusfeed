-- FocusFeed v3: sincronización condicional (ETag / Last-Modified).
-- Permite que la sync salte feeds sin cambios (HTTP 304) sin descargarlos ni parsearlos.
-- Idempotente: se puede correr más de una vez.

alter table feeds add column if not exists etag text;
alter table feeds add column if not exists last_modified text;
