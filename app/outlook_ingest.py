"""Ingesta de newsletters del buzón corporativo, leídas por un agente autorizado.

El buzón corporativo solo lo lee un agente autorizado de la organización. Ese agente deja
un archivo por corrida en una carpeta sincronizada y un puente local (~/Scripts/focusfeed-outlook-bridge.py)
lo envía a POST /api/ingest/outlook. Este módulo recibe esos registros:

  {"tipo": "fuente", "clave", "nombre", "remitente", "list_id", "sitio_url", "rss_url", "envios_30d"}
  {"tipo": "item", "fuente_clave", "message_id", "asunto", "fecha", "enlace_web",
   "resumen_tldr", "puntos_clave": [], "temas": []}

Reglas que rigen este archivo:
  - Del correo solo cruza lo que el agente autorizado ya resumió. FocusFeed nunca recibe el cuerpo
    del correo y nunca llama a un modelo con contenido del buzón corporativo (los items llevan
    guid 'outlook:' y ai_engine los excluye de Gemini y JEV).
  - Una fuente con RSS público entra como suscripción normal: solo cruza nombre y URL.
  - Una fuente sin RSS es un feed privado (owner_user_id): solo lo ve su dueño.
  - Lo que la persona quitó (dismissed_sources) no se vuelve a agregar solo.
  - Es idempotente: el mismo archivo enviado dos veces no duplica fuentes ni items.
"""

import json
import os
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from app import ingestion, storage

MAX_RECORDS = 400
OUTLOOK_FALLBACK_URL = "https://outlook.office.com/mail/"


class IngestError(Exception):
    pass


def ingest_key_ok(headers) -> bool:
    """Falla cerrado: sin FOCUSFEED_INGEST_KEY configurada nadie puede ingerir."""
    import hmac
    expected = os.environ.get("FOCUSFEED_INGEST_KEY", "")
    auth = headers.get("Authorization", "") if headers else ""
    if not expected or not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[7:].strip(), expected)


def _iso(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return datetime.utcnow().isoformat()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
            timezone.utc).replace(tzinfo=None).isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    except Exception:
        return datetime.utcnow().isoformat()


def _http_url(raw: str) -> str:
    raw = (raw or "").strip()
    return raw if urllib.parse.urlparse(raw).scheme in ("http", "https") else ""


def _list(value) -> list:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def ingest(user_id: str, corrida: str, registros: list) -> dict:
    if not isinstance(registros, list):
        raise IngestError("registros debe ser una lista")
    registros = registros[:MAX_RECORDS]

    dismissed = storage.list_dismissed(user_id)
    subscribed = {storage.source_key(f["feed_url"]): f for f in storage.fetch_feeds(user_id)}
    cap_left = None  # se calcula solo si hay una fuente nueva
    added, omitted, errors = [], 0, []
    private_by_clave = {}   # clave -> feed_id de fuentes de correo (de este envío o previas)

    def private_url(clave):
        return storage.private_feed_url(user_id, "outlook:" + clave)

    # ---- Fuentes primero, para que los items encuentren su feed ----
    for r in (x for x in registros if isinstance(x, dict) and x.get("tipo") == "fuente"):
        clave = str(r.get("clave") or "").strip().lower()
        nombre = str(r.get("nombre") or clave).strip()
        if not clave:
            omitted += 1
            continue
        seen_key = "seen:outlook:" + clave
        p_key = storage.source_key(private_url(clave))

        if p_key in subscribed:                         # fuente privada ya existente
            private_by_clave[clave] = subscribed[p_key]["id"]
            continue
        if seen_key in dismissed or p_key in dismissed:
            omitted += 1
            continue

        rss = _http_url(r.get("rss_url")) or ingestion.discover_rss(_http_url(r.get("sitio_url")))
        if rss:
            rss_key = storage.source_key(rss)
            if rss_key in dismissed or rss_key in subscribed:
                storage.dismiss_source(user_id, seen_key)
                omitted += 1
                continue

        if cap_left is None:
            cap_left = _cap_left(user_id)
        if cap_left <= 0:
            omitted += 1
            continue

        try:
            if rss:
                ftype = "substack" if "substack.com" in rss else "rss"
                fid = storage.create_or_update_feed(
                    user_id, title=nombre, feed_url=rss, feed_type=ftype,
                    category="Newsletters", site_url=rss, origin="outlook_rss")
                subscribed[rss_key] = {"id": fid, "feed_url": rss}
                added.append({"feed_id": fid, "title": nombre, "origin": "outlook_rss"})
            else:
                fid = storage.create_or_update_feed(
                    user_id, title=nombre, feed_url=private_url(clave), feed_type="email",
                    category="Newsletters", site_url="", origin="outlook_email",
                    owner_user_id=user_id)
                subscribed[p_key] = {"id": fid, "feed_url": private_url(clave)}
                private_by_clave[clave] = fid
                added.append({"feed_id": fid, "title": nombre, "origin": "outlook_email"})
            storage.dismiss_source(user_id, seen_key)
            cap_left -= 1
        except Exception as e:
            errors.append(f"fuente {clave}: {e}")

    # ---- Items: solo de fuentes privadas (las de RSS traen su contenido por RSS) ----
    items = []
    names = {str(r.get("clave") or "").strip().lower(): str(r.get("nombre") or "").strip()
             for r in registros if isinstance(r, dict) and r.get("tipo") == "fuente"}
    for r in (x for x in registros if isinstance(x, dict) and x.get("tipo") == "item"):
        clave = str(r.get("fuente_clave") or "").strip().lower()
        p_key = storage.source_key(private_url(clave))
        fid = private_by_clave.get(clave) or (subscribed.get(p_key) or {}).get("id")
        message_id = str(r.get("message_id") or "").strip()
        if not fid or not message_id or p_key not in subscribed:
            omitted += 1
            continue
        items.append({
            "feed_id": fid,
            "guid": f"outlook:{user_id}:{message_id}",
            "title": str(r.get("asunto") or "(sin asunto)").strip(),
            "url": _http_url(r.get("enlace_web")) or OUTLOOK_FALLBACK_URL,
            "author": names.get(clave) or subscribed[p_key].get("title") or "Newsletter",
            "published_at": _iso(r.get("fecha")),
            "content_type": "article",
            "raw_content": "",
            "summary_tldr": str(r.get("resumen_tldr") or "").strip(),
            "key_takeaways": json.dumps(_list(r.get("puntos_clave")), ensure_ascii=False),
            "topic_tags": json.dumps(_list(r.get("temas")), ensure_ascii=False),
            "owner_user_id": user_id,
            "ai_processed": True,
        })

    saved = 0
    if items:
        try:
            saved = storage.batch_save_items(items)
        except Exception as e:
            errors.append(f"items: {e}")

    return {"status": "ok" if not errors else "partial", "corrida": corrida,
            "fuentes_agregadas": added, "items_nuevos": saved,
            "omitidos": omitted, "errores": errors}


def _cap_left(user_id: str) -> int:
    from app.google_sync import max_auto_sources
    return max_auto_sources() - storage.count_auto_subscriptions(user_id)
