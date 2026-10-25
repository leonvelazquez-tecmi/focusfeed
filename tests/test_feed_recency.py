"""'Para ti' pesa la antigüedad por escalones y no muestra lo guardado.

Un item de un escalón más viejo (hasta 7 días, hasta 30, más viejo) nunca va
antes que uno de un escalón más nuevo, sin importar su score. El score guardado
no cambia; solo el orden. Rama SQLite, espejo de
supabase_migration_v8_tiers_y_guardados.sql.

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
        ("viejo-95", a, 400, 95),
        ("mes-90", b, 20, 90),
        ("reciente-65", a, 1, 65),
        ("hoy-sin-score", a, 0, None),
        ("semana-52", b, 6, 52),
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
    # Escalón 0 (hasta 7 días): reciente-65 63, semana-52 40, hoy-sin-score 20.
    # Escalón 1 (hasta 30): mes-90 50. Escalón 2 (más viejo): viejo-95, solo por fecha.
    esperado = ["reciente-65", "semana-52", "hoy-sin-score", "mes-90", "viejo-95"]
    check("Lo que JEV aprobó (65 de ayer) va primero", orden[0] == "reciente-65")
    check("Un 52 de hace una semana le gana a lo de hoy sin calificar",
          orden.index("semana-52") < orden.index("hoy-sin-score"))
    check("Un 65 de ayer le gana a un 95 de hace más de un año", orden.index("reciente-65") < orden.index("viejo-95"))
    check("Orden completo", orden == esperado, f"esperado {esperado}")

    check("Nada de un escalón viejo va antes que uno más nuevo (95 de hace 400 días al final)",
          orden[-1] == "viejo-95")

    print("\n2. El score guardado no cambia")
    por_guid = {i["guid"]: i for i in storage.fetch_items(LEON, tab="curated")["items"]}
    check("viejo-95 sigue con 95 en la base", por_guid["viejo-95"]["relevance_score"] == 95)
    check("mes-90 sigue con 90 en la base", por_guid["mes-90"]["relevance_score"] == 90)

    print("\n3. 'Todo' sigue por fecha, sin decaimiento")
    todo = [i["guid"] for i in storage.fetch_items(LEON, tab="inbox")["items"]]
    # La diversificación por fuente puede mover items; lo que no debe pasar es que
    # el score o el decaimiento reordenen "Todo": los dos más nuevos van primero.
    check("El más nuevo va primero, sin importar el score", todo[0] == "hoy-sin-score", str(todo))

    print("\n4. Poka-yoke: lo viejo con score no se salta a lo reciente")
    # Caso real del 25-oct: 191 videos con score 70 y fecha de hace 69 días iban antes
    # que videos de 6 a 30 días sin calificar.
    fa = storage.batch_create_feeds([{"title": "Fuente C", "feed_url": "https://ejemplo.com/c", "feed_type": "rss"}])[0]["id"]
    fb = storage.batch_create_feeds([{"title": "Fuente D", "feed_url": "https://ejemplo.com/d", "feed_type": "rss"}])[0]["id"]
    storage.subscribe(LEON, fa)
    storage.subscribe(LEON, fb)
    storage.batch_save_items([{
        "feed_id": f, "guid": g, "title": g, "url": f"https://ejemplo.com/{g}", "author": "A",
        "published_at": hace(d), "content_type": "video", "raw_content": "x",
    } for g, f, d in [("sep-sin-score", fa, 10), ("agosto-70-a", fb, 69), ("agosto-70-b", fa, 69),
                      ("oct-sin-score", fb, 3)]])
    ids = {i["guid"]: i["id"] for i in storage.fetch_items(LEON, tab="inbox", type_filter="video")["items"]}
    storage.save_user_score(LEON, ids["agosto-70-a"], 70, "x")
    storage.save_user_score(LEON, ids["agosto-70-b"], 70, "x")
    videos = [i["guid"] for i in storage.fetch_items(LEON, tab="curated", type_filter="video")["items"]]
    print(f"     {videos}")
    check("Lo de hace 10 días sin calificar va antes que lo de hace 69 días con 70",
          videos.index("sep-sin-score") < videos.index("agosto-70-a"))
    check("Lo más nuevo va primero", videos[0] == "oct-sin-score")

    print("\n5. La diversificación no salta escalones")
    lote = [{"id": n, "feed_id": 1} for n in range(6)] + [{"id": 99, "feed_id": 2}]
    orden_div = [i["id"] for i in storage.diversify_by_feed(lote)]
    check("Un item de otra fuente sube como máximo 3 lugares (antes saltaba sin límite)",
          orden_div.index(99) >= 3, str(orden_div))

    print("\n6. Lo guardado sale de 'Para ti' y vive en 'Guardados'")
    storage.set_item_status(LEON, ids["oct-sin-score"], "reading")
    pt = [i["guid"] for i in storage.fetch_items(LEON, tab="curated")["items"]]
    gd = [i["guid"] for i in storage.fetch_items(LEON, tab="reading")["items"]]
    check("Guardado no aparece en Para ti", "oct-sin-score" not in pt, str(pt))
    check("Guardado sí aparece en Guardados", "oct-sin-score" in gd, str(gd))
    storage.set_item_status(LEON, ids["oct-sin-score"], "inbox")
    pt = [i["guid"] for i in storage.fetch_items(LEON, tab="curated")["items"]]
    check("Quitarlo de Guardados lo devuelve a Para ti", "oct-sin-score" in pt)

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("'Para ti' prioriza lo reciente sin tocar el score.")


if __name__ == "__main__":
    main()
