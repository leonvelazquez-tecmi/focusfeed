"""El prompt de JEV no contradice los temas de ocio del lector, y el criterio no se pisa.

Medido el 12-nov: el "no vale" decía "entretenimiento… reseña de ocio", pero León
declara cine de autor, música y enología entre sus temas, y sus fuentes con boost
incluyen críticos de cine y música. Además "Actualizar temas" borraba el criterio.

    python3 tests/test_prompt_jev.py
"""

import contextlib
import http.client
import io
import json
import os
import socketserver
import sys
import tempfile
import threading

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_prompt.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage  # noqa: E402
import app.ai_engine as ai  # noqa: E402
from app.server import FeedCuratorHTTPHandler  # noqa: E402

LEON = "11111111-2222-3333-4444-555555555555"
TEMAS_LEON = [
    "Inteligencia Artificial Aplicada y Sistemas Agénticos",
    "Gestión del Conocimiento Personal (PKM, Obsidian, Zettelkasten)",
    "Transformación Institucional y Modelos Educativos",
    "Cine de autor, narrativa visual y formato gran escala",
    "Música contemporánea, post-punk, vinilos y análisis cultural",
    "Enología y exploración gastronómica",
]
CRITERIO = "Prioriza profundidad conceptual, rigor analítico y valor duradero."
fallos = []


def check(nombre, condicion, detalle=""):
    print(f"  [{'ok  ' if condicion else 'FALLA'}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def textos(perfil):
    c = ai._jev_question(perfil)["interesa"]["criteria"]
    return c["true"], c["false"]


def post(port, path, body, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    c.request("POST", path, body=json.dumps(body), headers=h)
    r = c.getresponse()
    data = json.loads(r.read() or b"{}")
    c.close()
    return r.status, data


def main():
    init_db()

    print("\n1. El texto ya no contradice los temas de ocio")
    perfil = {"focus_topics": TEMAS_LEON, "system_prompt_criteria": CRITERIO, "learned_preferences": {}}
    si, no = textos(perfil)
    check("'No vale' ya no dice 'reseña de ocio'", "reseña de ocio" not in no)
    check("Ni trata el entretenimiento como ruido por defecto", "Es entretenimiento" not in no)
    check("Dice que un tema declarado no es ruido si hay análisis o crítica",
          "declara como suyo" in no and "análisis o crítica" in no)
    check("Un anuncio o promoción sí es ruido", "promoción o anuncio" in no)
    check("'Sí vale' pide sustancia, criterio o ideas propias", "análisis, crítica con criterio" in si)
    check("Lista los 6 temas completos", all(t in si for t in TEMAS_LEON))
    check("Conserva el criterio del lector", CRITERIO in si)
    check("El texto es genérico: no nombra a nadie ni sus temas fuera de la lista",
          "León" not in (si + no) and "cine" not in no.lower() and "música" not in no.lower())

    print("\n2. Sin historial, la pregunta no trae frases extra")
    check("Sin lo aprendido no hay frases de aprendizaje",
          "Por lo que ha calificado" not in si and "Suele descartar" not in no and "Ejemplos" not in si + no)

    print("\n3. Una etiqueta de una sola señal débil no llega a JEV")
    aprendido = {"boosted_tags": {"TagUno": 1, "TagDos": 2, "TagTres": 4},
                 "penalized_tags": {"TagCuatro": 2, "TagCinco": 1, "TagTres": 1}}
    si, no = textos({"focus_topics": ["x"], "learned_preferences": aprendido})
    check("Saldo 1 a favor no entra", "TagUno" not in si)
    check("Saldo 2 a favor sí entra", "TagDos" in si)
    check("Saldo neto 3 (4 a favor, 1 en contra) entra", "TagTres" in si)
    check("Saldo 1 en contra no entra", "TagCinco" not in no)
    check("Saldo 2 en contra sí entra", "TagCuatro" in no)

    print("\n4. Nada privado entra (buzón corporativo)")
    priv = {"boosted_tags": {"Secreto": 5}, "private_tags": ["Secreto"],
            "boosted_authors": ["Dirección"], "private_authors": ["Dirección"]}
    si, no = textos({"focus_topics": ["x"], "learned_preferences": priv})
    check("Ni la etiqueta ni el remitente privados", "Secreto" not in si + no and "Dirección" not in si + no)

    print("\n5. El criterio ya no se pisa al guardar temas")
    storage.save_profile_data(LEON, ["Tema A"], "Mi criterio propio")
    p = storage.fetch_profile(LEON)
    check("Con criterio, se guarda", p["system_prompt_criteria"] == "Mi criterio propio")
    storage.save_profile_data(LEON, ["Tema A", "Tema B"])
    p = storage.fetch_profile(LEON)
    check("Sin criterio (None), se conserva", p["system_prompt_criteria"] == "Mi criterio propio", p["system_prompt_criteria"])
    check("Y los temas sí cambian", p["focus_topics"] == ["Tema A", "Tema B"])
    storage.save_profile_data(LEON, ["Tema A"], "")
    check("Con cadena vacía, vuelve al valor por defecto",
          storage.fetch_profile(LEON)["system_prompt_criteria"] == storage.DEFAULT_CRITERIA)

    print("\n6. Por HTTP: 'Actualizar temas' solo manda los temas")
    storage.save_profile_data(LEON, ["Tema A"], "Criterio de León")
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FeedCuratorHTTPHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s, _ = post(srv.server_address[1], "/api/profile", {"focus_topics": ["Cine", "Música"]}, {"X-Dev-User": LEON})
        p = storage.fetch_profile(LEON)
        check("La petición responde bien", s == 200, str(s))
        check("Los temas se actualizan", p["focus_topics"] == ["Cine", "Música"])
        check("El criterio sigue ahí", p["system_prompt_criteria"] == "Criterio de León", p["system_prompt_criteria"])
        post(srv.server_address[1], "/api/profile",
             {"focus_topics": ["Cine"], "system_prompt_criteria": "Otro criterio"}, {"X-Dev-User": LEON})
        check("Si el cuerpo trae criterio, se actualiza",
              storage.fetch_profile(LEON)["system_prompt_criteria"] == "Otro criterio")
    finally:
        srv.shutdown()

    print("\n7. routines/jev_eval.py --comparar corre de punta a punta (JEV y Supabase simulados)")
    import routines.jev_eval as ev
    llamadas = []

    def jev_falso(key, item, q):
        llamadas.append(q["interesa"]["criteria"]["false"])
        antes = "reseña de ocio" in q["interesa"]["criteria"]["false"]
        return (0.2 if antes else 0.7) if item["id"] % 2 else 0.3, 0.00003, 0.1

    filas = [{"user_id": LEON, "item_id": n, "user_rating": r, "relevance_score": 50}
             for n, r in enumerate(["love", "love", "dislike", "dislike", "like", "save"], start=1)]
    items = [{"id": n, "title": f"Item {n}", "author": "A", "content_type": "article", "raw_content": "x",
              "topic_tags": "[]", "summary_tldr": None} for n in range(1, 7)]

    def supabase_falso(endpoint, *a, **k):
        return filas if endpoint.startswith("user_items") else items

    ev.supabase_request, ev.jev, ev.llave = supabase_falso, jev_falso, lambda: "llave"
    ev.fetch_profile = lambda uid: {"focus_topics": TEMAS_LEON, "system_prompt_criteria": CRITERIO,
                                    "learned_preferences": {}}
    sys.argv = ["jev_eval.py", "--comparar"]
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        ev.main()
    texto = salida.getvalue()
    check("Imprime el prompt anterior y el nuevo", "prompt anterior" in texto and "prompt nuevo" in texto)
    check("Imprime AUC y promedio por calificación", "AUC=" in texto and "love" in texto and "dislike" in texto)
    check("Imprime los items que más cambiaron", "Items donde más cambió el score" in texto)
    check("Se mandaron las dos versiones del texto", any("reseña de ocio" in t for t in llamadas)
          and any("declara como suyo" in t for t in llamadas), f"{len(llamadas)} llamadas")
    check("La versión anterior reconstruye el texto de antes",
          ev.ANTES_NO in ev.pregunta_anterior({"focus_topics": TEMAS_LEON, "learned_preferences": {}})["interesa"]["criteria"]["false"])

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("El prompt respeta los temas del lector y el criterio ya no se pisa.")


if __name__ == "__main__":
    main()
