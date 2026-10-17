# FocusFeed: manual de operación

## El negocio en una línea

FocusFeed le da a profesionales del conocimiento un feed curado por IA que aterriza en su vault.

## Comprador

Consultores, investigadores, analistas y gente de PKM que ya vive en Obsidian o Notion. Siguen entre 50 y 200 fuentes, no alcanzan a leerlas, y ya pagan por herramientas de conocimiento. No es un lector casual de noticias.

Su dolor: el feed de Feedly o YouTube les entrega volumen sin jerarquía, y lo poco que rescatan se queda en un "guardado" que nunca vuelven a abrir.

## Promesa

Abres la app, ves lo que sí importa de tus propias fuentes, y lo que vale la pena se va a tu vault en un clic.

## Diferenciador

El export a Obsidian con frontmatter (`app/pkm_exporter.py`). Feedly y Perplexity resumen, pero dejan el resultado atrapado en su plataforma. Cerrar ese ciclo es la razón por la que este comprador paga.

## Modelo

Freemium. Gratis: hasta 15 fuentes, sin curación de IA. Pro: IA, export al vault y brief semanal.

El costo de tokens lo absorbe el producto. Consecuencia directa: cualquier cambio que suba el costo de IA por usuario es una decisión de negocio, no una decisión técnica. Se declara en el resumen del ticket.

## Barra de calidad

- Un visitante nuevo entiende la oferta en 5 segundos.
- Funciona primero en iPhone (375px). La PWA instalada es el uso principal, el escritorio es secundario.
- El export al vault nunca pierde datos ni duplica notas.
- Ningún usuario ve datos de otro. Sin excepción.
- La copy de la interfaz va en español.

## Cómo quiero que trabajes

- Cambios chicos y revisables. Un ticket, una línea de llegada, un diff.
- Plan mode obligatorio antes de tocar: esquema de base de datos, autenticación, el prompt de IA, o cobros.
- Después de editar: levanta la app en preview, recorre el flujo, revisa consola y red. No me pidas que verifique yo lo que tú puedes verificar.
- Al cerrar: resume qué cambió, qué probaste, qué no pudiste probar y qué necesita revisión humana.
- Usa el estilo que ya existe. Este repo no tiene build ni framework y así se queda por ahora.
- Versiona lo que reemplaces (`index_v1.0.html`) en lugar de sobrescribir sin rastro.

## Arquitectura, para que no la vuelvas a deducir

- **Backend**: `http.server` de Python servido como una sola función de Vercel. `vercel.json` reescribe todo a `api/index.py`, con `maxDuration` de 60 segundos.
- **Datos**: Supabase por REST desde `app/db.py`. Cae a SQLite local cuando faltan las variables de entorno, así que todo en `storage.py` tiene dos ramas.
- **Frontend**: un solo archivo, `app/static/index.html`. CSS propio sin Tailwind, dark mode por `prefers-color-scheme`, delegación de eventos con `data-act`.
- **Archivos estáticos**: `vercel.json` manda todo a la función, así que un archivo nuevo en `app/static` solo se sirve si `app/server.py` tiene ruta para él (ver el diccionario `ICONOS`). El ícono vive en `icon.svg`, la marca "FF" del encabezado; los PNG y el `.ico` se regeneran desde esa geometría con PIL.
- **Ingesta**: RSS y Atom directo, sin API de YouTube (`app/ingestion.py`). Importador de OPML de Feedly.
- **IA**: `app/ai_engine.py`, con motor heurístico de respaldo. Ver la deuda abierta en `ROADMAP.md`.
- **Cron**: `/api/cron/sync`, diario a las 13:00 UTC.

## Cómo verificar

```bash
python3 run.py
```

Corre en `localhost:8080`. El preview está registrado como `focusfeed` en `.claude/launch.json`.

Verificación mínima de cualquier cambio de interfaz: 375px y 1280px, claro y oscuro, consola sin errores, sin scroll horizontal.

### En el iPhone

La PWA instalada no se refresca sola. Tras un cambio de datos o de código, para ver el resultado hay que cerrarla por completo (deslizar hacia arriba en el selector de apps) y abrirla de nuevo. Para un ícono nuevo hay que quitar el acceso de la pantalla de inicio y volver a agregarlo desde Safari. Si algo "sigue igual", descartar esto primero.

### En sesiones en la nube

- Las pruebas son scripts, no pytest (no está instalado): `python3 tests/test_feed_recency.py`, `python3 tests/test_multitenancy.py`.
- No hay preview registrado ni acceso a producción (el proxy bloquea `focusfeed-olive.vercel.app`). Se verifica en local con SQLite: sembrar items de edades distintas y capturar con Playwright (`/opt/node22/lib/node_modules/playwright`, Chromium en `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`).
- Para apagar el servidor local, mata el PID. `pkill -f "python3 run.py"` también mata el shell de la sesión.
- Corre las pruebas y haz push solo si pasan todas (`... && git push`). Con `;` entre comandos el push sale aunque una prueba falle.
- Un PR mergeado no se reutiliza: reinicia la rama desde `main` con el mismo nombre y sube con `--force-with-lease`.

### Migraciones de Supabase

Las aplica León a mano, nunca la sesión. El proyecto está en su cuenta personal de Supabase (la del Gmail), organización Personal. Ver `context/decisiones.md`, 17-oct.

Al entregar una migración: el SQL completo en un bloque para copiar y pegar en el SQL Editor, los pasos numerados desde el dashboard, una consulta de prueba que no pida UUID (toma el usuario de `subscriptions`) y cómo deshacerla.

- El SQL Editor pregunta por RLS al crear una tabla. Elegir **Run and enable RLS**: el backend usa la llave de servicio, que se lo salta, y así la tabla no queda abierta a las llaves públicas.
- La consulta de comprobación debe incluir el caso concreto que originó el cambio, no solo un conteo general. El 17-oct una limpieza dio 0 y aun así quedaba la tarjeta que motivó el arreglo, porque `ai_usage` solo registra desde el 11-oct y el filtro dejó fuera lo anterior.
- Antes de un `UPDATE` o `DELETE`, respaldo en una tabla aparte y la consulta de prueba lista.

## Acciones que le tocan a León

León no es programador. Toda acción humana se entrega en pasos numerados de clics, con el texto a pegar dentro del chat y nunca en un archivo que haya que abrir.

Mergear un PR en GitHub: abrir el enlace, **Ready for review**, esperar el recuadro verde, **Merge pull request**, **Confirm merge**. Mergear puede disparar un deploy en Vercel, que es solo suyo.

## Permisos

| Nivel | Acciones |
|---|---|
| Seguro | Leer, planear, pruebas locales, editar `app/static/`, docs, PR en borrador |
| Pregunta primero | Dependencias, migraciones de esquema, autenticación, cambios al prompt de IA, tocar el cron |
| Solo humano | Deploy a producción, cobros, llaves de API, datos de clientes, borrado de datos |
