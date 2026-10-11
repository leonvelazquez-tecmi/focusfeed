# Runbook T2 · Base de datos lista para más de una cuenta

Quién lo corre: León. Claude no toca producción.
Cuándo: antes de mergear el PR del login (PR B). Mientras tanto la app sigue entrando como el dueño, igual que hoy.

Por qué este orden: el login pone la llave `anon` de Supabase en el navegador, donde cualquiera la puede leer. Con el esquema v1 de producción (RLS apagado y `GRANT ALL` a `anon`), esa llave da acceso directo a toda la base. Esta migración cierra eso primero.

Archivos:

| Archivo | Para qué |
|---|---|
| `supabase_schema_v2.sql` | Corpus compartido y estado por usuario (T1). Solo si el paso 1 dice que falta. |
| `supabase_migration_v4_rls.sql` | Cierra la base: `anon` y `authenticated` sin permisos, respaldos fuera de la API, perfil multi-cuenta. Incluye v3. |
| `supabase_rollback_v4.sql` | Regresa al estado de v2. |
| `supabase_rollback_v2.sql` | Regresa a v1. Solo con código anterior a T1. |
| `supabase_reasignar_dueno.sql` | Después del login: fusiona tu historial en tu uuid de Google. |
| `supabase_limpiar_para_restaurar.sql` | Solo para restaurar el respaldo. Borra datos. |
| `tests/rls/probar_migraciones.sh` | Prueba todo lo anterior en un Postgres local, incluida la restauración (92 verificaciones, corriendo como un dueño sin superusuario, igual que el SQL editor). |

---

## Paso 0 · Confirmar que el backend usa la service_role

Sin esta variable, el backend habla con Supabase con la llave `anon`, y después de v4 no podría leer nada.

```bash
cd "<carpeta del repo>"
vercel env ls production | grep -E 'SUPABASE_(URL|SERVICE_ROLE_KEY|KEY|ANON_KEY)|FOCUSFEED_OWNER_ID'
```

Debe aparecer `SUPABASE_SERVICE_ROLE_KEY`. Si no aparece, agrégala antes de seguir (Supabase → Project Settings → API Keys → `service_role` / secret):

```bash
read -s K && printf %s "$K" | vercel env add SUPABASE_SERVICE_ROLE_KEY production; unset K
```

Para que la tome, redeploy desde el dashboard: Vercel → focusfeed → Deployments → el deployment marcado como Production → ⋯ → **Redeploy**. No uses `vercel --prod` desde tu carpeta: publicaría lo que tengas en local (otra rama, cambios sin revisar), no lo que ya está en producción.

Si aparece `FOCUSFEED_OWNER_ID`, anota su valor y **no lo cambies todavía**: el paso 1 dice qué hacer con él.

## Paso 1 · Diagnóstico (solo lectura)

El SQL editor solo muestra el resultado de la última consulta, así que cada bloque de este runbook es un solo `SELECT` que devuelve una fila. En Supabase → SQL Editor:

```sql
SELECT current_setting('server_version')                       AS version_pg,
       to_regclass('public.subscriptions') IS NOT NULL         AS v2_aplicado,
       to_regclass('private.items_v1_backup') IS NOT NULL      AS v4_aplicado,
       to_regnamespace('private') IS NOT NULL                  AS existe_private,
       (SELECT COUNT(*) FROM public.feeds)                     AS feeds,
       (SELECT COUNT(*) FROM public.items)                     AS items,
       (SELECT COUNT(*) FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND grantee = 'anon')   AS permisos_anon;
```

Cómo leerlo:

- `v2_aplicado = false` → haz los pasos 2, 3 y 4.
- `v2_aplicado = true` → v2 ya corrió. Salta el paso 3.
- `v4_aplicado = true` → v4 ya corrió. Solo verifica (paso 5).
- `existe_private = true` con `v4_aplicado = false` → alguien más usa ese esquema. v4 se detiene solo si encuentra objetos ajenos; mándame el resultado antes de seguir. (Después de `supabase_rollback_v4.sql` el esquema se borra si quedó vacío, así que no debería aparecer por eso.)
- `permisos_anon` es lo que la llave pública puede hacer hoy. Después de v4 debe ser 0.
- Anota `version_pg`: define qué `pg_dump` instalar.

Si `v2_aplicado = true`, corre también esta, que dice a qué uuid quedó asignado tu historial:

```sql
SELECT string_agg(user_id::text || ' (' || n || ' fuentes)', ', ') AS duenos_v2
  FROM (SELECT user_id, COUNT(*) n FROM subscriptions GROUP BY 1) s;
```

Qué hacer con `FOCUSFEED_OWNER_ID` (la app entra como ese uuid hasta el PR C):

- **v2 no ha corrido:** el paso 3 deja tu historial en `00000000-0000-0000-0000-000000000001`. Antes del paso 3, borra la variable (`vercel env rm FOCUSFEED_OWNER_ID production --yes`) y redeploy desde el dashboard.
- **v2 ya corrió:** el uuid con tus fuentes en `duenos_v2` es tu cuenta vieja. La variable debe valer exactamente eso (o no existir, si es `0000…0001`). Si ya coincide, no toques nada. Si `duenos_v2` muestra un uuid distinto de `0000…0001`, cámbialo también en la línea `viejo` de `supabase_reasignar_dueno.sql` cuando llegues a ese paso.

Mándame las filas si algo no cuadra con estos casos.

## Paso 2 · Respaldo completo

Necesitas `pg_dump` de la **misma versión mayor** que `version_pg` del paso 1 (por ejemplo 15 o 17):

```bash
brew install postgresql@17        # ajusta el número a tu versión
export PATH="$(brew --prefix postgresql@17)/bin:$PATH"
pg_dump --version
```

La cadena de conexión está en Supabase → botón **Connect** → **Session pooler** (puerto 5432). Trae la contraseña adentro, así que entra con `read -s`:

El respaldo trae tus datos personales: va a una carpeta fuera del repo (y `*.dump` está en `.gitignore` por si acaso).

```bash
mkdir -p ~/focusfeed_respaldos && chmod 700 ~/focusfeed_respaldos
F=~/focusfeed_respaldos/focusfeed_pre_T2_$(date +%Y%m%d_%H%M).dump
read -s PGURL
pg_dump "$PGURL" --schema=public --no-owner -Fc -f "$F"
unset PGURL
ls -lh "$F"
pg_restore --list "$F" | grep -c 'TABLE DATA'
echo "$F"
```

El penúltimo comando (el conteo) debe dar al menos 3 (`feeds`, `items`, `user_profile`). El último imprime la ruta del respaldo: anótala, es la que usa la restauración.

## Paso 3 · Correr v2 (solo si el paso 1 lo pidió)

SQL Editor → pega `supabase_schema_v2.sql` completo → Run. No cambies el uuid `0000…0001`: el paso de reasignación lo mueve a tu cuenta real después del login.

Verificación (`subs_igual_feeds` debe ser true y `feed_dueno` mayor que 0):

```sql
SELECT (SELECT COUNT(*) FROM subscriptions) = (SELECT COUNT(*) FROM feeds) AS subs_igual_feeds,
       (SELECT COUNT(*) FROM user_items)                                   AS user_items,
       (SELECT COUNT(*) FROM get_user_feed('00000000-0000-0000-0000-000000000001'::uuid,
                                           'curated', 'all', 5))           AS feed_dueno;
```

Abre la app: el feed debe verse igual que antes.

## Paso 4 · Correr v4

SQL Editor → pega `supabase_migration_v4_rls.sql` completo → Run.

## Paso 5 · Verificar

Una sola fila; las cinco columnas deben decir lo que indica su nombre:

```sql
SELECT (SELECT COUNT(*) FROM information_schema.role_table_grants
         WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated'))       AS permisos_tabla_debe_ser_0,
       (SELECT COUNT(*) FROM information_schema.tables
         WHERE table_schema = 'private' AND table_name LIKE '%_v1_backup')            AS respaldos_en_private_debe_ser_3,
       (SELECT COUNT(*) FROM information_schema.tables
         WHERE table_schema = 'public' AND table_name LIKE '%_v1_backup')             AS respaldos_en_public_debe_ser_0,
       (SELECT bool_and(relrowsecurity) FROM pg_class
         WHERE relnamespace = 'public'::regnamespace
           AND relname IN ('feeds','items','subscriptions','user_items','ai_usage','user_profile')) AS rls_debe_ser_true,
       (SELECT COUNT(*) FROM pg_proc p
         WHERE p.pronamespace = 'public'::regnamespace
           AND has_function_privilege('anon', p.oid, 'EXECUTE'))                       AS funciones_anon_debe_ser_0;
```

Y la prueba desde fuera, con la llave pública (Supabase → API Keys → `anon` / publishable). Debe responder error de permisos, no datos:

```bash
read -s ANON
curl -s "https://<ref>.supabase.co/rest/v1/items?select=id&limit=1" -H "apikey: $ANON"; echo
unset ANON
```

Esperado: `{"code":"42501", ... "permission denied for table items"}`.

Al final, abre la app en el iPhone y en el escritorio: sincronizar, ver feed, marcar visto, guardar. Todo debe funcionar igual.

---

## Si algo sale mal

| Síntoma | Qué hacer |
|---|---|
| v2 o v4 falla a la mitad | Nada que hacer: cada archivo corre dentro de `BEGIN … COMMIT` y se revierte solo. Mándame el error. |
| Después de v4 la app no carga datos | Casi seguro el backend no usa la service_role (paso 0). Revisa `vercel env ls`. Si no es eso, corre `supabase_rollback_v4.sql`. |
| Después de v2 la app no carga datos | Si v4 ya corrió: `supabase_rollback_v4.sql`, luego `supabase_rollback_v2.sql`. Ojo: el código actual de main necesita v2; regresar a v1 implica también regresar el código. Mejor mándame el error primero. |
| Algo peor, o duda, **antes** de que alguien entre con Google | Restaurar el respaldo (abajo). |
| Algo sale mal **después** de que alguien entró con Google | No hay vuelta atrás automática: el respaldo no tiene los datos de esas cuentas. Mándame el error. |

Los dos rollbacks y la limpieza abortan solos, sin tocar nada, si detectan que perderían datos: otra cuenta en la base o el uuid del dueño equivocado.

### Restaurar el respaldo

Solo vale mientras nadie haya entrado con Google: la limpieza lo verifica y se niega si hay alguna cuenta en `auth.users`. En esa ventana la llave `anon` todavía no está en el frontend, así que regresar a los permisos del respaldo no expone nada.

Si el respaldo es de antes de v2 (el paso 1 dijo `v2_aplicado = false`), restaurarlo regresa a v1, y el código de main no funciona con v1. Mándame el error antes de restaurar: hay que regresar también el código.

Todo va en **una sola transacción**: la limpieza y el restore entran juntos o no entra ninguno. Por eso no se corre en el SQL editor sino con `psql`, que viene con el mismo `brew install` del paso 2.

```bash
F=<la ruta que anotaste en el paso 2>
cd ~/focusfeed_respaldos
pg_restore -l "$F" | grep -v ' SCHEMA - public ' | grep -v ' DEFAULT ACL ' > lista.txt
pg_restore --no-owner -L lista.txt -f restore.sql "$F"
read -s PGURL
psql "$PGURL" --single-transaction -v ON_ERROR_STOP=1 \
  -f "<carpeta del repo>/supabase_limpiar_para_restaurar.sql" -f restore.sql
unset PGURL
rm restore.sql lista.txt
```

Los dos `grep` quitan lo que el usuario `postgres` de Supabase no puede recrear: el esquema `public` (ya existe) y los permisos por default de `supabase_admin`. Sin ellos el restore falla (probado en local con roles que imitan a Supabase). `restore.sql` trae tus datos: por eso se borra al final.

Si `psql` termina sin errores, la base está como al momento del respaldo, permisos incluidos. Si imprime un error, no cambió nada: mándamelo.

[Need to verify] Lo probé en local con roles que imitan a Supabase, no en Supabase real. Si quieres certeza total antes de necesitarlo, ensaya los pasos 2 y "Restaurar" en un proyecto de Supabase de prueba.

---

## Después del login (cuando el PR B esté en producción)

El orden importa. Mientras exista `OWNER FALLBACK` (hasta el PR C), una sesión sin login cae en el uuid de `FOCUSFEED_OWNER_ID`, que es donde vive tu historial. Por eso primero se cierra esa puerta y al final se mueve el historial.

1. Entra con tu cuenta de Google **en cada dispositivo** donde usas FocusFeed: la PWA del iPhone y el navegador del escritorio. Así ninguno sigue entrando sin login.
2. Mergea el PR C (borra `OWNER FALLBACK`). Desde ahí, un request sin token válido responde 401 y no ve datos de nadie.
3. Abre `supabase_reasignar_dueno.sql` y sustituye `REEMPLAZA-CON-TU-CORREO` por tu correo de Google (y `viejo`, si el paso 1 mostró otro uuid en `duenos_v2`). SQL Editor → Run. El script busca tu cuenta por correo: no hay uuid que copiar a mano. Fusiona lo que hayas hecho con la cuenta nueva con tu historial y te pone en plan `pro`.
4. La variable ya no sirve. Bórrala y haz redeploy desde el dashboard (paso 0):

```bash
vercel env rm FOCUSFEED_OWNER_ID production --yes
```

5. Recarga la app en cada dispositivo: debes ver tus fuentes y tu historial.

Las credenciales de Google y las variables de Vercel del login vienen en el PR B.
