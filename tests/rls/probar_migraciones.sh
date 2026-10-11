#!/usr/bin/env bash
# Prueba las migraciones de T2 en un Postgres local:
#   v1 → v2 → v4 → reasignar dueño → rollback v4 → rollback v2
#   y aparte: respaldo de v1 → v2 → v4 → limpiar → restaurar.
#
#   PGHOST=/tmp PGPORT=5433 PGUSER=postgres bash tests/rls/probar_migraciones.sh
#
# Crea y borra la base desechable `ff_rls_test`. Nunca toca Supabase.
# Los roles que imitan a Supabase (anon, ff_dueno, supabase_admin...) son de
# todo el servidor: se borran al salir los que este script creó. Úsalo contra
# un Postgres local de prueba, no contra uno que ya tenga esos roles en uso.
set -euo pipefail
export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"

RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
AQUI="$RAIZ/tests/rls"
BD=ff_rls_test
DUENO=00000000-0000-0000-0000-000000000001
OTRO=22222222-2222-2222-2222-222222222222
REAL=33333333-3333-3333-3333-333333333333
TMP="$(mktemp -d)"
FALLAS=0

# q: superusuario, para preparar escenarios. d: ff_dueno, el `postgres` de
# Supabase (sin superusuario): así corren las migraciones, como en el SQL editor.
q()  { psql -X -q -v ON_ERROR_STOP=1 -d "$BD" "$@"; }
d()  { psql -X -q -v ON_ERROR_STOP=1 -U ff_dueno -d "$BD" "$@"; }
val(){ psql -X -q -t -A -v ON_ERROR_STOP=1 -d "$BD" -c "$1"; }

# Corre SQL con un rol de API. Devuelve el resultado, "DENEGADO" si Postgres
# negó el permiso, o "ERROR: ..." para cualquier otro error (que no cuenta
# como denegado: así un error de sintaxis no pasa por seguridad).
como() {
  local rol=$1 sub=$2 sql=$3 out
  if out=$(psql -X -q -t -A -v ON_ERROR_STOP=1 -d "$BD" 2>"$TMP/err" <<SQL
SET request.jwt.claim.sub = '$sub';
SET ROLE $rol;
$sql
SQL
  ); then
    echo "$out"
  elif grep -q "permission denied" "$TMP/err"; then
    echo DENEGADO
  else
    echo "ERROR: $(head -1 "$TMP/err")"
  fi
}

# Corre un archivo SQL. Devuelve "ok" o el texto de la excepción.
correr() {
  if psql -X -q -v ON_ERROR_STOP=1 -U ff_dueno -d "$BD" >/dev/null 2>"$TMP/err"; then
    echo ok
  else
    grep -m1 -o "ERROR: .*" "$TMP/err" | sed 's/^ERROR:  *//'
  fi
}
# El restore del runbook: limpiar + restore.sql en una sola transacción.
restaurar() {
  local dump=$1 filtro=${2:-' DEFAULT ACL '}
  pg_restore -l "$dump" | grep -v ' SCHEMA - public ' | grep -v "$filtro" > "$TMP/lista"
  pg_restore -L "$TMP/lista" -f "$TMP/restore.sql" "$dump"
  if psql -X -q -U ff_dueno -d "$BD" --single-transaction -v ON_ERROR_STOP=1 \
       -f "$RAIZ/supabase_limpiar_para_restaurar.sql" -f "$TMP/restore.sql" >/dev/null 2>"$TMP/err"; then
    echo ok
  else
    grep -m1 -o "ERROR: .*" "$TMP/err" | sed 's/^ERROR:  *//'
  fi
}
reasignar() { sed "s/REEMPLAZA-CON-TU-UUID/$REAL/" "$RAIZ/supabase_reasignar_dueno.sql" | correr; }

check() {
  local nombre=$1 esperado=$2 obtenido=$3
  if [[ "$obtenido" == "$esperado" ]]; then
    echo "  [ok  ] $nombre"
  else
    echo "  [FALLA] $nombre · esperado: $esperado · obtenido: $obtenido"
    FALLAS=$((FALLAS + 1))
  fi
}
contiene() {
  local nombre=$1 esperado=$2 obtenido=$3
  if [[ "$obtenido" == *"$esperado"* ]]; then
    echo "  [ok  ] $nombre"
  else
    echo "  [FALLA] $nombre · esperado que contenga: $esperado · obtenido: $obtenido"
    FALLAS=$((FALLAS + 1))
  fi
}

nueva_bd() {
  dropdb --if-exists "$BD" >/dev/null
  createdb "$BD"
  q -f "$AQUI/supabase_shim.sql" >/dev/null
  q -c "ALTER DATABASE $BD OWNER TO ff_dueno; ALTER SCHEMA public OWNER TO ff_dueno;" >/dev/null
  d -f "$RAIZ/supabase_schema.sql" >/dev/null
  d -f "$AQUI/datos_v1.sql" >/dev/null
}
estados() { val "SELECT string_agg(status || ':' || n, ',' ORDER BY status) FROM (SELECT status, COUNT(*) n FROM items GROUP BY 1) s"; }

ROLES_SHIM="anon authenticated service_role supabase_admin ff_dueno"
ROLES_PREVIOS=$(psql -X -t -A -d postgres -c "SELECT string_agg(rolname, ' ') FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role','supabase_admin','ff_dueno')")
limpiar_todo() {
  dropdb --if-exists ff_rls_test >/dev/null 2>&1
  for r in $ROLES_SHIM; do
    [[ " $ROLES_PREVIOS " == *" $r "* ]] && continue
    psql -X -q -d postgres -c "DROP ROLE IF EXISTS $r" >/dev/null 2>&1 || true
  done
  rm -rf "$TMP"
}
trap limpiar_todo EXIT

# ----------------------------------------------------------
nueva_bd
echo "v1 (lo que hoy vive en producción)"
check "anon lee items en v1 (el hueco)" "12" "$(como anon '' 'SELECT COUNT(*) FROM public.items;')"
ESTADOS_V1=$(estados)

d -f "$RAIZ/supabase_schema_v2.sql" >/dev/null
echo "v2"
check "anon lee el respaldo del perfil (hueco de v2)" "1" \
  "$(como anon '' 'SELECT COUNT(*) FROM public.user_profile_v1_backup;')"
check "otra cuenta lista todas las fuentes (hueco de v2)" "2" \
  "$(como authenticated $OTRO 'SELECT COUNT(*) FROM public.feeds;')"

# Correr v4 dos veces prueba que es idempotente.
check "v4 corre" "ok" "$(correr < "$RAIZ/supabase_migration_v4_rls.sql")"
check "v4 corre dos veces" "ok" "$(correr < "$RAIZ/supabase_migration_v4_rls.sql")"
echo "v4"
for t in feeds items subscriptions user_items ai_usage user_profile; do
  check "anon no lee $t" "DENEGADO" "$(como anon '' "SELECT COUNT(*) FROM public.$t;")"
  check "authenticated no lee $t" "DENEGADO" "$(como authenticated $OTRO "SELECT COUNT(*) FROM public.$t;")"
done
check "authenticated no escribe user_items" "DENEGADO" \
  "$(como authenticated $OTRO "INSERT INTO public.user_items (user_id, item_id) VALUES ('$OTRO', 1);")"
for f in "get_user_feed(UUID, TEXT, TEXT, INTEGER)" "get_user_counts(UUID)" "get_unanalyzed_items(INTEGER)"; do
  check "anon sin EXECUTE en ${f%%(*}" "f" \
    "$(val "SELECT has_function_privilege('anon', 'public.$f', 'EXECUTE')")"
  check "authenticated sin EXECUTE en ${f%%(*}" "f" \
    "$(val "SELECT has_function_privilege('authenticated', 'public.$f', 'EXECUTE')")"
done
check "respaldos fuera de public" "0" \
  "$(val "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE '%_v1_backup'")"
check "respaldos en private" "3" \
  "$(val "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='private' AND table_name LIKE '%_v1_backup'")"
check "anon no ve el esquema private" "DENEGADO" \
  "$(como anon '' 'SELECT COUNT(*) FROM private.user_profile_v1_backup;')"
check "0 permisos de tabla para anon/authenticated" "0" \
  "$(val "SELECT COUNT(*) FROM information_schema.role_table_grants WHERE table_schema='public' AND grantee IN ('anon','authenticated')")"
check "columnas etag y last_modified" "2" \
  "$(val "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='feeds' AND column_name IN ('etag','last_modified')")"

echo "v4 · lo que se cree después tampoco nace abierto"
d -c "CREATE FUNCTION public.rpc_futura() RETURNS BIGINT LANGUAGE sql SECURITY DEFINER AS 'SELECT COUNT(*) FROM public.user_items';
      CREATE TABLE public.tabla_futura (id INT);" >/dev/null
check "anon sin EXECUTE en una función nueva" "f" \
  "$(val "SELECT has_function_privilege('anon', 'public.rpc_futura()', 'EXECUTE')")"
check "anon no ejecuta una RPC nueva con SECURITY DEFINER" "DENEGADO" \
  "$(como anon '' 'SELECT public.rpc_futura();')"
check "anon no lee una tabla nueva" "DENEGADO" "$(como anon '' 'SELECT COUNT(*) FROM public.tabla_futura;')"
check "authenticated no lee una tabla nueva" "DENEGADO" \
  "$(como authenticated $OTRO 'SELECT COUNT(*) FROM public.tabla_futura;')"
d -c "DROP FUNCTION public.rpc_futura(); DROP TABLE public.tabla_futura;" >/dev/null

echo "v4 · el backend (service_role) sigue funcionando"
check "service_role lee el feed del dueño (12 items, 4 archivados)" "8" \
  "$(como service_role '' "SELECT COUNT(*) FROM public.get_user_feed('$DUENO'::uuid, 'curated', 'all', 500);")"
check "segunda cuenta guarda perfil sin chocar con id=1" "1" \
  "$(como service_role '' "INSERT INTO public.user_profile (user_id, focus_topics) VALUES ('$OTRO', '[]') RETURNING (id > 1)::int;" | head -1)"
check "la cuenta nueva no hereda nombre" "" \
  "$(val "SELECT COALESCE(user_name, '') FROM public.user_profile WHERE user_id = '$OTRO'")"
check "service_role suscribe a la cuenta nueva" "1" \
  "$(como service_role '' "INSERT INTO public.subscriptions (user_id, feed_id) VALUES ('$OTRO', 2) RETURNING 1;" | head -1)"
check "la cuenta nueva solo ve su feed" "6" \
  "$(como service_role '' "SELECT COUNT(*) FROM public.get_user_feed('$OTRO'::uuid, 'curated', 'all', 500);")"
check "service_role ejecuta get_unanalyzed_items" "5" \
  "$(como service_role '' "SELECT COUNT(*) FROM public.get_unanalyzed_items(5);")"

# ----------------------------------------------------------
echo "reasignar dueño a su uuid real"
contiene "sin entrar con Google, aborta" "entra primero con Google" "$(reasignar)"

# La cuenta real entró: recibió una fuente que el dueño ya seguía (1), una
# que no (2), un item que el dueño también tenía y uno nuevo.
ITEM_COMUN=$(val "SELECT item_id FROM public.user_items WHERE user_id = '$DUENO' ORDER BY item_id LIMIT 1")
ITEM_NUEVO=$(val "SELECT id FROM public.items WHERE id NOT IN (SELECT item_id FROM public.user_items WHERE user_id = '$DUENO') LIMIT 1")
ITEM_NUEVO=${ITEM_NUEVO:-$(val "SELECT MAX(id) FROM public.items")}
q -c "INSERT INTO auth.users (id, email) VALUES ('$REAL', 'dueno@ejemplo.com');
      INSERT INTO public.subscriptions (user_id, feed_id) VALUES ('$REAL', 1), ('$REAL', 2);
      INSERT INTO public.user_items (user_id, item_id, status) VALUES ('$REAL', $ITEM_COMUN, 'reading')
        ON CONFLICT DO NOTHING;
      INSERT INTO public.user_profile (user_id, focus_topics) VALUES ('$REAL', '[\"ia\"]');" >/dev/null
# Que el item "nuevo" exista solo en la cuenta real
q -c "DELETE FROM public.user_items WHERE user_id = '$DUENO' AND item_id = $ITEM_NUEVO;
      INSERT INTO public.user_items (user_id, item_id, status) VALUES ('$REAL', $ITEM_NUEVO, 'reading');" >/dev/null
# Quitar la fuente 2 al dueño para que la unión se note
q -c "DELETE FROM public.subscriptions WHERE user_id = '$DUENO' AND feed_id = 2;" >/dev/null
# Categoría propia del dueño en la fuente 1; la cuenta nueva trae la semilla.
q -c "UPDATE public.subscriptions SET custom_category = 'Mi IA' WHERE user_id = '$DUENO' AND feed_id = 1;
      UPDATE public.subscriptions SET custom_category = 'IA & Agentes' WHERE user_id = '$REAL' AND feed_id = 1;" >/dev/null
# Un item que el dueño guardó con like y comentario, y que la cuenta nueva
# solo tiene por el score automático de IA al entrar.
ITEM_AUTO=$(val "SELECT item_id FROM public.user_items WHERE user_id = '$DUENO' AND item_id NOT IN ($ITEM_COMUN, $ITEM_NUEVO) ORDER BY item_id LIMIT 1")
q -c "UPDATE public.user_items SET status = 'reading', user_rating = 'love', user_feedback_comment = 'clave',
                                  relevance_score = 20, curator_note = 'nota real'
       WHERE user_id = '$DUENO' AND item_id = $ITEM_AUTO;
      INSERT INTO public.user_items (user_id, item_id, relevance_score, curator_note)
       VALUES ('$REAL', $ITEM_AUTO, 91, 'nota IA');" >/dev/null
UNION_ITEMS=$(val "SELECT COUNT(DISTINCT item_id) FROM public.user_items WHERE user_id IN ('$DUENO', '$REAL')")

# Like nuevo sobre un item que el dueño tenía guardado: el guardado se queda.
ITEM_LIKE=$(val "SELECT item_id FROM public.user_items WHERE user_id = '$DUENO' AND item_id NOT IN ($ITEM_COMUN, $ITEM_NUEVO, $ITEM_AUTO) ORDER BY item_id LIMIT 1")
# "Visto" nuevo (archived) sobre un item que el dueño tenía con like: el like se queda.
ITEM_VISTO=$(val "SELECT item_id FROM public.user_items WHERE user_id = '$DUENO' AND item_id NOT IN ($ITEM_COMUN, $ITEM_NUEVO, $ITEM_AUTO, $ITEM_LIKE) ORDER BY item_id LIMIT 1")
q -c "UPDATE public.user_items SET status = 'reading', user_rating = 'dislike', user_feedback_comment = 'clickbait'
       WHERE user_id = '$DUENO' AND item_id = $ITEM_LIKE;
      INSERT INTO public.user_items (user_id, item_id, status, user_rating, user_feedback_comment)
       VALUES ('$REAL', $ITEM_LIKE, 'inbox', 'like', '');
      UPDATE public.user_items SET status = 'inbox', user_rating = 'love'
       WHERE user_id = '$DUENO' AND item_id = $ITEM_VISTO;
      INSERT INTO public.user_items (user_id, item_id, status) VALUES ('$REAL', $ITEM_VISTO, 'archived');" >/dev/null

contiene "si la cuenta nueva ya tiene temas, aborta" "ya tiene temas propios" "$(reasignar)"
q -c "UPDATE public.user_profile SET focus_topics = '[]' WHERE user_id = '$REAL';" >/dev/null
check "reasignación corre" "ok" "$(reasignar)"
check "fuentes: la unión de las dos cuentas" "2" \
  "$(val "SELECT COUNT(*) FROM public.subscriptions WHERE user_id = '$REAL'")"
check "items: la unión de las dos cuentas" "$UNION_ITEMS" \
  "$(val "SELECT COUNT(*) FROM public.user_items WHERE user_id = '$REAL'")"
check "en empate gana la cuenta nueva" "reading" \
  "$(val "SELECT status FROM public.user_items WHERE user_id = '$REAL' AND item_id = $ITEM_COMUN")"
check "el score automático no pisa tu guardado, like, comentario ni tu score real" "reading|love|clave|20|nota real" \
  "$(val "SELECT status || '|' || user_rating || '|' || user_feedback_comment || '|' || relevance_score || '|' || curator_note FROM public.user_items WHERE user_id = '$REAL' AND item_id = $ITEM_AUTO")"
check "like nuevo no borra el guardado viejo; el comentario viejo se va con su dislike" "reading|like|" \
  "$(val "SELECT status || '|' || user_rating || '|' || user_feedback_comment FROM public.user_items WHERE user_id = '$REAL' AND item_id = $ITEM_LIKE")"
check "visto nuevo no borra el like viejo" "archived|love" \
  "$(val "SELECT status || '|' || user_rating FROM public.user_items WHERE user_id = '$REAL' AND item_id = $ITEM_VISTO")"
check "si las dos siguen la fuente, se queda tu categoría" "Mi IA" \
  "$(val "SELECT custom_category FROM public.subscriptions WHERE user_id = '$REAL' AND feed_id = 1")"
check "lo que solo hizo la cuenta nueva se conserva" "reading" \
  "$(val "SELECT status FROM public.user_items WHERE user_id = '$REAL' AND item_id = $ITEM_NUEVO")"
check "nada queda en el provisional" "0" \
  "$(val "SELECT (SELECT COUNT(*) FROM public.subscriptions WHERE user_id = '$DUENO') + (SELECT COUNT(*) FROM public.user_items WHERE user_id = '$DUENO')")"
check "un solo perfil, el viejo (con tus temas de v1), en plan pro" "1|pro|true" \
  "$(val "SELECT COUNT(*) || '|' || MAX(plan) || '|' || bool_or(focus_topics LIKE '%Inteligencia Artificial%') FROM public.user_profile WHERE user_id = '$REAL'")"
check "la otra cuenta intacta" "1" "$(val "SELECT COUNT(*) FROM public.subscriptions WHERE user_id = '$OTRO'")"
contiene "correrlo dos veces aborta" "ya se reasignó" "$(reasignar)"

# ----------------------------------------------------------
echo "rollback v2 · protecciones"
d -f "$RAIZ/supabase_rollback_v4.sql" >/dev/null
contiene "con otras cuentas en la base, aborta" "otras cuentas" \
  "$(sed "s/dueno UUID := '$DUENO'/dueno UUID := '$REAL'/" "$RAIZ/supabase_rollback_v2.sql" | correr)"
q -c "DELETE FROM public.subscriptions WHERE user_id = '$OTRO'; DELETE FROM public.user_profile WHERE user_id = '$OTRO';
      INSERT INTO public.user_items (user_id, item_id, status) VALUES ('$OTRO', 1, 'archived');" >/dev/null
contiene "con estado de lectura de otra cuenta (sin fuentes ni perfil), aborta" "otras cuentas" \
  "$(sed "s/dueno UUID := '$DUENO'/dueno UUID := '$REAL'/" "$RAIZ/supabase_rollback_v2.sql" | correr)"
q -c "DELETE FROM public.user_items WHERE user_id = '$OTRO';" >/dev/null
ANTES_ROLLBACK=$(val "SELECT COUNT(*) FROM public.user_items")
contiene "con el uuid del dueño equivocado, aborta en vez de borrar" "Ningún user_item es de" \
  "$(correr < "$RAIZ/supabase_rollback_v2.sql")"
check "user_items sigue ahí tras abortar (y no estaba vacía)" "$ANTES_ROLLBACK|true" \
  "$(val "SELECT COUNT(*) || '|' || (COUNT(*) > 0) FROM public.user_items")"

# ----------------------------------------------------------
echo "rollback v4 (de vuelta al estado de v2)"
nueva_bd
d -f "$RAIZ/supabase_schema_v2.sql" >/dev/null
d -f "$RAIZ/supabase_migration_v4_rls.sql" >/dev/null
check "rollback v4 corre" "ok" "$(correr < "$RAIZ/supabase_rollback_v4.sql")"
check "respaldos de vuelta en public" "3" \
  "$(val "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE '%_v1_backup'")"
check "authenticated vuelve a leer feeds" "2" \
  "$(como authenticated $OTRO 'SELECT COUNT(*) FROM public.feeds;')"
check "rollback v4 borra private si quedó vacío" "" "$(val "SELECT to_regnamespace('private')")"
check "RLS de v2 sigue aislando user_items" "0" \
  "$(como authenticated $OTRO 'SELECT COUNT(*) FROM public.user_items;')"
d -c "CREATE SCHEMA IF NOT EXISTS private; CREATE TABLE private.items_v1_backup (id INT);" >/dev/null
contiene "si el respaldo existe en los dos esquemas, aborta" "revisa a mano" \
  "$(correr < "$RAIZ/supabase_rollback_v4.sql")"
d -c "DROP TABLE private.items_v1_backup; DROP SCHEMA private;" >/dev/null

echo "rollback v2 (de vuelta a v1)"
check "rollback v2 corre" "ok" "$(correr < "$RAIZ/supabase_rollback_v2.sql")"
check "estado personal de vuelta en items" "$ESTADOS_V1" "$(estados)"
check "subscriptions borrada" "" "$(val "SELECT to_regclass('public.subscriptions')")"
check "perfil del dueño con id=1" "1" "$(val "SELECT COUNT(*) FROM user_profile WHERE id = 1")"

# ----------------------------------------------------------
echo "respaldo y restauración (camino del runbook)"
nueva_bd
pg_dump -U ff_dueno -d "$BD" --schema=public --no-owner -Fc -f "$TMP/pre.dump"
check "el respaldo trae default privileges de supabase_admin (la trampa)" "si" \
  "$(pg_restore -l "$TMP/pre.dump" | grep -q 'DEFAULT ACL.*supabase_admin' && echo si || echo no)"
d -f "$RAIZ/supabase_schema_v2.sql" >/dev/null
d -f "$RAIZ/supabase_migration_v4_rls.sql" >/dev/null
check "sin el filtro de DEFAULT ACL, falla (como en Supabase)" "t" \
  "$([[ "$(restaurar "$TMP/pre.dump" 'NO-FILTRAR-NADA')" != ok ]] && echo t || echo f)"
check "y como es una sola transacción, no borró nada" "2" "$(val "SELECT COUNT(*) FROM public.feeds")"
check "con el comando del runbook, restaura" "ok" "$(restaurar "$TMP/pre.dump")"
check "items con su columna status de v1" "$ESTADOS_V1" "$(estados)"
check "sin restos de v2" "" "$(val "SELECT to_regclass('public.subscriptions')")"
check "sin esquema private" "" "$(val "SELECT to_regnamespace('private')")"
check "perfil de v1 de vuelta" "1" "$(val "SELECT COUNT(*) FROM user_profile WHERE id = 1")"
check "permisos de v1 de vuelta (anon lee items)" "12" "$(como anon '' 'SELECT COUNT(*) FROM public.items;')"

echo "respaldo tomado con v2 ya aplicado, restaurado después de v4"
nueva_bd
d -f "$RAIZ/supabase_schema_v2.sql" >/dev/null
pg_dump -U ff_dueno -d "$BD" --schema=public --no-owner -Fc -f "$TMP/pre_v2.dump"
d -f "$RAIZ/supabase_migration_v4_rls.sql" >/dev/null
check "restaura" "ok" "$(restaurar "$TMP/pre_v2.dump")"
check "el backend vuelve a leer el feed tras restaurar" "8" \
  "$(como service_role '' "SELECT COUNT(*) FROM public.get_user_feed('$DUENO'::uuid, 'curated', 'all', 500);")"
check "v4 vuelve a correr sobre lo restaurado" "ok" "$(correr < "$RAIZ/supabase_migration_v4_rls.sql")"
check "y anon vuelve a quedar fuera" "DENEGADO" "$(como anon '' 'SELECT COUNT(*) FROM public.items;')"

echo "restaurar con una cuenta de Google ya creada"
q -c "INSERT INTO auth.users (id, email) VALUES ('$REAL', 'dueno@ejemplo.com');" >/dev/null
contiene "la limpieza se niega" "Ya hay cuentas en auth.users" "$(restaurar "$TMP/pre_v2.dump")"
check "y no borró nada" "2" "$(val "SELECT COUNT(*) FROM public.feeds")"

echo "reasignar cuando el uuid viejo no tiene perfil"
nueva_bd
d -f "$RAIZ/supabase_schema_v2.sql" >/dev/null
d -f "$RAIZ/supabase_migration_v4_rls.sql" >/dev/null
q -c "DELETE FROM public.user_profile WHERE user_id = '$DUENO';
      INSERT INTO auth.users (id, email) VALUES ('$REAL', 'dueno@ejemplo.com');
      INSERT INTO public.user_profile (user_id, learned_preferences) VALUES ('$REAL', '{\"aprendido\": 1}');" >/dev/null
check "reasignación corre" "ok" "$(reasignar)"
check "se conserva el perfil de la cuenta nueva, en plan pro" "1|pro|t" \
  "$(val "SELECT COUNT(*) || '|' || MAX(plan) || '|' || bool_or(learned_preferences LIKE '%aprendido%') FROM public.user_profile WHERE user_id = '$REAL'" | sed 's/true$/t/')"

echo
if [[ $FALLAS -eq 0 ]]; then echo "Todo en verde."; else echo "$FALLAS fallas."; exit 1; fi
