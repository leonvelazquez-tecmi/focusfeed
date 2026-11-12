"""Los likes de YouTube enseñan gustos: canales y temas, sin guardar títulos.

Todo con respuestas simuladas de Google: no hay red ni credenciales reales. Lo
que no se puede probar aquí (el consentimiento real, el formato real de tus
likes, cuántos traen etiquetas, la cuota real) se prueba con tu cuenta.

    python3 tests/test_youtube_likes.py
"""

import http.client
import json
import os
import socketserver
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_likes.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)
os.environ["GOOGLE_CLIENT_ID"] = "id-de-prueba"
os.environ["GOOGLE_CLIENT_SECRET"] = "secreto-de-prueba"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage, google_sync as gs  # noqa: E402
import app.ai_engine as ai  # noqa: E402
from app.server import FeedCuratorHTTPHandler  # noqa: E402

LEON = "aaaaaaaa-0000-0000-0000-000000000001"
OTRA = "aaaaaaaa-0000-0000-0000-000000000002"
ANA = "aaaaaaaa-0000-0000-0000-000000000003"    # cuenta corporativa
BRUNO = "aaaaaaaa-0000-0000-0000-000000000004"  # permiso caducado
CARLA = "aaaaaaaa-0000-0000-0000-000000000005"  # errores de YouTube
DIEGO = "aaaaaaaa-0000-0000-0000-000000000006"  # sin conexión
fallos = []

ESTADO = {"likes": [], "email": "persona@gmail.com", "token_error": False, "videos_error": None, "calls": []}


def check(nombre, condicion, detalle=""):
    print(f"  [{'ok  ' if condicion else 'FALLA'}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def http_falso(method, url, params=None, form=None, token=None, timeout=12):
    if url == gs.TOKEN_URL:
        return (400, {"error": "invalid_grant"}) if ESTADO["token_error"] else (200, {"access_token": "tok"})
    if url.endswith("/profile"):
        return 200, {"emailAddress": ESTADO["email"]}
    if url == gs.YT_VIDEOS_URL:
        ESTADO["calls"].append(dict(params))
        if ESTADO["videos_error"]:
            return ESTADO["videos_error"]
        likes = ESTADO["likes"]
        inicio, tam = int(params.get("pageToken") or 0), int(params["maxResults"])
        data = {"items": likes[inicio:inicio + tam]}
        if inicio + tam < len(likes):
            data["nextPageToken"] = str(inicio + tam)
        return 200, data
    return 404, {}


def video(canal, tags=(), dur="PT10M", n=[0]):
    n[0] += 1
    return {"snippet": {"channelTitle": canal, "tags": list(tags), "title": f"Título secreto {n[0]}",
                        "description": "descripción secreta"},
            "contentDetails": {"duration": dur}}


def aprendido(uid=LEON):
    return storage.fetch_profile(uid).get("learned_preferences", {}) or {}


def yt(uid=LEON):
    return aprendido(uid).get("yt") or {}


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
    gs.http_json = http_falso
    for u in (LEON, OTRA, ANA, BRUNO, CARLA):
        storage.save_google_connection(u, f"refresh-{u[-1]}", "youtube.readonly gmail.readonly")

    print("\n1. Se lee de 50 en 50 y se topa en 300")
    ESTADO["likes"] = [video(f"Canal {n % 40}", ["tema común", f"solo{n}"]) for n in range(400)]
    r = gs.learn_from_likes(LEON, force=True)
    llamadas = ESTADO["calls"]
    check("Lee 300 likes de 400", r["status"] == "success" and r["likes_read"] == 300, str(r.get("likes_read")))
    check("Pide 6 páginas (no 7)", len(llamadas) == 6, f"{len(llamadas)}")
    check("Pide solo los likes, con canal y duración",
          llamadas[0]["myRating"] == "like" and llamadas[0]["maxResults"] == 50
          and "snippet" in llamadas[0]["part"] and "contentDetails" in llamadas[0]["part"])
    check("Encadena con pageToken", "pageToken" not in llamadas[0] and llamadas[1]["pageToken"] == "50")

    print("\n2. Filtros y normalización")
    ESTADO["likes"] = (
        [video("Canal Uno", ["Kaiju", "godzilla", "ab"]) for _ in range(3)]
        + [video("Banda - Topic") for _ in range(2)]
        + [video("Canal Corto", ["kaiju"], dur="PT45S") for _ in range(5)]      # Shorts
        + [video("Canal Frontera", dur="PT1M")]                                  # 60 s: cuenta como Short
        + [video("Canal Limite", dur="PT1M1S")]                                  # 61 s: sí cuenta
        + [video("", ["kaiju"])]                                                 # sin canal
        + [video("Canal Muchas", [f"etiqueta{n:02d}" for n in range(12)])]       # 12 etiquetas: tope de 10
    )
    ESTADO["calls"].clear()
    r = gs.learn_from_likes(LEON, force=True)
    y = yt()
    check("Cuenta todo lo leído, también lo filtrado", r["likes_read"] == len(ESTADO["likes"]), str(r["likes_read"]))
    check("Cuenta por canal, sin Shorts ni videos sin canal",
          y["authors"] == {"Canal Uno": 3, "Banda": 2, "Canal Limite": 1, "Canal Muchas": 1}, str(y["authors"]))
    check("Quita ' - Topic' de YouTube Music", "Banda" in y["authors"] and "Banda - Topic" not in y["authors"])
    check("Etiquetas en minúsculas, sin las muy cortas ni las de Shorts",
          y["tags"].get("kaiju") == 3 and y["tags"].get("godzilla") == 3 and "ab" not in y["tags"], str(y["tags"]))
    check("Máximo 10 etiquetas por video", sum(1 for t in y["tags"] if t.startswith("etiqueta")) == 10)
    check("No se guarda título, descripción ni id",
          "secreto" not in json.dumps(aprendido(), ensure_ascii=False))

    print("\n3. Es una foto: repetir no suma doble y un like quitado se desaprende")
    antes = dict(y["authors"])
    gs.learn_from_likes(LEON, force=True)
    check("Importar dos veces deja lo mismo", yt()["authors"] == antes, str(yt()["authors"]))
    ESTADO["likes"] = [v for v in ESTADO["likes"] if v["snippet"]["channelTitle"] != "Canal Uno"]
    gs.learn_from_likes(LEON, force=True)
    check("Un like quitado en YouTube desaparece", "Canal Uno" not in yt()["authors"] and "godzilla" not in yt()["tags"])

    print("\n4. Lo que ve JEV: frases exactas, solo con señal suficiente")
    perfil = {"focus_topics": ["x"], "learned_preferences": {"yt": {
        "authors": {"Canal Uno": 3, "Canal Dos": 2, "Canal Tres": 1},
        "tags": {"kaiju": 3, "solo uno": 1}}}}
    si = ai._jev_question(perfil)["interesa"]["criteria"]["true"]
    check("Frase de canales (3 y 2 likes sí, 1 like no)",
          " En YouTube suele dar like a canales como: Canal Uno, Canal Dos." in si and "Canal Tres" not in si, si[-200:])
    check("Frase de temas (peso 3 sí, peso 1 no)",
          " En los videos que le gustan en YouTube son frecuentes estos temas: kaiju." in si and "solo uno" not in si)
    perfil2 = {"focus_topics": ["x"], "learned_preferences": {
        "boosted_authors": ["Canal Uno"], "yt": perfil["learned_preferences"]["yt"]}}
    si2 = ai._jev_question(perfil2)["interesa"]["criteria"]["true"]
    check("Un canal que ya está en 'más valora' no se repite en la frase de YouTube",
          "Fuentes que más valora: Canal Uno." in si2 and "likes a canales como: Canal Dos." in si2.replace("dar like a", "likes a"),
          si2[-220:])
    no = ai._jev_question(perfil)["interesa"]["criteria"]["false"]
    check("En 'no vale' no entra nada de YouTube", "YouTube" not in no)
    vacio = ai._jev_question({"focus_topics": ["x"], "learned_preferences": {}})["interesa"]["criteria"]
    check("Sin datos las frases no aparecen", "YouTube" not in vacio["true"] + vacio["false"])
    con_titulos = json.dumps(ai._jev_question(storage.fetch_profile(LEON)), ensure_ascii=False)
    check("Nunca viajan títulos", "secreto" not in con_titulos)

    print("\n5. Peso en el motor de respaldo: más que un clic, mucho menos que un corazón")
    item = {"title": "Nota", "author": "Canal Uno", "raw_content": "x", "topic_tags": "[]"}
    puntos = lambda aprendido_: ai.score_for_user(item, {"focus_topics": [], "learned_preferences": aprendido_})["relevance_score"]
    base = puntos({})
    un_like, tres_likes = puntos({"yt": {"authors": {"Canal Uno": 1}}}), puntos({"yt": {"authors": {"Canal Uno": 3}}})
    cien_likes = puntos({"yt": {"authors": {"Canal Uno": 100}}})
    un_clic, corazon = puntos({"clicked_authors": {"Canal Uno": 1}}), puntos({"boosted_authors": ["Canal Uno"]})
    check("Un like de YouTube sube más que un clic", un_like > un_clic > base, f"{base} / clic {un_clic} / like {un_like}")
    check("Tiene tope (+12)", tres_likes == cien_likes == base + 12, f"{tres_likes} / {cien_likes}")
    check("Un corazón sigue pesando más", corazon > cien_likes, f"corazón {corazon}")

    print("\n6. Respeta lo demás y se puede olvidar")
    storage.update_learned_preferences(OTRA, {"boosted_tags": {"cine": 2}, "boosted_authors": ["Ana"]})
    ESTADO["likes"] = [video("Canal Dos", ["kaiju"]) for _ in range(2)]
    gs.learn_from_likes(OTRA, force=True)
    a = aprendido(OTRA)
    check("Aprender no pisa lo que ya había", a["boosted_tags"] == {"cine": 2} and a["boosted_authors"] == ["Ana"] and a["yt"]["authors"] == {"Canal Dos": 2})
    check("Cada persona aprende lo suyo", yt(LEON).get("authors") != yt(OTRA).get("authors"))
    r = gs.forget_likes(OTRA)
    a = aprendido(OTRA)
    check("Olvidar borra lo de YouTube y nada más", r["status"] == "success" and "yt" not in a and a["boosted_tags"] == {"cine": 2})
    check("Después de olvidar, no se refresca solo", gs.learn_from_likes(OTRA)["status"] == "disabled")

    print("\n7. Refresco diario")
    gs.learn_from_likes(LEON, force=True)
    ESTADO["calls"].clear()
    check("Con datos de hoy se salta", gs.learn_from_likes(LEON)["status"] == "skipped" and not ESTADO["calls"])
    viejo = aprendido()
    viejo["yt"]["imported_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    storage.update_learned_preferences(LEON, viejo)
    check("Pasadas 24 h vuelve a leer", gs.learn_from_likes(LEON)["status"] == "success" and ESTADO["calls"])
    check("Sin activar no corre por su cuenta", gs.learn_from_likes(DIEGO)["status"] == "disabled")
    check("Sin conexión de Google lo dice", gs.learn_from_likes(DIEGO, force=True)["status"] == "not_connected")

    print("\n8. Cuando Google dice que no")
    ESTADO["email"] = "alguien@tec.mx"
    r = gs.learn_from_likes(ANA, force=True)
    check("Cuenta corporativa: se rechaza y se desconecta",
          r["status"] == "account_not_allowed" and storage.get_google_connection(ANA) is None and not yt(ANA))
    ESTADO["email"] = "persona@gmail.com"
    ESTADO["token_error"] = True
    r = gs.learn_from_likes(BRUNO, force=True)
    check("Permiso caducado: pide reconectar", r["status"] == "reconnect" and storage.get_google_connection(BRUNO) is None
          and storage.google_expired(BRUNO))
    ESTADO["token_error"] = False
    ESTADO["videos_error"] = (403, {"error": {"message": "q", "errors": [{"reason": "quotaExceeded"}]}})
    r = gs.learn_from_likes(CARLA, force=True)
    check("Cuota agotada: mensaje claro y no se aprende nada", r["status"] == "error" and "límite diario" in r["message"] and not yt(CARLA), r.get("message", ""))
    ESTADO["videos_error"] = (403, {"error": {"message": "forbidden", "errors": [{"reason": "insufficientPermissions"}]}})
    r = gs.learn_from_likes(CARLA, force=True)
    check("Sin permiso: pide reconectar Google", r["status"] == "error" and "Reconecta" in r["message"], r.get("message", ""))
    ESTADO["videos_error"] = None

    print("\n9. Por HTTP, como lo usa la app")
    ESTADO["likes"] = [video("Canal HTTP", ["tema http"]) for _ in range(3)]
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FeedCuratorHTTPHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        puerto = srv.server_address[1]
        s, d = post(puerto, "/api/google/learn-likes", {"force": True}, {"X-Dev-User": LEON})
        check("learn-likes responde y aprende", s == 200 and d["status"] == "success" and d["likes_read"] == 3 and yt()["authors"] == {"Canal HTTP": 3}, str(d))
        s, d = post(puerto, "/api/google/learn-likes", {}, {"X-Dev-User": LEON})
        check("Sin force respeta el tope de 24 h", d["status"] == "skipped", str(d))
        s, d = post(puerto, "/api/google/forget-likes", {}, {"X-Dev-User": LEON})
        check("forget-likes borra", s == 200 and not yt(), str(d))
    finally:
        srv.shutdown()

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("Los likes enseñan canales y temas, sin títulos, y se pueden olvidar.")


if __name__ == "__main__":
    main()
