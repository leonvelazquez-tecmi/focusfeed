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
- **Ingesta**: RSS y Atom directo, sin API de YouTube (`app/ingestion.py`). Importador de OPML de Feedly.
- **IA**: `app/ai_engine.py`, con motor heurístico de respaldo. Ver la deuda abierta en `ROADMAP.md`.
- **Cron**: `/api/cron/sync`, diario a las 13:00 UTC.

## Cómo verificar

```bash
python3 run.py
```

Corre en `localhost:8080`. El preview está registrado como `focusfeed` en `.claude/launch.json`.

Verificación mínima de cualquier cambio de interfaz: 375px y 1280px, claro y oscuro, consola sin errores, sin scroll horizontal.

## Permisos

| Nivel | Acciones |
|---|---|
| Seguro | Leer, planear, pruebas locales, editar `app/static/`, docs, PR en borrador |
| Pregunta primero | Dependencias, migraciones de esquema, autenticación, cambios al prompt de IA, tocar el cron |
| Solo humano | Deploy a producción, cobros, llaves de API, datos de clientes, borrado de datos |
