"""Abrir una tarjeta es una señal de interés débil.

Orden de fuerza (positivas): corazón > clic > nada. El pulgar abajo penaliza
aparte. El clic cuenta una vez por tarjeta y nunca pisa una calificación
explícita.

    python3 tests/test_clic_senal.py
"""

import os
import sys
import tempfile

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_clic.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage, ai_engine  # noqa: E402

LEON = "44444444-4444-4444-4444-444444444444"
fallos = []


def check(nombre, condicion, detalle=""):
    print(f"  [{'ok  ' if condicion else 'FALLA'}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def aprendido():
    return storage.fetch_profile(LEON).get("learned_preferences", {}) or {}


def main():
    init_db()
    feed = storage.batch_create_feeds([{"title": "Canal", "feed_url": "https://e.com/c", "feed_type": "rss"}])[0]["id"]
    storage.subscribe(LEON, feed)
    storage.batch_save_items([{
        "feed_id": feed, "guid": f"g{n}", "title": f"Item {n}", "url": f"https://e.com/{n}",
        "author": "Ana Autora", "published_at": "2026-10-24T10:00:00", "content_type": "article",
        "raw_content": "texto", "topic_tags": '["cine"]',
    } for n in range(4)])
    ids = {i["guid"]: i["id"] for i in storage.fetch_items(LEON, tab="inbox")["items"]}

    print("\n1. Un clic suma poco y no mueve la tarjeta")
    ai_engine.record_feedback(LEON, ids["g0"], "click")
    item = storage.get_user_item(LEON, ids["g0"])
    check("Queda registrado como clic", item["user_rating"] == "click")
    check("No cambia de estado (sigue en inbox)", (item.get("status") or "inbox") == "inbox")
    a = aprendido()
    check("Los tags suben 1 (el corazón sube 2)", (a.get("boosted_tags") or {}).get("cine") == 1, str(a.get("boosted_tags")))
    check("El autor cuenta 1 clic", (a.get("clicked_authors") or {}).get("Ana Autora") == 1)
    check("El clic no lo vuelve autor favorito fijo", "Ana Autora" not in (a.get("boosted_authors") or []))

    print("\n2. Una tarjeta cuenta una sola vez")
    ai_engine.record_feedback(LEON, ids["g0"], "click")
    ai_engine.record_feedback(LEON, ids["g0"], "click")
    a = aprendido()
    check("Clics repetidos en la misma tarjeta no suman", (a.get("clicked_authors") or {}).get("Ana Autora") == 1
          and (a.get("boosted_tags") or {}).get("cine") == 1)

    print("\n3. El clic nunca pisa una calificación explícita")
    ai_engine.record_feedback(LEON, ids["g1"], "love")
    antes = aprendido()
    ai_engine.record_feedback(LEON, ids["g1"], "click")
    check("Corazón sigue siendo corazón", storage.get_user_item(LEON, ids["g1"])["user_rating"] == "love")
    check("Y el clic posterior no suma", aprendido() == antes)
    ai_engine.record_feedback(LEON, ids["g2"], "dislike")
    antes = aprendido()
    ai_engine.record_feedback(LEON, ids["g2"], "click")
    check("Pulgar abajo sigue siendo pulgar abajo", storage.get_user_item(LEON, ids["g2"])["user_rating"] == "dislike")
    check("Y el clic posterior no suma", aprendido() == antes)

    print("\n4. El corazón después de un clic sí cuenta")
    ai_engine.record_feedback(LEON, ids["g0"], "love")
    check("Un tarjeta con clic se puede calificar con corazón",
          storage.get_user_item(LEON, ids["g0"])["user_rating"] == "love")

    print("\n5. Orden de fuerza al puntuar")
    item = {"title": "Nota", "author": "Ana Autora", "raw_content": "x", "topic_tags": "[]"}
    base = ai_engine.score_for_user(item, {"focus_topics": [], "learned_preferences": {}})["relevance_score"]
    con1 = ai_engine.score_for_user(item, {"focus_topics": [], "learned_preferences": {"clicked_authors": {"Ana Autora": 1}}})["relevance_score"]
    con9 = ai_engine.score_for_user(item, {"focus_topics": [], "learned_preferences": {"clicked_authors": {"Ana Autora": 9}}})["relevance_score"]
    corazon = ai_engine.score_for_user(item, {"focus_topics": [], "learned_preferences": {"boosted_authors": ["Ana Autora"]}})["relevance_score"]
    check("Un clic sube más que nada", con1 > base, f"{base} -> {con1}")
    check("El corazón sube más que muchos clics", corazon > con9 > con1, f"corazón {corazon}, 9 clics {con9}, 1 clic {con1}")

    print("\n6. En el prompt de JEV solo entra quien abres seguido")
    q2 = ai_engine._jev_question({"focus_topics": [], "learned_preferences": {"clicked_authors": {"Ana Autora": 2}}})
    q3 = ai_engine._jev_question({"focus_topics": [], "learned_preferences": {"clicked_authors": {"Ana Autora": 3}}})
    texto = lambda q: q["interesa"]["criteria"]["true"]
    check("Con 2 clics no aparece", "Ana Autora" not in texto(q2))
    check("Con 3 clics aparece", "Ana Autora" in texto(q3))

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("El clic es una señal débil, de una sola vez, que nunca pisa una calificación.")


if __name__ == "__main__":
    main()
