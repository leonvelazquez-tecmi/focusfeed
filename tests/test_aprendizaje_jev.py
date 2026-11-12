"""Lo que el lector califica llega de verdad a la pregunta que se le hace a JEV.

Cubre: temas por peso neto, fuentes por fuerza (no 8 al azar), ejemplos recientes,
guardar / quitar de guardados, pulgar abajo que penaliza la fuente, y que nada
del buzón corporativo viaje a JEV.

    python3 tests/test_aprendizaje_jev.py
"""

import json
import os
import sys
import tempfile

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_aprendizaje.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage, ai_engine  # noqa: E402

LEON = "55555555-5555-5555-5555-555555555555"
OTRA = "66666666-6666-6666-6666-666666666666"
fallos = []


def check(nombre, condicion, detalle=""):
    print(f"  [{'ok  ' if condicion else 'FALLA'}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def aprendido(user=LEON):
    return storage.fetch_profile(user).get("learned_preferences", {}) or {}


def prompt(user=LEON):
    q = ai_engine._jev_question(storage.fetch_profile(user))["interesa"]["criteria"]
    return q["true"], q["false"]


def main():
    init_db()
    feed = storage.batch_create_feeds([{"title": "Canal", "feed_url": "https://e.com/c", "feed_type": "rss"}])[0]["id"]
    buzon = storage.batch_create_feeds([{"title": "Buzón", "feed_url": "outlook://x", "feed_type": "rss"}])[0]["id"]
    for u in (LEON, OTRA):
        storage.subscribe(u, feed)
        storage.subscribe(u, buzon)

    filas = []
    # 12 autores con un item cada uno, para probar que entran los más fuertes y no 8 al azar.
    for k in range(12):
        filas.append({"feed_id": feed, "guid": f"a{k}", "title": f"Nota de autor {k}", "url": f"https://e.com/a{k}",
                      "author": f"Autor {k:02d}", "published_at": "2026-11-01T10:00:00", "content_type": "article",
                      "raw_content": "x", "topic_tags": json.dumps([f"Tema{k:02d}", "Agentes"])})
    filas.append({"feed_id": feed, "guid": "farandula", "title": "El divorcio del año", "url": "https://e.com/f",
                  "author": "Farándula TV", "published_at": "2026-11-01T10:00:00", "content_type": "article",
                  "raw_content": "x", "topic_tags": json.dumps(["Farándula"])})
    for n in range(3):
        filas.append({"feed_id": feed, "guid": f"far{n}", "title": f"Chisme {n}", "url": f"https://e.com/far{n}",
                      "author": "Farándula TV", "published_at": "2026-11-01T10:00:00", "content_type": "article",
                      "raw_content": "x", "topic_tags": json.dumps(["Farándula"])})
    filas.append({"feed_id": buzon, "guid": "outlook:1", "title": "Reporte confidencial de rectoría", "url": "https://e.com/o",
                  "author": "Dirección Académica", "published_at": "2026-11-01T10:00:00", "content_type": "article",
                  "raw_content": "x", "topic_tags": json.dumps(["Acreditación interna"])})
    storage.batch_save_items(filas)
    ids = {i["guid"]: i["id"] for i in storage.fetch_items(LEON, tab="inbox", limit=300)["items"]}

    print("\n1. La pregunta a JEV recibe temas y fuentes por fuerza, no al azar")
    for k in range(12):                       # corazón a los 12 autores...
        ai_engine.record_feedback(LEON, ids[f"a{k}"], "love")
    ai_engine.record_feedback(LEON, ids["a5"], "like")   # a05 sube a 3+2 = 5, el más fuerte
    si, no = prompt()
    check("Aparece el tema aprendido de mayor peso (Agentes)", "Agentes" in si, si[-160:])
    check("Entran como máximo 8 fuentes", si.count("Autor ") <= 8)
    check("La más fuerte (Autor 05) está entre las que valora", "Autor 05" in si)
    check("Los ejemplos recientes que le gustaron viajan con comillas", "«Nota de autor" in si)

    print("\n2. Una fuente descartada de forma repetida se va a 'suele descartar'")
    ai_engine.record_feedback(LEON, ids["farandula"], "dislike")
    _, no1 = prompt()
    check("Con un pulgar abajo aún no se declara fuente descartada", "Fuentes que suele descartar" not in no1)
    ai_engine.record_feedback(LEON, ids["far0"], "dislike")
    _, no2 = prompt()
    check("Con dos pulgares abajo sí (saldo -6)", "Fuentes que suele descartar: Farándula TV" in no2, no2[-200:])
    check("El tema Farándula aparece como descartado", "Farándula" in no2.split("Suele descartar temas como:")[-1])
    check("Aparecen títulos descartados como ejemplo", "Ejemplos recientes que descartó" in no2 and "Chisme 0" in no2)
    item = {"title": "Otra nota", "author": "Farándula TV", "raw_content": "x", "topic_tags": "[]"}
    base = ai_engine.score_for_user(item, {"focus_topics": [], "learned_preferences": {}})["relevance_score"]
    con = ai_engine.score_for_user(item, {"focus_topics": [], "learned_preferences": aprendido()})["relevance_score"]
    check("El motor de respaldo también baja a esa fuente", con < base, f"{base} -> {con}")

    print("\n3. Guardar enseña y quitar de guardados lo deshace")
    antes = aprendido()
    ai_engine.record_save(LEON, ids["far1"])           # guardar un item de Farándula TV (ya penalizada)
    check("Queda registrado como 'save'", storage.get_user_item(LEON, ids["far1"])["user_rating"] == "save")
    a = aprendido()
    check("El autor sube 2", a["author_scores"]["Farándula TV"] == antes["author_scores"]["Farándula TV"] + 2)
    check("Los tags suben 1", a["boosted_tags"].get("Farándula", 0) == antes["boosted_tags"].get("Farándula", 0) + 1)
    ai_engine.record_save(LEON, ids["far1"])           # repetir no suma otra vez
    check("Guardar dos veces el mismo no suma doble", aprendido() == a)
    ai_engine.record_unsave(LEON, ids["far1"])
    check("Quitar de guardados deja el rating en 'none'", storage.get_user_item(LEON, ids["far1"])["user_rating"] == "none")
    despues = aprendido()
    # liked_examples es una ventana de los últimos 6 títulos: guardar desplaza al más viejo y
    # deshacer no lo trae de vuelta. Por eso se compara aparte de los contadores.
    # boosted_authors sale de un set: el orden puede cambiar entre corridas aunque los datos sean iguales.
    def sin_orden(v):
        return sorted(v) if isinstance(v, list) and all(isinstance(x, str) for x in v) else v

    difiere = {k: (antes.get(k), despues.get(k)) for k in set(antes) | set(despues)
               if k != "liked_examples" and sin_orden(antes.get(k)) != sin_orden(despues.get(k))}
    check("Los contadores (temas, fuentes) vuelven a como estaban", not difiere, str(difiere))
    check("El título guardado ya no figura como ejemplo",
          all(e.get("id") != ids["far1"] for e in despues.get("liked_examples", [])))

    print("\n4. Guardar nunca pisa una calificación explícita")
    ai_engine.record_feedback(LEON, ids["a1"], "love")
    antes = aprendido()
    ai_engine.record_save(LEON, ids["a1"])
    check("Corazón sigue siendo corazón", storage.get_user_item(LEON, ids["a1"])["user_rating"] == "love")
    check("Y guardar encima no suma", aprendido() == antes)
    ai_engine.record_unsave(LEON, ids["a1"])
    check("Quitar de guardados no deshace un corazón", aprendido() == antes)

    print("\n5. Un clic previo no impide guardar, y el clic posterior al guardado no suma")
    ai_engine.record_feedback(LEON, ids["far2"], "click")
    ai_engine.record_save(LEON, ids["far2"])
    check("Clic seguido de guardar queda como 'save'", storage.get_user_item(LEON, ids["far2"])["user_rating"] == "save")
    antes = aprendido()
    ai_engine.record_feedback(LEON, ids["far2"], "click")
    check("Un clic posterior a guardar no suma", aprendido() == antes)

    print("\n6. Nada del buzón corporativo viaja a JEV")
    ai_engine.record_feedback(LEON, ids["outlook:1"], "love")
    a = aprendido()
    si, no = prompt()
    todo = si + no
    check("El título no se guarda como ejemplo", all("confidencial" not in str(e) for e in a.get("liked_examples", [])))
    check("El remitente no entra a la pregunta", "Dirección Académica" not in todo)
    check("La etiqueta no entra a la pregunta", "Acreditación interna" not in todo)
    check("Sí queda marcado como privado", "Dirección Académica" in a.get("private_authors", [])
          and "Acreditación interna" in a.get("private_tags", []))
    ai_engine.record_feedback(LEON, ids["outlook:1"], "dislike")
    todo = "".join(prompt())
    check("Tampoco como descartado", "Dirección Académica" not in todo and "Reporte confidencial" not in todo)

    print("\n7. Cada usuario aprende solo de sí mismo")
    check("La otra cuenta no hereda nada", aprendido(OTRA).get("boosted_tags") in (None, {}) and
          "Autor 05" not in "".join(prompt(OTRA)))

    print("\n8. Perfil sin historial: la pregunta queda como antes")
    si, no = prompt(OTRA)
    check("Sin lo aprendido no aparecen frases nuevas", "Por lo que ha calificado" not in si
          and "Suele descartar" not in no and "Ejemplos" not in si + no)

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("Lo que calificas llega a la pregunta de JEV, y lo privado se queda fuera.")


if __name__ == "__main__":
    main()
