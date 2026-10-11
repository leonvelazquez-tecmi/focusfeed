"""Imagen bajo demanda: se pide una sola vez por item y se guarda.

    python3 tests/test_images_on_demand.py
"""

import os
import sys
import tempfile
import warnings

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
for k in ("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app import server  # noqa: E402
from app.db import init_db, get_db_connection  # noqa: E402

fallas = []


def check(nombre, cond):
    print(("OK   " if cond else "FALLA") + " " + nombre)
    if not cond:
        fallas.append(nombre)


init_db()
conn = get_db_connection()
for i, (url, tipo, thumb) in enumerate([
        ("https://a.com/con-og", "article", ""),
        ("https://a.com/sin-og", "article", ""),
        ("https://a.com/ya-tiene", "article", "https://a.com/x.jpg"),
        ("https://youtube.com/watch?v=v", "video", ""),
], start=1):
    conn.execute("INSERT INTO items (id, feed_id, guid, title, url, content_type, thumbnail_url) "
                 "VALUES (?, 1, ?, 't', ?, ?, ?)", (i, f"g{i}", url, tipo, thumb))
conn.commit()
conn.close()

pedidas = []


def falso_og(url, timeout=0):
    pedidas.append(url)
    return "https://a.com/og.jpg" if "con-og" in url else ""


server.fetch_og_image = falso_og

res = server.fill_images([1, 2, 3, 4])
check("Devuelve solo la que sí trajo imagen", res == {1: "https://a.com/og.jpg"})
check("No pide la que ya tenía imagen ni el video", sorted(pedidas) == ["https://a.com/con-og", "https://a.com/sin-og"])

conn = get_db_connection()
thumbs = {r["id"]: r["thumbnail_url"] for r in conn.execute("SELECT id, thumbnail_url FROM items")}
conn.close()
check("Guarda la imagen encontrada", thumbs[1] == "https://a.com/og.jpg")
check("Marca 'none' la que no tiene", thumbs[2] == "none")

pedidas.clear()
check("Segunda vuelta no pide nada", server.fill_images([1, 2, 3, 4]) == {} and pedidas == [])
check("Tope de 16 ids", server.fill_images(list(range(100, 140))) == {})

print(f"\n{len(fallas)} fallas")
sys.exit(1 if fallas else 0)
