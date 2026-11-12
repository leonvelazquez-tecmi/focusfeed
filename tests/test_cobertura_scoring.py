"""Todo lo reciente de un usuario se califica, no solo los 160 items más nuevos.

Medido el 12-nov en producción: 341 de 865 items de la última semana sin score,
que entraban a "Para ti" con 20, más que el score mediano de JEV (13).

    python3 tests/test_cobertura_scoring.py
"""

import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_cobertura.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage  # noqa: E402
import app.ai_engine as ai  # noqa: E402

LEON = "88888888-8888-8888-8888-888888888888"
OTRA = "99999999-9999-9999-9999-999999999999"
fallos = []
PETICIONES = []  # cuerpos que "salieron" a JEV


def check(nombre, condicion, detalle=""):
    print(f"  [{'ok  ' if condicion else 'FALLA'}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


class RespuestaFalsa(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def urlopen_falso(req, timeout=None):
    PETICIONES.append(json.loads(req.data.decode("utf-8")))
    return RespuestaFalsa(json.dumps({"answers": {"interesa": {"noul": 0.8}}, "usage": {"cost": 0}}).encode())


def fecha(horas_atras=0, dias_atras=0):
    t = datetime.now(timezone.utc) - timedelta(hours=horas_atras, days=dias_atras)
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def con_score(user):
    conn = storage.get_db_connection()
    filas = conn.execute("""SELECT i.guid FROM user_items ui JOIN items i ON i.id = ui.item_id
                             WHERE ui.user_id = ? AND ui.relevance_score IS NOT NULL""", (user,)).fetchall()
    conn.close()
    return {r["guid"] for r in filas}


def pendientes_ids(user):
    return {i["id"] for i in (storage.fetch_unscored_items(user, limit=1000) or [])}


def main():
    init_db()
    ai.OPENROUTER_API_KEY = "llave-de-prueba"
    ai.urllib.request.urlopen = urlopen_falso

    f_leon = storage.batch_create_feeds([{"title": "Leon", "feed_url": "https://e.com/l", "feed_type": "rss"}])[0]["id"]
    f_otra = storage.batch_create_feeds([{"title": "Otra", "feed_url": "https://e.com/o", "feed_type": "rss"}])[0]["id"]
    f_buzon = storage.batch_create_feeds([{"title": "Buzón", "feed_url": "outlook://b", "feed_type": "rss"}])[0]["id"]
    storage.subscribe(LEON, f_leon)
    storage.subscribe(LEON, f_buzon)
    storage.subscribe(OTRA, f_otra)

    def item(feed, guid, titulo, **kw):
        base = {"feed_id": feed, "guid": guid, "title": titulo, "url": f"https://e.com/{guid}", "author": "Autor",
                "published_at": fecha(), "content_type": "article", "raw_content": "texto", "topic_tags": "[]"}
        base.update(kw)
        return base

    filas = [item(f_leon, f"r{n}", f"Reciente {n}", published_at=fecha(horas_atras=n)) for n in range(400)]  # hasta ~17 días
    filas += [item(f_leon, f"v{n}", f"Viejo {n}", published_at=fecha(dias_atras=45 + n)) for n in range(20)]
    filas += [item(f_leon, f"a{n}", f"Archivado {n}", published_at=fecha(horas_atras=n)) for n in range(10)]
    filas += [item(f_leon, f"g{n}", f"Guardado {n}", published_at=fecha(horas_atras=n)) for n in range(5)]
    filas += [item(f_buzon, f"outlook:{n}", f"Correo confidencial {n}", published_at=fecha(horas_atras=n)) for n in range(3)]
    filas += [item(f_otra, f"x{n}", f"De otra persona {n}", published_at=fecha(horas_atras=n)) for n in range(20)]
    storage.batch_save_items(filas)

    ids = {}
    for u in (LEON, OTRA):
        for i in storage.fetch_items(u, tab="inbox", limit=300)["items"]:
            ids[i["guid"]] = i["id"]
    # fetch_items topa en 300: los 400 recientes no caben, se piden por guid aparte.
    conn = storage.get_db_connection()
    ids = {r["guid"]: r["id"] for r in conn.execute("SELECT id, guid FROM items").fetchall()}
    conn.close()
    for n in range(10):
        storage.set_item_status(LEON, ids[f"a{n}"], "archived")
    for n in range(5):
        storage.set_item_status(LEON, ids[f"g{n}"], "reading")

    print("\n1. Qué se considera pendiente")
    pend = storage.fetch_unscored_items(LEON, limit=1000)
    guids = {i["guid"] for i in pend}
    check("Entran los 400 recientes y los 3 del buzón", len([g for g in guids if g.startswith("r")]) == 400
          and len([g for g in guids if g.startswith("outlook:")]) == 3, f"{len(pend)} pendientes")
    check("No entra lo de hace más de 30 días", not any(g.startswith("v") for g in guids))
    check("No entra lo archivado ni lo guardado", not any(g.startswith(("a", "g")) for g in guids))
    check("No entra nada de otra persona", not any(g.startswith("x") for g in guids))
    check("Sale del más nuevo al más viejo", pend[0]["guid"] in ("r0", "outlook:0") and pend[-1]["guid"] == "r399",
          f"primero {pend[0]['guid']}, último {pend[-1]['guid']}")
    check("Trae lo que JEV necesita", {"id", "title", "author", "content_type", "raw_content"} <= set(pend[0]))

    print("\n2. El tope de tiempo corta el lote y avisa que quedan más")
    r = ai.score_pending_batch(LEON, budget_seconds=-1)
    check("Califica una tanda y se detiene", r["scored"] == ai.SCORE_CHUNK and r["has_more"] is True, str(r))

    print("\n3. Llamadas repetidas terminan con todo lo reciente calificado")
    n_antes = len(PETICIONES)
    rondas = []
    for _ in range(8):
        r = ai.score_pending_batch(LEON)
        rondas.append(r)
        if not r["has_more"]:
            break
    check("Cada ronda califica hasta 120", rondas[0]["scored"] == ai.SCORE_BATCH, str([x["scored"] for x in rondas]))
    check("Termina sin pendientes (has_more False)", rondas[-1]["has_more"] is False and len(rondas) <= 4, f"{len(rondas)} rondas")
    check("No quedan pendientes", not pendientes_ids(LEON))
    calificados = con_score(LEON)
    check("Se calificaron los 400 recientes y los 3 del buzón", len(calificados) == 403, f"{len(calificados)}")
    check("Lo viejo, archivado y guardado quedó sin tocar",
          all(storage.get_user_item(LEON, ids[g]) is None or storage.get_user_item(LEON, ids[g]).get("relevance_score") is None
              for g in ("v0", "a0", "g0")))

    print("\n4. Privacidad y aislamiento")
    todas = PETICIONES
    check("Nada del buzón salió a JEV", not any("confidencial" in json.dumps(p, ensure_ascii=False) for p in todas))
    outl = storage.get_user_item(LEON, ids["outlook:0"])
    check("El buzón sí se califica, con el motor local", outl and outl["relevance_score"] is not None)
    check("A la otra persona no se le creó nada", storage.get_user_item(OTRA, ids["r0"]) is None
          and not con_score(OTRA))
    check("Los pendientes de la otra persona son solo suyos", pendientes_ids(OTRA) == {ids[f"x{n}"] for n in range(20)})

    print("\n5. Sin la migración v9 el código vuelve al comportamiento anterior")
    original = storage.fetch_unscored_items
    storage.fetch_unscored_items = lambda *a, **k: None
    try:
        for n in range(30):  # items nuevos del usuario para tener qué calificar
            storage.batch_save_items([item(f_leon, f"n{n}", f"Nuevo {n}", published_at=fecha())])
        r = ai.score_pending_batch(LEON)
        check("Sigue funcionando (ventana de 160, 40 por llamada)", r["scored"] <= ai.LEGACY_SCORE_BATCH and isinstance(r["has_more"], bool), str(r))
    finally:
        storage.fetch_unscored_items = original

    print("\n6. Compatibilidad")
    check("score_pending_for_user sigue devolviendo un número", isinstance(ai.score_pending_for_user(LEON), int))

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("Todo lo reciente se califica, lo privado no sale y sin la migración nada se rompe.")


if __name__ == "__main__":
    main()
