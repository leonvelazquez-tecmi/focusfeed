#!/usr/bin/env python3
"""Archiva los Shorts de YouTube que ya están en el corpus.

Desde fix/shorts-por-playlist los canales se leen por su playlist UULF (solo
videos largos). Esto limpia lo que ya estaba: junta los Shorts de la playlist
UUSH de cada canal (los 15 más recientes, confiable) y revisa el resto de los
videos contra /shorts/<id> (200 = Short, solo funciona fuera de Vercel). Los marca como
`archived` para cada usuario suscrito a su canal. No borra nada del corpus:
el item sigue en `items` y se puede devolver al feed cambiando el status.
Respeta lo que el usuario guardó: un Short en "Guardados" no se toca.

Usa la misma base que la app: Supabase si hay variables de entorno, si no SQLite.

    python3 routines/clean_shorts.py --dry-run     # cuenta y muestra ejemplos
    python3 routines/clean_shorts.py               # archiva
"""

import os
import sys
import warnings
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app.db import is_supabase, supabase_request, get_db_connection, init_db  # noqa: E402
from app.ingestion import HEADERS, parse_youtube_feed_xml  # noqa: E402
from app.shorts import find_shorts  # noqa: E402
import re  # noqa: E402
import urllib.request  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from app.storage import bulk_set_status  # noqa: E402


def _paginado(endpoint):
    out, offset = [], 0
    while True:
        sep = "&" if "?" in endpoint else "?"
        res = supabase_request(f"{endpoint}{sep}limit=1000&offset={offset}")
        if not isinstance(res, list) or not res:
            return out
        out.extend(res)
        offset += len(res)


def videos():
    if is_supabase():
        return _paginado("items?select=id,feed_id,video_id,title&content_type=eq.video&order=id.asc")
    init_db()
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id, feed_id, video_id, title FROM items WHERE content_type = 'video'").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def shorts_por_playlist():
    """video_ids de la playlist UUSH (Shorts) de cada canal suscrito."""
    if is_supabase():
        feeds = _paginado("feeds?select=feed_url&feed_type=eq.youtube")
    else:
        conn = get_db_connection()
        feeds = [dict(r) for r in conn.execute("SELECT feed_url FROM feeds WHERE feed_type = 'youtube'")]
        conn.close()
    urls = []
    for f in feeds:
        m = re.search(r'channel_id=UC([A-Za-z0-9_-]{22})', f.get("feed_url") or "")
        if m:
            urls.append(f"https://www.youtube.com/feeds/videos.xml?playlist_id=UUSH{m.group(1)}")

    def ids(url):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                return [i["video_id"] for i in parse_youtube_feed_xml(resp.read().decode("utf-8", "ignore"), 0)]
        except Exception:
            return []

    with ThreadPoolExecutor(16) as ex:
        return {v for lote in ex.map(ids, urls) for v in lote}


def suscriptores(feed_ids):
    """{feed_id: [user_id, ...]}"""
    out = defaultdict(list)
    ids = sorted({int(f) for f in feed_ids if f is not None})
    if not ids:
        return out
    if is_supabase():
        for i in range(0, len(ids), 200):
            chunk = ",".join(str(x) for x in ids[i:i + 200])
            for r in _paginado(f"subscriptions?select=user_id,feed_id&feed_id=in.({chunk})"):
                out[r["feed_id"]].append(r["user_id"])
        return out
    conn = get_db_connection()
    ph = ",".join("?" for _ in ids)
    for r in conn.execute(f"SELECT user_id, feed_id FROM subscriptions WHERE feed_id IN ({ph})", ids):
        out[r["feed_id"]].append(r["user_id"])
    conn.close()
    return out


def estados(user_id, item_ids):
    """{item_id: status} de las filas que ya existen en user_items."""
    ids = sorted(set(item_ids))
    out = {}
    if is_supabase():
        for i in range(0, len(ids), 200):
            chunk = ",".join(str(x) for x in ids[i:i + 200])
            for r in _paginado(f"user_items?select=item_id,status&user_id=eq.{user_id}&item_id=in.({chunk})"):
                out[r["item_id"]] = r["status"]
        return out
    conn = get_db_connection()
    ph = ",".join("?" for _ in ids)
    for r in conn.execute(
            f"SELECT item_id, status FROM user_items WHERE user_id = ? AND item_id IN ({ph})",
            [user_id, *ids]):
        out[r["item_id"]] = r["status"]
    conn.close()
    return out


def main():
    dry = "--dry-run" in sys.argv
    vids = videos()
    print(f"{'Supabase' if is_supabase() else 'SQLite'}: {len(vids)} videos en el corpus. Revisando…")

    por_playlist = shorts_por_playlist()
    shorts_ids = por_playlist | find_shorts(
        [v["video_id"] for v in vids if v["video_id"] not in por_playlist], workers=24, timeout=6)
    shorts = [v for v in vids if v["video_id"] in shorts_ids]
    print(f"Shorts: {len(shorts)} de {len(vids)} videos ({len(vids) - len(shorts)} normales)")
    for v in shorts[:5]:
        print(f"  {v['id']}: {v['title'][:80]}")

    por_usuario = defaultdict(list)
    subs = suscriptores([v["feed_id"] for v in shorts])
    for v in shorts:
        for uid in subs.get(v["feed_id"], []):
            por_usuario[uid].append(v["id"])

    total = guardados = ya = 0
    for uid, ids in por_usuario.items():
        st = estados(uid, ids)
        guardados += sum(1 for i in ids if st.get(i) == "reading")
        ya += sum(1 for i in ids if st.get(i) == "archived")
        pendientes = [i for i in ids if st.get(i, "inbox") == "inbox"]
        total += len(pendientes)
        if pendientes and not dry:
            for i in range(0, len(pendientes), 500):
                bulk_set_status(uid, pendientes[i:i + 500], "archived")

    print(f"Usuarios afectados: {len(por_usuario)}. Por archivar: {total}. "
          f"Ya archivados: {ya}. Guardados que se respetan: {guardados}.")
    print("--dry-run: no se escribió nada" if dry else f"Archivados: {total}")


if __name__ == "__main__":
    main()
