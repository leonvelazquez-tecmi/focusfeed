-- Rollback de supabase_migration_v9_unscored_items.sql.
-- Quita la función get_unscored_items. No toca tablas ni datos.
-- Idempotente: IF EXISTS. Sin la función, el código vuelve solo al comportamiento
-- anterior (ventana de 160 items, 40 por llamada); no falla.

DROP FUNCTION IF EXISTS public.get_unscored_items(UUID, INTEGER, INTEGER);
