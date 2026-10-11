#!/usr/bin/env python3
"""Repara los items guardados como "Sin título" por el bug de ElementTree.

YouTube: título y canal por oEmbed (sin llave, por video_id), porque el RSS
solo trae los 15 videos más recientes. Otros feeds: se vuelve a descargar el
feed y se toma el item con el mismo guid.

Solo toca items con title='Sin título'. No borra nada. Usa la misma base que la
app: Supabase si hay variables de entorno, si no SQLite local.

    python3 routines/repair_titles.py --dry-run     # cuenta y muestra 5 ejemplos
    python3 routines/repair_titles.py               # escribe
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app.db import is_supabase, supabase_request, get_db_connection  # noqa: E402
from app.ingestion import HEADERS, parse_rss_feed_xml  # noqa: E402

SIN_TITULO = "Sin título"


def pendientes():
    if is_supabase():
        out, offset = [], 0
        while True:
            q = urllib.parse.quote(SIN_TITULO)
            res = supabase_request(
                f"items?title=eq.{q}&select=id,guid,feed_id,video_id,content_type"
                f"&order=id.asc&limit=1000&offset={offset}")
            if not isinstance(res, list) or not res:
                return out
            out.extend(res)
            offset += len(res)
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, guid, feed_id, video_id, content_type FROM items WHERE title = ?",
        (SIN_TITULO,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def oembed(video_id):
    url = ("https://www.youtube.com/oembed?format=json&url="
           + urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}"))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read())
        return {"title": d.get("title") or "", "author": d.get("author_name") or ""}
    except Exception:
        return None


def feed_url(feed_id):
    if is_supabase():
        res = supabase_request(f"feeds?id=eq.{int(feed_id)}&select=feed_url")
        return res[0]["feed_url"] if isinstance(res, list) and res else None
    conn = get_db_connection()
    row = conn.execute("SELECT feed_url FROM feeds WHERE id = ?", (feed_id,)).fetchone()
    conn.close()
    return row["feed_url"] if row else None


def desde_feed(feed_id):
    """{guid: {title, author, published_at, url}} del feed actual."""
    url = feed_url(feed_id)
    if not url:
        return {}
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            xml = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}
    return {i["guid"]: {k: i[k] for k in ("title", "author", "published_at", "url")}
            for i in parse_rss_feed_xml(xml, feed_id) if i["title"] != SIN_TITULO}


def guardar(item_id, data):
    if is_supabase():
        supabase_request(f"items?id=eq.{int(item_id)}", method="PATCH", data=data,
                         headers_extra={"Prefer": "return=minimal"})
        return
    conn = get_db_connection()
    cols = ", ".join(f"{k} = ?" for k in data)
    conn.execute(f"UPDATE items SET {cols} WHERE id = ?", (*data.values(), item_id))
    conn.commit()
    conn.close()


def main():
    dry = "--dry-run" in sys.argv
    items = pendientes()
    videos = [i for i in items if i.get("video_id")]
    otros = [i for i in items if not i.get("video_id")]
    print(f"{'Supabase' if is_supabase() else 'SQLite'}: {len(items)} sin título "
          f"({len(videos)} videos, {len(otros)} otros)")

    cambios = []
    with ThreadPoolExecutor(16) as ex:
        for item, meta in zip(videos, ex.map(lambda i: oembed(i["video_id"]), videos)):
            if meta and meta["title"]:
                data = {"title": meta["title"]}
                if meta["author"]:
                    data["author"] = meta["author"]
                cambios.append((item["id"], data))

    for feed_id in sorted({i["feed_id"] for i in otros if i.get("feed_id")}):
        actuales = desde_feed(feed_id)
        for item in otros:
            if item.get("feed_id") == feed_id and item["guid"] in actuales:
                cambios.append((item["id"], actuales[item["guid"]]))

    print(f"Reparables: {len(cambios)} de {len(items)}")
    for item_id, data in cambios[:5]:
        print(f"  {item_id}: {data.get('title')}")
    if dry:
        print("--dry-run: no se escribió nada")
        return

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda c: guardar(*c), cambios))
    print(f"Escritos: {len(cambios)}. Sin reparar: {len(items) - len(cambios)} "
          "(videos borrados o privados, o items que ya no están en su feed).")


if __name__ == "__main__":
    main()
