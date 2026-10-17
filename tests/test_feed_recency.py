"""'Para ti' pesa la antigüedad.

Un score alto de hace meses no debe ganarle a un item reciente de score medio.
El score guardado no cambia; solo el orden. Rama SQLite, espejo de
supabase_migration_v4_feed_recency.sql.

    python3 tests/test_feed_recency.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_recency.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage  # noqa: E402

LEON = "33333333-3333-3333-3333-333333333333"
fallos = []


def check(nombre, condicion, detalle=""):
    estado = "ok  " if condicion else "FALLA"
    print(f"  [{estado}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def hace(dias):
    return (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")


def sembrar():
    # Dos fuentes para que diversify_by_feed no altere el orden que se prueba.
    feeds = storage.batch_create_feeds([
        {"title": "Fuente A", "feed_url": "https://ejemplo.com/a", "feed_type": "rss"},
        {"title": "Fuente B", "feed_url": "https://ejemplo.com/b", "feed_type": "rss"},
    ])
    ids = {f["feed_url"]: f["id"] for f in feeds}
    a, b = ids["https://ejemplo.com/a"], ids["https://ejemplo.com/b"]
    storage.subscribe(LEON, a)
    storage.subscribe(LEON, b)

    # Las fuentes alternan en el orden esperado para que diversify_by_feed
    # no mueva nada y la prueba mida solo el decaimiento.
    casos = [
        # guid, feed, días de antigüedad, score guardado (None = sin calificar)
        ("viejo-95", b, 400, 95),
        ("mes-90", a, 30, 90),
        ("reciente-65", a, 1, 65),
        ("hoy-sin-score", b, 0, None),
        ("semana-52", a, 7, 52),
    ]
    storage.batch_save_items([{
        "feed_id": feed, "guid": guid, "title": guid, "url": f"https://ejemplo.com/{guid}",
        "author": "Autor", "published_at": hace(dias), "content_type": "article",
        "raw_content": "texto",
    } for guid, feed, dias, _ in casos])

    por_guid = {i["guid"]: i["id"] for i in storage.fetch_items(LEON, tab="inbox")["items"]}
    for guid, _, _, score in casos:
        if score is not None:
            storage.save_user_score(LEON, por_guid[guid], score, "nota")
    return por_guid


def main():
    init_db()
    sembrar()

    print("\n1. Orden de 'Para ti' con decaimiento")
    orden = [i["guid"] for i in storage.fetch_items(LEON, tab="curated")["items"]]
    print(f"     {orden}")
    # Sin calificar arranca en 40 (v5) y el castigo por antigüedad llega a 60 (v6).
    # Efectivos: reciente-65 63, hoy-sin-score 40, semana-52 38,
    # viejo-95 35 (95 - 60), mes-90 30 (90 - 60).
    esperado = ["reciente-65", "hoy-sin-score", "semana-52", "viejo-95", "mes-90"]
    check("Lo que JEV aprobó (65 de ayer) le gana a lo de hoy sin calificar", orden[0] == "reciente-65")
    check("Lo de hoy sin calificar le gana a un 95 de hace más de un año",
          orden.index("hoy-sin-score") < orden.index("viejo-95"))
    check("Lo de hoy sin calificar le gana a un 90 de hace un mes",
          orden.index("hoy-sin-score") < orden.index("mes-90"))
    check("Un 65 de ayer le gana a un 95 de hace más de un año", orden.index("reciente-65") < orden.index("viejo-95"))
    check("Orden completo", orden == esperado, f"esperado {esperado}")

    print("\n2. El score guardado no cambia")
    por_guid = {i["guid"]: i for i in storage.fetch_items(LEON, tab="curated")["items"]}
    check("viejo-95 sigue con 95 en la base", por_guid["viejo-95"]["relevance_score"] == 95)
    check("mes-90 sigue con 90 en la base", por_guid["mes-90"]["relevance_score"] == 90)

    print("\n3. 'Todo' sigue por fecha, sin decaimiento")
    todo = [i["guid"] for i in storage.fetch_items(LEON, tab="inbox")["items"]]
    check("Primero el más nuevo, al final el más viejo",
          todo[0] == "hoy-sin-score" and todo[-1] == "viejo-95", str(todo))

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("'Para ti' prioriza lo reciente sin tocar el score.")


if __name__ == "__main__":
    main()
