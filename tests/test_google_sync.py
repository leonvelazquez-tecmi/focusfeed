"""Detección automática de suscripciones con Google (YouTube y Gmail).

Todo con respuestas simuladas de Google: no hay red ni credenciales reales.
Lo que no se puede probar aquí (el consentimiento real y tu buzón real) se
prueba contigo en local.

    python3 tests/test_google_sync.py
"""

import base64
import os
import re
import sys
import tempfile

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_google.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)
os.environ["GOOGLE_CLIENT_ID"] = "id-de-prueba"
os.environ["GOOGLE_CLIENT_SECRET"] = "secreto-de-prueba"
os.environ.pop("MAX_AUTO_SOURCES", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage, google_sync as gs, ingestion  # noqa: E402
import app.ai_engine as ai  # noqa: E402

ANA = "11111111-1111-1111-1111-111111111111"
BRUNO = "22222222-2222-2222-2222-222222222222"
CARLA = "33333333-3333-3333-3333-333333333333"

UC_A, UC_B, UC_C = "UC" + "A" * 22, "UC" + "B" * 22, "UC" + "C" * 22
YT = "https://www.youtube.com/feeds/videos.xml?channel_id="

fallos = []


def check(nombre, condicion, detalle=""):
    print(f"  [{'ok  ' if condicion else 'FALLA'}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def b64(t):
    return base64.urlsafe_b64encode(t.encode()).decode()


MSGS = {
    # Newsletter con RSS: dos envíos y List-Id
    "m1": {"labels": [], "from": "Weekly <hi@news.example.com>", "subject": "Edición 1",
           "date": "Mon, 12 Oct 2026 10:00:00 +0000", "list_id": "<weekly.news.example.com>",
           "unsub": "<https://news.example.com/unsub>"},
    "m2": {"labels": [], "from": "Weekly <hi@news.example.com>", "subject": "Edición 2",
           "date": "Mon, 05 Oct 2026 10:00:00 +0000", "list_id": "<weekly.news.example.com>",
           "unsub": "<https://news.example.com/unsub>"},
    # LinkedIn: sin RSS, entra como correo privado
    "m3": {"labels": [], "from": "LinkedIn News <news@linkedin.com>", "subject": "Lo que pasa en IA",
           "date": "Tue, 13 Oct 2026 08:00:00 +0000", "list_id": "<linkedin-news.linkedin.com>",
           "unsub": "", "html": '<p>Hola</p><a href="https://www.linkedin.com/pulse/x">View in browser</a>'},
    "m4": {"labels": [], "from": "LinkedIn News <news@linkedin.com>", "subject": "Otra edición",
           "date": "Tue, 06 Oct 2026 08:00:00 +0000", "list_id": "<linkedin-news.linkedin.com>",
           "unsub": ""},
    # Una tienda con un solo envío y sin List-Id: ruido, no entra
    "m5": {"labels": [], "from": "Tienda <ofertas@tienda.com>", "subject": "Oferta",
           "date": "Mon, 12 Oct 2026 09:00:00 +0000", "list_id": "",
           "unsub": "<https://tienda.com/baja>"},
    # Promociones: excluido por categoría de Gmail
    "m6": {"labels": ["CATEGORY_PROMOTIONS"], "from": "Promo <p@promo.example.com>", "subject": "Descuento",
           "date": "Mon, 12 Oct 2026 09:00:00 +0000", "list_id": "<promo.promo.example.com>", "unsub": ""},
    # Remitente corporativo: nunca entra por Google
    "m7": {"labels": [], "from": "Jefe <jefe@tecmilenio.mx>", "subject": "Boletín interno",
           "date": "Mon, 12 Oct 2026 09:00:00 +0000", "list_id": "<interno.tecmilenio.mx>", "unsub": ""},
}

STATE = {"email": "persona@gmail.com", "invalid_grant": False}
REVOCADOS = []
FULL_CALLS = []


def fake_http(method, url, params=None, form=None, token=None, timeout=12):
    if url == gs.TOKEN_URL:
        if STATE["invalid_grant"]:
            return 400, {"error": "invalid_grant"}
        return 200, {"access_token": "tok"}
    if url == gs.REVOKE_URL:
        REVOCADOS.append(form["token"])
        return 200, {}
    if url == f"{gs.GMAIL}/profile":
        return 200, {"emailAddress": STATE["email"]}
    if url == gs.YT_SUBS_URL:
        return 200, {"items": [
            {"snippet": {"title": "Canal A", "resourceId": {"channelId": UC_A}}},
            {"snippet": {"title": "Canal B", "resourceId": {"channelId": UC_B}}},
            {"snippet": {"title": "Canal C", "resourceId": {"channelId": UC_C}}},
        ]}
    if url == f"{gs.GMAIL}/messages":
        return 200, {"messages": [{"id": i} for i in MSGS]}
    m = re.match(re.escape(gs.GMAIL) + r"/messages/(\w+)$", url)
    if m:
        spec = MSGS[m.group(1)]
        if params["format"] == "metadata":
            headers = [{"name": "From", "value": spec["from"]}, {"name": "Subject", "value": spec["subject"]},
                       {"name": "Date", "value": spec["date"]}]
            if spec["list_id"]:
                headers.append({"name": "List-Id", "value": spec["list_id"]})
            if spec["unsub"]:
                headers.append({"name": "List-Unsubscribe", "value": spec["unsub"]})
            return 200, {"id": m.group(1), "labelIds": spec["labels"], "payload": {"headers": headers}}
        FULL_CALLS.append(m.group(1))
        parts = [{"mimeType": "text/plain", "body": {"data": b64("Cuerpo del boletín de " + spec["subject"])}}]
        if spec.get("html"):
            parts.append({"mimeType": "text/html", "body": {"data": b64(spec["html"])}})
        return 200, {"id": m.group(1), "payload": {"mimeType": "multipart/alternative", "parts": parts}}
    return 404, {"error": {"message": "ruta no simulada " + url}}


def fake_discover_rss(site, fetch=None):
    return "https://news.example.com/feed" if site == "https://news.example.com" else None


ANALIZADOS = []


def fake_process(item_id, user_id=None):
    ANALIZADOS.append(item_id)
    return {"summary_tldr": "x"}


gs.http_json = fake_http
ingestion.discover_rss = fake_discover_rss
ai.process_item_ai = fake_process


def main():
    init_db()

    print("\n1. State de OAuth firmado")
    tok = gs.sign_state(ANA, now=1000)
    check("Un state válido devuelve su user_id", gs.verify_state(tok, now=1100) == ANA)
    check("Un state alterado se rechaza", gs.verify_state(tok[:-3] + "xxx", now=1100) is None)
    check("Un state caducado se rechaza", gs.verify_state(tok, now=1000 + gs.STATE_MAX_AGE + 5) is None)
    check("Sin Google configurado no hay URL de conexión",
          "client_id=id-de-prueba" in gs.auth_url(ANA, "http://localhost:8080/api/google/callback"))

    print("\n2. Primera revisión de Ana")
    storage.save_google_connection(ANA, "refresh-ana", "yt gmail")
    storage.create_or_update_feed(ANA, "Canal B", YT + UC_B, "youtube", "General")   # ya lo seguía
    storage.dismiss_source(ANA, YT + UC_C)                                          # lo había quitado
    r = gs.discover(ANA)
    origenes = {a["title"]: a["origin"] for a in r["added"]}
    check("Agrega el canal nuevo de YouTube", origenes.get("Canal A") == "youtube", str(origenes))
    check("No re-agrega el canal que ya seguía", "Canal B" not in origenes)
    check("No re-agrega el canal que había quitado", "Canal C" not in origenes)
    check("La newsletter con RSS entra como suscripción normal",
          origenes.get("Weekly") == "gmail_rss" or origenes.get("weekly.news.example.com") == "gmail_rss", str(origenes))
    check("LinkedIn (sin RSS) entra como fuente de correo",
          "gmail_email" in origenes.values(), str(origenes))
    check("La tienda de un solo envío no entra", not any("ienda" in t for t in origenes))
    check("La promoción no entra", not any("Promo" in t for t in origenes))
    check("El remitente corporativo no entra", not any("nterno" in t or "Jefe" in t for t in origenes))
    check("Quedan exactamente 3 fuentes nuevas (A, newsletter, LinkedIn)", len(r["added"]) == 3, str(len(r["added"])))

    feeds = {f["feed_url"]: f for f in storage.fetch_feeds(ANA)}
    privado = [f for f in feeds.values() if f["feed_type"] == "email"]
    check("La fuente de correo es privada de Ana", len(privado) == 1 and privado[0].get("owner_user_id") == ANA)
    check("El feed de correo no tiene URL real", privado[0]["feed_url"].startswith("email:" + ANA))
    check("Cada suscripción guarda su origen",
          feeds[YT + UC_A]["origin"] == "youtube" and feeds[YT + UC_B]["origin"] == "manual")

    print("\n3. Los items de correo")
    mios = storage.fetch_items(ANA, tab="inbox")["items"]
    correo = [i for i in mios if i["guid"].startswith("gmail:")]
    check("LinkedIn trajo 2 items", len(correo) == 2, str(len(correo)))
    por_asunto = {i["title"]: i for i in correo}
    check("El enlace es el de 'ver en el navegador'",
          por_asunto["Lo que pasa en IA"]["url"] == "https://www.linkedin.com/pulse/x")
    check("Sin ese enlace cae al mensaje de Gmail",
          por_asunto["Otra edición"]["url"].startswith("https://mail.google.com/mail/u/0/#all/m4"))
    check("El cuerpo quedó guardado para el resumen", "Cuerpo del boletín" in por_asunto["Otra edición"]["raw_content"])
    check("Se analizaron con IA (Gmail personal)", len(ANALIZADOS) == 2, str(len(ANALIZADOS)))

    print("\n4. Aislamiento")
    check("Bruno no ve las fuentes de Ana", storage.fetch_feeds(BRUNO) == [])
    check("Bruno no ve los items de correo de Ana",
          not [i for i in storage.fetch_items(BRUNO, tab="inbox")["items"] if i["guid"].startswith("gmail:")])
    check("El cron no intenta bajar feeds de correo",
          not [f for f in storage.fetch_all_active_feeds() if f["feed_type"] == "email"])
    check("Los items privados no entran a la cola de IA del corpus",
          not set(storage.get_pending_item_ids(50)) & {i["id"] for i in correo})

    print("\n5. Es idempotente y respeta lo que Ana quita")
    r2 = gs.discover(ANA)
    check("La segunda revisión no agrega nada", r2["added"] == [], str(r2["added"]))
    check("Y no duplica items", r2["new_items"] == 0)
    weekly = next(f for f in storage.fetch_feeds(ANA) if f["feed_url"] == "https://news.example.com/feed")
    storage.forget_feed(ANA, weekly["id"])           # al quitar una automática se recuerda
    r3 = gs.discover(ANA)
    check("Una fuente automática que Ana quita no vuelve sola", r3["added"] == [], str(r3["added"]))
    check("Una manual quitada sí queda libre (no se recordó)",
          storage.source_key(YT + UC_B) not in storage.list_dismissed(ANA))

    print("\n6. Tope de fuentes automáticas")
    os.environ["MAX_AUTO_SOURCES"] = "1"
    storage.save_google_connection(BRUNO, "refresh-bruno", "yt gmail")
    r4 = gs.discover(BRUNO)
    check("Solo agrega hasta el tope", len(r4["added"]) == 1, str(len(r4["added"])))
    check("Avisa cuántas quedaron fuera", r4["over_cap"] >= 1, str(r4["over_cap"]))
    os.environ.pop("MAX_AUTO_SOURCES")

    print("\n7. Cuentas corporativas")
    STATE["email"] = "alguien@tecmilenio.mx"
    storage.save_google_connection(CARLA, "refresh-carla", "yt gmail")
    r5 = gs.discover(CARLA)
    check("Una cuenta de Tecmilenio se rechaza", r5["status"] == "account_not_allowed", r5["status"])
    check("Se revoca y se borra la conexión",
          storage.get_google_connection(CARLA) is None and "refresh-carla" in REVOCADOS)
    check("Y no se agregó nada", storage.fetch_feeds(CARLA) == [])
    STATE["email"] = "persona@gmail.com"

    print("\n8. Permiso revocado en Google")
    STATE["invalid_grant"] = True
    r6 = gs.discover(ANA)
    check("Pide reconectar", r6["status"] == "reconnect", r6["status"])
    check("Y borra la conexión caducada", storage.get_google_connection(ANA) is None)
    STATE["invalid_grant"] = False

    print("\n9. Retención de correo")
    storage.purge_email_bodies(0)
    cuerpos = [i["raw_content"] for i in storage.fetch_items(ANA, tab="inbox")["items"]
               if i["guid"].startswith("gmail:")]
    check("Pasado el plazo se borra el cuerpo y quedan título y enlace",
          cuerpos and all(c == "" for c in cuerpos), str(cuerpos))

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("Detección con Google verificada (con respuestas simuladas).")


if __name__ == "__main__":
    main()
