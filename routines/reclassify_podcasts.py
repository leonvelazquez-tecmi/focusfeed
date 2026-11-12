#!/usr/bin/env python3
"""Corrige el tipo de los episodios de podcast guardados antes del 11-oct-2026.

Hasta ese día el parser marcaba todo item RSS como `article`; desde
171fbb2 detecta el audio (enclosure audio/*) y marca `podcast`, pero solo para
lo que entra nuevo: batch_save_items nunca actualiza lo ya guardado. Resultado:
los episodios viejos de un podcast quedaron como `article` y "Solo podcasts"
casi no muestra nada.

Esto vuelve a leer cada feed no-YouTube, y donde el parser dice `podcast` y la
base dice otra cosa, cambia SOLO `content_type`. No toca status, calificaciones
ni el análisis de IA. Solo alcanza lo que el feed todavía publica; un episodio
que ya salió del feed se queda como está. Guarda los ids cambiados en
routines/reclassify_podcasts_ids.txt para poder revertir.

    python3 routines/reclassify_podcasts.py            # solo cuenta (por defecto)
    python3 routines/reclassify_podcasts.py --apply    # escribe
    python3 routines/reclassify_podcasts.py --revert   # devuelve a 'article' los ids guardados
"""

import os
import sys
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app.db import is_supabase, supabase_request, get_db_connection, init_db  # noqa: E402
from app.ingestion import HEADERS, parse_rss_feed_xml  # noqa: E402

IDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reclassify_podcasts_ids.txt")


def _paginado(endpoint):
    out, offset = [], 0
    while True:
        sep = "&" if "?" in endpoint else "?"
        res = supabase_request(f"{endpoint}{sep}limit=1000&offset={offset}")
        if not isinstance(res, list) or not res:
            return out
        out.extend(res)
        offset += len(res)


def feeds():
    if is_supabase():
        return _paginado("feeds?select=id,title,feed_url&feed_type=neq.youtube&order=id.asc")
    init_db()
    conn = get_db_connection()
    rows = conn.execute("SELECT id, title, feed_url FROM feeds WHERE feed_type != 'youtube'").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def podcasts_del_feed(feed):
    """guids que el parser reconoce como podcast en el feed vivo."""
    try:
        req = urllib.request.Request(feed["feed_url"], headers=HEADERS)
        xml = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    except Exception:
        return feed, None
    return feed, [i["guid"] for i in parse_rss_feed_xml(xml, feed["id"]) if i["content_type"] == "podcast"]


def mal_tipados(feed_id, guids):
    """(id, guid) guardados en este feed con esos guids y tipo distinto de podcast."""
    gs = set(guids)
    if is_supabase():
        rows = _paginado(f"items?select=id,guid,content_type&feed_id=eq.{int(feed_id)}")
    else:
        conn = get_db_connection()
        rows = [dict(r) for r in conn.execute(
            "SELECT id, guid, content_type FROM items WHERE feed_id = ?", (feed_id,)).fetchall()]
        conn.close()
    return [r["id"] for r in rows if r["guid"] in gs and r["content_type"] != "podcast"]


def set_type(ids, tipo):
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        if is_supabase():
            supabase_request(f"items?id=in.({','.join(str(x) for x in chunk)})",
                             method="PATCH", data={"content_type": tipo})
        else:
            conn = get_db_connection()
            conn.execute(f"UPDATE items SET content_type = ? WHERE id IN ({','.join('?' for _ in chunk)})",
                         [tipo] + chunk)
            conn.commit()
            conn.close()


def main():
    if "--revert" in sys.argv:
        ids = [int(x) for x in open(IDS_FILE).read().split()]
        set_type(ids, "article")
        print(f"Revertidos a 'article': {len(ids)}")
        return

    apply = "--apply" in sys.argv
    with ThreadPoolExecutor(8) as ex:
        resultados = list(ex.map(podcasts_del_feed, feeds()))

    todos, fallidos = [], 0
    for feed, guids in resultados:
        if guids is None:
            fallidos += 1
            continue
        if not guids:
            continue
        ids = mal_tipados(feed["id"], guids)
        print(f"  feed {feed['id']} {feed['title'][:40]}: {len(guids)} podcasts en el feed, {len(ids)} mal tipados")
        todos.extend(ids)

    print(f"{'Supabase' if is_supabase() else 'SQLite'}: a reclasificar {len(todos)}. Feeds que no respondieron: {fallidos}.")
    if apply and todos:
        with open(IDS_FILE, "w") as f:
            f.write("\n".join(str(i) for i in todos))
        set_type(todos, "podcast")
        print(f"Reclasificados: {len(todos)}. Ids guardados en {IDS_FILE}")
    else:
        print("Solo conteo: no se escribió nada (usa --apply)")


if __name__ == "__main__":
    main()
