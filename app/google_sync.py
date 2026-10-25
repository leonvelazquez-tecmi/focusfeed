"""Conexión con Google y detección automática de suscripciones.

Una sola conexión OAuth (YouTube y Gmail, solo lectura) permite que FocusFeed se
entere solo de lo que la persona sigue:

  * YouTube: sus suscripciones (YouTube Data API v3, subscriptions.list mine=true).
  * Gmail: las newsletters que le llegan (envíos masivos con List-Unsubscribe o
    List-Id). Si la fuente tiene RSS se suscribe al RSS; si no (LinkedIn y
    newsletters cerradas) se crea una fuente privada de correo y cada mensaje es
    un item que solo ve su dueño.

Reglas que rigen este archivo:
  - Solo cuentas personales. Una cuenta corporativa se rechaza y se
    desconecta: su correo solo lo lee un agente autorizado (app/outlook_ingest.py).
  - De Gmail solo se leen envíos masivos, nunca correo personal.
  - Lo que la persona quitó (dismissed_sources) no se vuelve a agregar solo.
  - `http_json` es el único punto de red hacia Google: las pruebas lo sustituyen.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup

from app import ingestion, storage

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
YT_SUBS_URL = "https://www.googleapis.com/youtube/v3/subscriptions"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"

# Cuentas cuyo correo no puede entrar a FocusFeed: lo corporativo pasa solo por el agente autorizado.
BLOCKED_DOMAINS = ("tecmilenio.mx", "tec.mx", "itesm.mx", "tecmilenio.edu.mx")

# Remitentes y hosts que no identifican a la publicación (proveedores de envío,
# plataformas con un dominio común, correo gratuito).
GENERIC_HOSTS = (
    "mailchimp.com", "list-manage.com", "campaign-archive.com", "sendgrid.net",
    "mandrillapp.com", "mailgun.org", "sparkpostmail.com", "constantcontact.com",
    "convertkit.com", "kit.com", "ck.page", "beehiiv.com", "mail.beehiiv.com",
    "substack.com", "medium.com", "linkedin.com", "google.com", "gmail.com",
    "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "amazonses.com",
    "sendinblue.com", "brevo.com", "hubspot.com", "customer.io", "klaviyo.com",
    "bounces.google.com", "mcsv.net", "rsgsv.net", "t.co",
)

AI_CAP_PER_RUN = 20          # items de correo analizados con IA por corrida
PER_SOURCE_MESSAGES = 5      # mensajes nuevos por fuente de correo y corrida
GMAIL_MAX_MESSAGES = 150
STATE_MAX_AGE = 900          # el state de OAuth vale 15 minutos


class GoogleError(Exception):
    """Falla de Google con un mensaje que se puede mostrar."""


class ReconnectNeeded(GoogleError):
    """El permiso fue revocado o caducó: hay que conectar de nuevo."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def configured() -> bool:
    return bool(_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET"))


def max_auto_sources() -> int:
    try:
        return max(0, int(_env("MAX_AUTO_SOURCES", "100")))
    except ValueError:
        return 100


def email_retention_days() -> int:
    try:
        return max(1, int(_env("EMAIL_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


# ==========================================================
# Red (único punto hacia Google)
# ==========================================================

def http_json(method: str, url: str, params: dict = None, form: dict = None,
              token: str = None, timeout: float = 12):
    """Devuelve (status, dict). Nunca lanza por un HTTP de error: el llamador
    decide con el status."""
    if params:
        pairs = []
        for k, v in params.items():
            for item in (v if isinstance(v, (list, tuple)) else [v]):
                pairs.append((k, item))
        url = f"{url}?{urllib.parse.urlencode(pairs)}"
    data = urllib.parse.urlencode(form).encode("utf-8") if form is not None else None
    headers = {"Accept": "application/json"}
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            return e.code, json.loads(raw) if raw else {}
        except ValueError:
            return e.code, {"error": {"message": raw[:300]}}
    except Exception as e:
        return 0, {"error": {"message": str(e)}}


def _err_message(data: dict) -> str:
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return err.get("message") or str(err)
    if isinstance(err, str):
        return data.get("error_description") or err
    return "error desconocido"


# ==========================================================
# OAuth
# ==========================================================

def _state_secret() -> bytes:
    secret = _env("FOCUSFEED_ADMIN_KEY") or _env("GOOGLE_CLIENT_SECRET")
    return secret.encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_state(user_id: str, now: float = None) -> str:
    """Token anti-CSRF: user_id + hora, firmado con HMAC. Evita que alguien más
    complete un flujo OAuth iniciado por otra persona."""
    body = _b64(json.dumps({"u": user_id, "t": int(now or time.time())}).encode("utf-8"))
    sig = _b64(hmac.new(_state_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_state(token: str, now: float = None):
    """user_id del state o None si la firma no cuadra o ya caducó."""
    try:
        body, sig = (token or "").split(".")
        expected = _b64(hmac.new(_state_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_unb64(body))
        if (now or time.time()) - int(payload["t"]) > STATE_MAX_AGE:
            return None
        return payload["u"]
    except Exception:
        return None


def auth_url(user_id: str, redirect_uri: str) -> str:
    if not configured():
        raise GoogleError("Google no está configurado (faltan GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET).")
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": _env("GOOGLE_CLIENT_ID"),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": sign_state(user_id),
    })


def exchange_code(code: str, redirect_uri: str) -> dict:
    status, data = http_json("POST", TOKEN_URL, form={
        "code": code, "client_id": _env("GOOGLE_CLIENT_ID"),
        "client_secret": _env("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if status != 200 or "access_token" not in data:
        raise GoogleError(f"Google rechazó el código: {_err_message(data)}")
    if not data.get("refresh_token"):
        raise GoogleError("Google no entregó permiso de largo plazo. Revoca el acceso de FocusFeed "
                          "en tu cuenta de Google y vuelve a conectar.")
    return data


def access_token(refresh_token: str) -> str:
    status, data = http_json("POST", TOKEN_URL, form={
        "client_id": _env("GOOGLE_CLIENT_ID"), "client_secret": _env("GOOGLE_CLIENT_SECRET"),
        "refresh_token": refresh_token, "grant_type": "refresh_token"})
    if status == 200 and data.get("access_token"):
        return data["access_token"]
    if data.get("error") == "invalid_grant":
        raise ReconnectNeeded("El permiso de Google caducó o fue revocado.")
    raise GoogleError(f"No se pudo renovar el acceso de Google: {_err_message(data)}")


def revoke(refresh_token: str):
    http_json("POST", REVOKE_URL, form={"token": refresh_token})


def blocked_account(email: str) -> bool:
    domain = (email or "").lower().rsplit("@", 1)[-1]
    return any(domain == d or domain.endswith("." + d) for d in BLOCKED_DOMAINS)


def finish_connect(code: str, state: str, redirect_uri: str) -> str:
    """Cierra el flujo OAuth: valida el state, canjea el código, rechaza cuentas
    corporativas y guarda el refresh token. Devuelve el user_id."""
    user_id = verify_state(state)
    if not user_id:
        raise GoogleError("La solicitud de conexión no es válida o caducó. Inténtalo de nuevo.")
    tokens = exchange_code(code, redirect_uri)
    status, profile = http_json("GET", f"{GMAIL}/profile", token=tokens["access_token"])
    email = profile.get("emailAddress", "") if status == 200 else ""
    if blocked_account(email):
        revoke(tokens["refresh_token"])
        raise GoogleError("Esa es una cuenta corporativa. Conecta tu cuenta personal de Google: "
                          "el correo corporativo solo lo lee un agente autorizado.")
    storage.save_google_connection(user_id, tokens["refresh_token"], tokens.get("scope", ""))
    return user_id


def disconnect(user_id: str):
    conn = storage.get_google_connection(user_id)
    if conn:
        revoke(conn["refresh_token"])
        storage.delete_google_connection(user_id)


def status(user_id: str) -> dict:
    conn = storage.get_google_connection(user_id) if configured() else None
    return {
        "configured": configured(),
        "connected": bool(conn),
        "last_scan_at": (conn or {}).get("last_scan_at"),
        "auto_sources": storage.count_auto_subscriptions(user_id),
        "max_auto_sources": max_auto_sources(),
    }


# ==========================================================
# YouTube
# ==========================================================

def youtube_subscriptions(token: str, max_pages: int = 20) -> list:
    """[{channel_id, title}] de las suscripciones de la persona."""
    out, page = [], None
    for _ in range(max_pages):
        params = {"part": "snippet", "mine": "true", "maxResults": 50, "order": "alphabetical"}
        if page:
            params["pageToken"] = page
        code, data = http_json("GET", YT_SUBS_URL, params=params, token=token)
        if code != 200:
            raise GoogleError(f"YouTube: {_err_message(data)}")
        for it in data.get("items", []):
            snip = it.get("snippet", {})
            cid = (snip.get("resourceId") or {}).get("channelId")
            if cid:
                out.append({"channel_id": cid, "title": snip.get("title") or cid})
        page = data.get("nextPageToken")
        if not page:
            break
    return out


# ==========================================================
# Gmail: lectura de envíos masivos (funciones puras, probables sin red)
# ==========================================================

def parse_message_meta(msg: dict) -> dict:
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in (msg.get("payload", {}).get("headers") or [])}
    name, addr = parseaddr(headers.get("from", ""))
    list_id = headers.get("list-id", "")
    m = re.search(r"<([^>]+)>", list_id)
    return {
        "id": msg.get("id"),
        "labels": msg.get("labelIds") or [],
        "from_name": name.strip().strip('"'),
        "from_addr": addr.lower(),
        "subject": headers.get("subject", "").strip(),
        "date": headers.get("date", ""),
        "list_id": (m.group(1) if m else list_id).strip().lower(),
        "list_id_name": re.sub(r"<[^>]*>", "", list_id).strip().strip('"'),
        "list_unsub": headers.get("list-unsubscribe", ""),
    }


def _host_is_generic(host: str) -> bool:
    host = (host or "").lower()
    return any(host == g or host.endswith("." + g) for g in GENERIC_HOSTS)


def group_newsletters(metas: list, min_count: int = 2) -> list:
    """Agrupa mensajes por fuente (List-Id o remitente). Un mensaje cuenta solo si
    es envío masivo y no cayó en Promociones ni Social. `qualifies` es la regla
    anti-ruido: List-Id explícito o al menos `min_count` envíos en la ventana."""
    groups = {}
    for m in metas:
        if not (m["list_id"] or m["list_unsub"]):
            continue
        if {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"} & set(m["labels"]):
            continue
        if blocked_account(m["from_addr"]):
            continue
        key = m["list_id"] or m["from_addr"]
        if not key:
            continue
        g = groups.setdefault(key, {
            "clave": key, "nombre": "", "from_addr": m["from_addr"], "list_id": m["list_id"],
            "list_unsub": m["list_unsub"], "ids": [], "count": 0})
        g["count"] += 1
        g["ids"].append(m)
        g["nombre"] = g["nombre"] or m["list_id_name"] or m["from_name"] or key
    out = []
    for g in groups.values():
        g["qualifies"] = bool(g["list_id"]) or g["count"] >= min_count
        g["ids"].sort(key=lambda x: _date_key(x["date"]), reverse=True)
        out.append(g)
    return out


def _date_key(raw: str) -> float:
    try:
        return parsedate_to_datetime(raw).timestamp()
    except Exception:
        return 0.0


def candidate_sites(group: dict) -> list:
    """Sitios donde buscar el RSS de la fuente: hosts de List-Unsubscribe y dominio
    del remitente, sin proveedores de envío ni dominios genéricos. Los de
    Substack salen primero porque son los más fiables."""
    hosts = []
    for url in re.findall(r"<(https?://[^>]+)>", group.get("list_unsub", "")):
        hosts.append(urllib.parse.urlparse(url).netloc.lower().split(":")[0])
    domain = group.get("from_addr", "").rsplit("@", 1)[-1]
    if domain:
        hosts.append(domain)
        if domain.count(".") >= 1:
            hosts.append("www." + domain if not domain.startswith("www.") else domain)
    seen, sites = set(), []
    for h in hosts:
        if not h or h in seen:
            continue
        seen.add(h)
        pub_substack = h.endswith(".substack.com") and h != "substack.com"
        if _host_is_generic(h) and not pub_substack:
            continue
        sites.append("https://" + h)
    sites.sort(key=lambda s: 0 if ".substack.com" in s else 1)
    return sites[:3]


def _decode_part(body: dict) -> str:
    data = (body or {}).get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _walk_parts(payload: dict):
    yield payload
    for part in payload.get("parts") or []:
        for sub in _walk_parts(part):
            yield sub


def message_text_and_html(msg: dict):
    """(texto_plano, html) del mensaje."""
    text, html = "", ""
    for part in _walk_parts(msg.get("payload") or {}):
        mime = part.get("mimeType", "")
        if mime == "text/plain" and not text:
            text = _decode_part(part.get("body"))
        elif mime == "text/html" and not html:
            html = _decode_part(part.get("body"))
    if not text and html:
        text = BeautifulSoup(html, "html.parser").get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
    return text, html


VIEW_RE = re.compile(r"(view|read).{0,25}(browser|online)|ver .{0,20}(navegador|en l[ií]nea)|leer en l[ií]nea",
                     re.IGNORECASE)


def web_link(html: str, message_id: str) -> str:
    """Enlace 'ver en el navegador' del correo; si no hay, el mensaje en Gmail."""
    if html:
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            if VIEW_RE.search(a.get_text(" ", strip=True)) and a["href"].startswith("http"):
                return a["href"]
    return f"https://mail.google.com/mail/u/0/#all/{message_id}"


def to_iso(raw: str) -> str:
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    except Exception:
        return datetime.utcnow().isoformat()


# ==========================================================
# Gmail: llamadas
# ==========================================================

def gmail_email(token: str) -> str:
    code, data = http_json("GET", f"{GMAIL}/profile", token=token)
    return data.get("emailAddress", "") if code == 200 else ""


def gmail_candidates(token: str, max_messages: int = GMAIL_MAX_MESSAGES) -> list:
    """Metadatos (solo cabeceras) de los envíos masivos de los últimos 30 días."""
    q = "unsubscribe newer_than:30d -in:spam -in:trash -category:promotions -category:social"
    ids, page = [], None
    while len(ids) < max_messages:
        params = {"q": q, "maxResults": min(100, max_messages - len(ids))}
        if page:
            params["pageToken"] = page
        code, data = http_json("GET", f"{GMAIL}/messages", params=params, token=token)
        if code != 200:
            raise GoogleError(f"Gmail: {_err_message(data)}")
        ids += [m["id"] for m in data.get("messages", [])]
        page = data.get("nextPageToken")
        if not page:
            break

    def meta(mid):
        code, data = http_json("GET", f"{GMAIL}/messages/{mid}", token=token, params={
            "format": "metadata",
            "metadataHeaders": ["From", "Subject", "Date", "List-Id", "List-Unsubscribe"]})
        return parse_message_meta(data) if code == 200 else None

    with ThreadPoolExecutor(8) as ex:
        return [m for m in ex.map(meta, ids[:max_messages]) if m]


def gmail_full(token: str, message_id: str):
    code, data = http_json("GET", f"{GMAIL}/messages/{message_id}", params={"format": "full"}, token=token)
    return data if code == 200 else None


# ==========================================================
# Orquestación
# ==========================================================

def _email_items(token: str, user_id: str, feed_id: int, group: dict, existing: set) -> list:
    items = []
    for meta in group["ids"][:PER_SOURCE_MESSAGES]:
        guid = f"gmail:{user_id}:{meta['id']}"
        if guid in existing:
            continue
        msg = gmail_full(token, meta["id"])
        if not msg:
            continue
        text, html = message_text_and_html(msg)
        items.append({
            "feed_id": feed_id, "guid": guid,
            "title": meta["subject"] or "(sin asunto)",
            "url": web_link(html, meta["id"]),
            "author": group["nombre"], "published_at": to_iso(meta["date"]),
            "content_type": "article", "raw_content": text[:2000],
            "owner_user_id": user_id,
        })
    return items


def discover(user_id: str, budget_seconds: float = 40) -> dict:
    """Compara lo que la persona sigue en Google con lo que ya está en FocusFeed
    y agrega lo nuevo. Devuelve qué se agregó para el aviso con Deshacer."""
    started = time.time()

    def left():
        return budget_seconds - (time.time() - started)

    conn = storage.get_google_connection(user_id)
    if not conn:
        return {"status": "not_connected", "added": []}
    try:
        token = access_token(conn["refresh_token"])
    except ReconnectNeeded:
        storage.delete_google_connection(user_id)
        return {"status": "reconnect", "added": []}
    except GoogleError as e:
        return {"status": "error", "added": [], "errors": [str(e)]}

    if blocked_account(gmail_email(token)):
        disconnect(user_id)
        return {"status": "account_not_allowed", "added": []}

    dismissed = storage.list_dismissed(user_id)
    feeds_now = storage.fetch_feeds(user_id)
    subscribed = {storage.source_key(f["feed_url"]): f for f in feeds_now}
    cap_left = max_auto_sources() - storage.count_auto_subscriptions(user_id)
    added, errors, over_cap, pending, new_items = [], [], 0, 0, 0

    # ---- YouTube ----
    try:
        for sub in youtube_subscriptions(token):
            feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={sub['channel_id']}"
            key = storage.source_key(feed_url)
            if key in dismissed or key in subscribed:
                continue
            if cap_left <= 0:
                over_cap += 1
                continue
            if left() < 8:
                pending += 1
                continue
            fid = storage.create_or_update_feed(
                user_id, title=sub["title"], feed_url=feed_url, feed_type="youtube",
                category="YouTube", site_url=f"https://www.youtube.com/channel/{sub['channel_id']}",
                origin="youtube")
            subscribed[key] = {"id": fid, "feed_url": feed_url}
            added.append({"feed_id": fid, "title": sub["title"], "origin": "youtube"})
            cap_left -= 1
    except GoogleError as e:
        errors.append(str(e))

    # ---- Gmail ----
    to_analyze = []
    try:
        groups = group_newsletters(gmail_candidates(token))
        for g in groups:
            if left() < 6:
                pending += 1
                continue
            private_url = storage.private_feed_url(user_id, g["clave"])
            private_key = storage.source_key(private_url)
            seen_key = "seen:" + g["clave"]

            if private_key in subscribed:               # fuente de correo ya existente
                feed = subscribed[private_key]
                existing = storage.get_existing_guids([feed["id"]])
                items = _email_items(token, user_id, feed["id"], g, existing)
                if items:
                    new_items += storage.batch_save_items(items)
                    to_analyze += [i["guid"] for i in items]
                continue

            if not g["qualifies"] or seen_key in dismissed or private_key in dismissed:
                continue
            if cap_left <= 0:
                over_cap += 1
                continue

            rss = None
            for site in candidate_sites(g):
                rss = ingestion.discover_rss(site)
                if rss:
                    break

            if rss:
                rss_key = storage.source_key(rss)
                if rss_key in dismissed or rss_key in subscribed:
                    storage.dismiss_source(user_id, seen_key)
                    continue
                ftype = "substack" if "substack.com" in rss else "rss"
                fid = storage.create_or_update_feed(
                    user_id, title=g["nombre"], feed_url=rss, feed_type=ftype,
                    category="Newsletters", site_url=rss, origin="gmail_rss")
                subscribed[rss_key] = {"id": fid, "feed_url": rss}
                added.append({"feed_id": fid, "title": g["nombre"], "origin": "gmail_rss"})
            else:
                fid = storage.create_or_update_feed(
                    user_id, title=g["nombre"], feed_url=private_url, feed_type="email",
                    category="Newsletters", site_url="", origin="gmail_email",
                    owner_user_id=user_id)
                subscribed[private_key] = {"id": fid, "feed_url": private_url}
                added.append({"feed_id": fid, "title": g["nombre"], "origin": "gmail_email"})
                items = _email_items(token, user_id, fid, g, set())
                if items:
                    new_items += storage.batch_save_items(items)
                    to_analyze += [i["guid"] for i in items]
            storage.dismiss_source(user_id, seen_key)
            cap_left -= 1
    except GoogleError as e:
        errors.append(str(e))

    analyzed = analyze_private(user_id, to_analyze)
    storage.update_google_scan(user_id)
    storage.purge_email_bodies(email_retention_days())
    return {"status": "ok" if not errors else "partial", "added": added, "new_items": new_items,
            "analyzed": analyzed, "over_cap": over_cap, "pending": pending, "errors": errors}


def analyze_private(user_id: str, guids: list) -> int:
    """Resumen con IA de los items de correo de Gmail personal, con tope por
    corrida. Los del buzón corporativo nunca llegan aquí (los resume el agente autorizado)."""
    guids = [g for g in guids if g.startswith("gmail:")][:AI_CAP_PER_RUN]
    if not guids:
        return 0
    from app.ai_engine import process_item_ai
    done = 0
    for item_id in storage.get_item_ids_by_guid(guids):
        try:
            if process_item_ai(item_id, user_id):
                done += 1
        except Exception as e:
            print(f"analyze_private: {e}")
    return done


def discover_all(budget_seconds: float = 45) -> dict:
    """Lo que corre el cron: cada persona con Google conectado, dentro del presupuesto."""
    started = time.time()
    results = {}
    for row in storage.list_google_connections():
        remaining = budget_seconds - (time.time() - started)
        if remaining < 10:
            results[row["user_id"]] = {"status": "pending"}
            continue
        results[row["user_id"]] = discover(row["user_id"], budget_seconds=remaining)
    return results
