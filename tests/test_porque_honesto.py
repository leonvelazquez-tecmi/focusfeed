"""El "Por qué" de la tarjeta debe ser cierto.

Dos defectos medidos el 17-oct con "Sovereign Engineering": (1) el score venía
de JEV y la nota del motor de palabras clave, dos motores en una misma tarjeta;
(2) una palabra suelta de un tema largo ("visual", "cultural", "post" de
"post-punk") bastaba para declarar que el artículo trataba del tema.

    python3 tests/test_porque_honesto.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_porque.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage  # noqa: E402
import app.ai_engine as ai  # noqa: E402

LEON = "44444444-4444-4444-4444-444444444444"
TEMAS = ["Inteligencia Artificial Aplicada y Sistemas Agénticos",
         "Cine de autor, narrativa visual y formato gran escala",
         "Música contemporánea, post-punk, vinilos y análisis cultural"]
fallos = []


def check(nombre, condicion, detalle=""):
    print(f"  [{'ok  ' if condicion else 'FALLA'}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def nota(texto, titulo="Titulo", autor="Fulano"):
    perfil = {"focus_topics": TEMAS, "learned_preferences": {}}
    item = {"title": titulo, "author": autor, "raw_content": texto, "topic_tags": []}
    return ai.score_for_user(item, perfil)["curator_note"]


def main():
    init_db()

    print("\n1. El motor de palabras clave no inventa coincidencias")
    n = nota("In this post we explain sovereign AI and who controls the data")
    check("Una palabra suelta ('post' de post-punk) no declara el tema", "Música" not in n, n)
    n = nota("Who can read the data, stop the service, or change how it behaves?")
    check("Un texto sobre IA soberana no coincide con cine ni con música",
          "Cine" not in n and "Música" not in n, n)
    n = nota("The granular approach to a visual cultural problem")
    check("'gran' no cuenta dentro de 'granular'", "Cine" not in n or "gran" not in n.lower(), n)

    print("\n2. Sigue coincidiendo cuando de verdad trata del tema")
    n = nota("Una guía de cine de autor y narrativa visual para ver este año")
    check("Dos palabras del tema sí cuentan", "Cine de autor" in n, n)

    print("\n3. Con score de JEV no hay 'Por qué' de otro motor")
    feeds = storage.batch_create_feeds([{"title": "F", "feed_url": "https://ejemplo.com/f", "feed_type": "rss"}])
    fid = feeds[0]["id"]
    storage.subscribe(LEON, fid)
    storage.save_profile_data(LEON, TEMAS, "Rigor")
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    storage.batch_save_items([{
        "feed_id": fid, "guid": "g1", "title": "Sovereign Engineering", "url": "https://ejemplo.com/1",
        "author": "The Business Engineer", "published_at": ahora, "content_type": "article",
        "raw_content": "A visual map of the cultural layer of the stack and the permission layer.",
    }])

    original = ai.score_with_jev
    ai.score_with_jev = lambda item, profile, user_id=None: 59
    try:
        ai.score_pending_for_user(LEON)
    finally:
        ai.score_with_jev = original
    fila = storage.fetch_items(LEON, tab="inbox")["items"][0]
    check("El score guardado es el de JEV", fila["relevance_score"] == 59, str(fila["relevance_score"]))
    check("Y no queda ninguna nota del motor de palabras clave", not fila["curator_note"],
          repr(fila["curator_note"]))

    print("\n4. Sin JEV, el respaldo guarda score y nota del mismo motor")
    storage.batch_save_items([{
        "feed_id": fid, "guid": "g2", "title": "Guia de cine", "url": "https://ejemplo.com/2",
        "author": "Autor", "published_at": ahora, "content_type": "article",
        "raw_content": "Una guía de cine de autor y narrativa visual.",
    }])
    ai.score_with_jev = lambda item, profile, user_id=None: None
    try:
        ai.score_pending_for_user(LEON)
    finally:
        ai.score_with_jev = original
    g2 = [i for i in storage.fetch_items(LEON, tab="inbox")["items"] if i["title"] == "Guia de cine"][0]
    check("El respaldo guarda un score propio", g2["relevance_score"] not in (None, 59), str(g2["relevance_score"]))
    check("Y una nota coherente con el texto", "Cine de autor" in (g2["curator_note"] or ""), repr(g2["curator_note"]))

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("El 'Por qué' solo aparece cuando es cierto.")


if __name__ == "__main__":
    main()
