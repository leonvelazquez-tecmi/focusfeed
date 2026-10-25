"""Newsletters del buzón corporativo que llegan resumidas por un agente autorizado.

Comprueba el contrato con el puente local (registros 'fuente' e 'item'), que sea
idempotente, que lo privado no se filtre y, sobre todo, que nada de Outlook pueda
salir a un modelo externo (Gemini o JEV).

    python3 tests/test_outlook_ingest.py
"""

import http.client
import json
import os
import socketserver
import sys
import tempfile
import threading
import urllib.request

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_outlook.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)
os.environ["FOCUSFEED_INGEST_KEY"] = "llave-de-prueba"
os.environ.pop("MAX_AUTO_SOURCES", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage, outlook_ingest, ingestion  # noqa: E402
import app.ai_engine as ai  # noqa: E402
from app.server import FeedCuratorHTTPHandler  # noqa: E402

ANA = "11111111-1111-1111-1111-111111111111"
BRUNO = "22222222-2222-2222-2222-222222222222"

fallos = []


def check(nombre, condicion, detalle=""):
    print(f"  [{'ok  ' if condicion else 'FALLA'}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


ingestion.discover_rss = lambda site, fetch=None: None   # sin red: nada se "descubre"

REGISTROS = [
    {"tipo": "fuente", "clave": "blog-con-rss", "nombre": "Blog con RSS", "remitente": "hi@blog.example.com",
     "list_id": "blog.example.com", "sitio_url": "https://blog.example.com",
     "rss_url": "https://blog.example.com/feed.xml", "envios_30d": 4},
    {"tipo": "fuente", "clave": "boletin-cerrado", "nombre": "Boletín cerrado", "remitente": "n@cerrado.example.com",
     "list_id": "cerrado.example.com", "sitio_url": "", "rss_url": "", "envios_30d": 3},
    {"tipo": "item", "fuente_clave": "boletin-cerrado", "message_id": "AAMk-1", "asunto": "Tendencias de abril",
     "fecha": "2026-10-13T08:00:00Z", "enlace_web": "https://cerrado.example.com/ed/1",
     "resumen_tldr": "Resumen escrito por el agente.", "puntos_clave": ["Uno", "Dos"], "temas": ["IA"]},
    {"tipo": "item", "fuente_clave": "boletin-cerrado", "message_id": "AAMk-2", "asunto": "Sin enlace",
     "fecha": "2026-10-14T08:00:00Z", "enlace_web": "", "resumen_tldr": "Otro resumen.",
     "puntos_clave": [], "temas": []},
    {"tipo": "item", "fuente_clave": "blog-con-rss", "message_id": "AAMk-3", "asunto": "De fuente con RSS",
     "fecha": "2026-10-13T08:00:00Z", "enlace_web": "", "resumen_tldr": "x", "puntos_clave": [], "temas": []},
    {"tipo": "item", "fuente_clave": "no-existe", "message_id": "AAMk-4", "asunto": "Huérfano",
     "fecha": "2026-10-13T08:00:00Z", "enlace_web": "", "resumen_tldr": "x", "puntos_clave": [], "temas": []},
]


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

    print("\n1. Primer envío del puente")
    r = outlook_ingest.ingest(ANA, "prog-1", REGISTROS)
    origenes = {a["title"]: a["origin"] for a in r["fuentes_agregadas"]}
    check("La fuente con RSS entra como suscripción pública", origenes.get("Blog con RSS") == "outlook_rss", str(origenes))
    check("La fuente sin RSS entra como correo privado", origenes.get("Boletín cerrado") == "outlook_email")
    check("Entran los 2 items del boletín cerrado", r["items_nuevos"] == 2, str(r["items_nuevos"]))
    check("El item de una fuente con RSS y el huérfano se omiten", r["omitidos"] == 2, str(r["omitidos"]))
    feeds = {f["title"]: f for f in storage.fetch_feeds(ANA)}
    check("El feed de correo es de Ana y sin URL real",
          feeds["Boletín cerrado"]["owner_user_id"] == ANA and feeds["Boletín cerrado"]["feed_url"].startswith("email:"))
    check("El feed con RSS apunta a la URL pública",
          feeds["Blog con RSS"]["feed_url"] == "https://blog.example.com/feed.xml")

    print("\n2. Lo que ve Ana")
    items = {i["title"]: i for i in storage.fetch_items(ANA, tab="inbox")["items"]}
    it = items["Tendencias de abril"]
    check("El resumen es el que escribió el agente", it["summary_tldr"] == "Resumen escrito por el agente.")
    check("Los puntos y temas llegan", json.loads(it["key_takeaways"]) == ["Uno", "Dos"] and json.loads(it["topic_tags"]) == ["IA"])
    check("Ya viene analizado (no pasa por IA)", it["ai_processed"] == 1)
    check("No guarda el cuerpo del correo", it["raw_content"] == "")
    check("El guid marca el origen Outlook", it["guid"].startswith("outlook:" + ANA))
    check("Sin enlace web cae a Outlook en la web", items["Sin enlace"]["url"].startswith("https://outlook.office.com"))

    print("\n3. Nada de Outlook sale a un modelo externo")
    llamadas = []
    original = urllib.request.urlopen

    def bloqueada(*a, **k):
        llamadas.append(a)
        raise RuntimeError("red bloqueada")

    urllib.request.urlopen = bloqueada
    try:
        ai.OPENROUTER_API_KEY = "llave-falsa"
        puntaje = ai.score_with_jev(it, storage.fetch_profile(ANA), ANA)
        analisis = ai.process_item_ai(it["id"], ANA)
    finally:
        urllib.request.urlopen = original
    check("JEV no recibe items de Outlook", puntaje is None)
    check("Gemini no analiza items de Outlook", analisis is None)
    check("Ninguna de las dos hizo una llamada de red", llamadas == [], str(llamadas))
    check("Los items de Outlook no están en la cola de IA del corpus",
          it["id"] not in storage.get_pending_item_ids(100))

    print("\n4. Es idempotente")
    r2 = outlook_ingest.ingest(ANA, "prog-1", REGISTROS)
    check("El mismo archivo dos veces no agrega fuentes", r2["fuentes_agregadas"] == [], str(r2["fuentes_agregadas"]))
    check("Ni duplica items", r2["items_nuevos"] == 0, str(r2["items_nuevos"]))

    print("\n5. Aislamiento")
    check("Bruno no ve las fuentes de Ana", storage.fetch_feeds(BRUNO) == [])
    check("Bruno no ve los items de Outlook de Ana",
          not [i for i in storage.fetch_items(BRUNO, tab="inbox")["items"] if i["guid"].startswith("outlook:")])
    check("El cron no intenta bajar feeds de correo",
          not [f for f in storage.fetch_all_active_feeds() if f["feed_type"] == "email"])

    print("\n6. Lo que Ana quita no vuelve")
    storage.forget_feed(ANA, feeds["Boletín cerrado"]["id"])
    r3 = outlook_ingest.ingest(ANA, "prog-2", REGISTROS)
    check("La fuente quitada no se vuelve a agregar", not [a for a in r3["fuentes_agregadas"] if a["title"] == "Boletín cerrado"])
    check("Y sus items nuevos tampoco entran", r3["items_nuevos"] == 0)

    print("\n7. Por HTTP: la llave de ingesta")
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FeedCuratorHTTPHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cuerpo = {"corrida": "http-1", "registros": [REGISTROS[0]]}
        s, _ = post(port, "/api/ingest/outlook", cuerpo)
        check("Sin llave: 401", s == 401, str(s))
        s, _ = post(port, "/api/ingest/outlook", cuerpo, {"Authorization": "Bearer otra"})
        check("Con llave equivocada: 401", s == 401, str(s))
        s, d = post(port, "/api/ingest/outlook", {"corrida": "http-2", "registros": [REGISTROS[1]]},
                    {"Authorization": "Bearer llave-de-prueba", "X-Dev-User": BRUNO})
        check("Con la llave correcta: 200 y agrega la fuente", s == 200 and len(d["fuentes_agregadas"]) == 1, f"{s} {d}")
        check("Entra a la cuenta que corresponde (Bruno)", [f["title"] for f in storage.fetch_feeds(BRUNO)] == ["Boletín cerrado"])
        s, _ = post(port, "/api/ingest/outlook", {"corrida": "x", "registros": "no es lista"},
                    {"Authorization": "Bearer llave-de-prueba"})
        check("Registros mal formados: 400", s == 400, str(s))

        os.environ.pop("FOCUSFEED_INGEST_KEY")
        s, _ = post(port, "/api/ingest/outlook", cuerpo, {"Authorization": "Bearer llave-de-prueba"})
        check("Sin llave configurada en el servidor nadie ingiere (falla cerrado)", s == 401, str(s))
        os.environ["FOCUSFEED_INGEST_KEY"] = "llave-de-prueba"

        print("\n8. Por HTTP: excluir una fuente")
        propios = storage.fetch_feeds(BRUNO)
        s, _ = post(port, f"/api/feeds/{propios[0]['id']}/dismiss", {}, {"X-Dev-User": BRUNO})
        check("Excluir la quita y la recuerda",
              s == 200 and storage.fetch_feeds(BRUNO) == []
              and storage.source_key(propios[0]["feed_url"]) in storage.list_dismissed(BRUNO))
    finally:
        srv.shutdown()

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("Ingesta de Outlook verificada: nada sale a un modelo externo y lo privado no se filtra.")


if __name__ == "__main__":
    main()
