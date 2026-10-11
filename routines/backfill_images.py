#!/usr/bin/env python3
"""Llena la imagen de los artículos y podcasts ya guardados sin thumbnail_url.

Antes de feat/imagenes-en-tarjetas el parser de RSS/Atom dejaba la imagen
vacía. Primero se vuelve a descargar cada feed y se toma la imagen del item con
el mismo guid; lo que el feed ya no trae (o trae sin imagen) cae a og:image de
la página. Solo escribe donde thumbnail_url está vacío. No borra nada. Usa la
misma base que la app: Supabase si hay variables de entorno, si no SQLite.

    python3 routines/backfill_images.py --dry-run          # cuenta y muestra ejemplos
    python3 routines/backfill_images.py                    # escribe (últimos 30 días)
    python3 routines/backfill_images.py --days 90
"""

import os
import sys
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app.db import is_supabase, supabase_request, get_db_connection, init_db  # noqa: E402
from app.extractors import fetch_og_image  # noqa: E402
from app.ingestion import HEADERS, parse_rss_feed_xml  # noqa: E402
from routines.repair_titles import feed_url, guardar  # noqa: E402


def dias():
    if "--days" in sys.argv:
        return int(sys.argv[sys.argv.index("--days") + 1])
    return 30


def pendientes(desde):
    if is_supabase():
        desde_iso = desde.strftime("%Y-%m-%dT%H:%M:%SZ")
        out, offset = [], 0
        while True:
            res = supabase_request(
                "items?select=id,guid,feed_id,url,content_type"
                "&content_type=neq.video&or=(thumbnail_url.is.null,thumbnail_url.eq.)"
                f"&created_at=gte.{desde_iso}&order=id.asc&limit=1000&offset={offset}")
            if not isinstance(res, list) or not res:
                return out
            out.extend(res)
            offset += len(res)
    init_db()
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, guid, feed_id, url, content_type FROM items "
        "WHERE content_type != 'video' AND COALESCE(thumbnail_url, '') = '' "
        "AND created_at >= ?", (desde.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def imagenes_del_feed(feed_id):
    """{guid: imagen} del feed actual, solo items que sí traen imagen."""
    url = feed_url(feed_id)
    if not url:
        return {}
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            xml = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}
    return {i["guid"]: i["thumbnail_url"] for i in parse_rss_feed_xml(xml, feed_id)
            if i.get("thumbnail_url")}


def main():
    dry = "--dry-run" in sys.argv
    desde = datetime.now(timezone.utc) - timedelta(days=dias())
    items = pendientes(desde)
    print(f"{'Supabase' if is_supabase() else 'SQLite'}: {len(items)} sin imagen "
          f"en los últimos {dias()} días")

    feed_ids = sorted({i["feed_id"] for i in items if i.get("feed_id")})
    with ThreadPoolExecutor(16) as ex:
        por_feed = dict(zip(feed_ids, ex.map(imagenes_del_feed, feed_ids)))

    cambios, sin_feed = [], []
    for item in items:
        img = por_feed.get(item.get("feed_id"), {}).get(item["guid"])
        (cambios if img else sin_feed).append((item, img))
    del_feed = len(cambios)

    with ThreadPoolExecutor(16) as ex:
        for (item, _), img in zip(sin_feed, ex.map(lambda p: fetch_og_image(p[0].get("url")), sin_feed)):
            if img:
                cambios.append((item, img))

    print(f"Con imagen: {len(cambios)} ({del_feed} del feed, {len(cambios) - del_feed} por og:image). "
          f"Se quedan con el respaldo de color: {len(items) - len(cambios)}")
    for item, img in cambios[:5]:
        print(f"  {item['id']}: {img[:90]}")
    if dry:
        print("--dry-run: no se escribió nada")
        return

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda c: guardar(c[0]["id"], {"thumbnail_url": c[1]}), cambios))
    print(f"Escritos: {len(cambios)}")


if __name__ == "__main__":
    main()
