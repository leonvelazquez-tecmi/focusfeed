import http.server
import socketserver
import json
import urllib.parse
import urllib.request
import urllib.error
import os
import re
import time
from datetime import datetime, timezone
import concurrent.futures

from app.db import init_db, is_supabase
from app.auth import resolve_user
from app.storage import (
    fetch_items, set_item_status, bulk_set_status,
    fetch_feeds, fetch_all_active_feeds, create_or_update_feed, unsubscribe,
    fetch_profile, save_profile_data, batch_save_items,
    ensure_seed_if_empty, get_pending_item_ids, usage_summary, save_feed_cache,
    get_existing_guids, items_missing_image, save_item_thumbnail, NO_IMAGE
)
from app.ingestion import (
    parse_youtube_feed_xml, parse_rss_feed_xml,
    parse_opml, resolve_feed_url, HEADERS
)
from app.extractors import fetch_og_image
from app.shorts import drop_shorts
from app.ai_engine import (
    process_item_ai, record_feedback, score_pending_for_user, ai_health, ai_canary,
    RATING_TO_STATUS
)

PORT = int(os.environ.get("PORT", 8080))
_CANARIO = {"t": 0.0, "res": {}}
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

socketserver.TCPServer.allow_reuse_address = True


def fetch_feed_and_parse(feed_dict: dict) -> list:
    """Descarga un feed y devuelve sus items. Solo red y parseo: el resumen de IA
    va después, y solo para lo nuevo (/api/items/process-pending).

    Manda If-None-Match / If-Modified-Since con lo que guardó la sync anterior;
    un 304 devuelve [] sin parsear. Los headers nuevos quedan en feed_dict
    (_etag, _last_modified) para que sync_feeds los guarde."""
    feed_url = feed_dict.get("feed_url")
    feed_type = feed_dict.get("feed_type", "rss")
    feed_id = feed_dict.get("id", 1)

    headers = dict(HEADERS)
    if feed_dict.get("etag"):
        headers["If-None-Match"] = feed_dict["etag"]
    if feed_dict.get("last_modified"):
        headers["If-Modified-Since"] = feed_dict["last_modified"]

    long_url = youtube_long_form_url(feed_url) if feed_type == "youtube" else None
    if long_url:
        # Solo videos largos: la playlist UULF del canal no trae Shorts.
        items = _fetch_and_parse(feed_dict, long_url, headers, feed_type, feed_id)
        if items is not None:
            return items
        # UULF no existe para este canal: feed completo, filtrando Shorts uno por uno.
        return drop_shorts(_fetch_and_parse(feed_dict, feed_url, headers, feed_type, feed_id) or [])
    return _fetch_and_parse(feed_dict, feed_url, headers, feed_type, feed_id) or []


def youtube_long_form_url(feed_url: str):
    """Feed de la playlist UULF (solo videos largos) de un canal de YouTube, o
    None si la URL no es de canal. UC<id> es el canal, UULF<id> sus videos
    largos y UUSH<id> sus Shorts: verificado contra /shorts/<id>."""
    m = re.search(r'channel_id=UC([A-Za-z0-9_-]{22})', feed_url or "")
    if not m:
        return None
    return f"https://www.youtube.com/feeds/videos.xml?playlist_id=UULF{m.group(1)}"


def _fetch_and_parse(feed_dict: dict, feed_url: str, headers: dict, feed_type: str, feed_id):
    """Descarga y parsea. None solo cuando la URL da 404 (para el respaldo de
    UULF); [] en cualquier otra falla o en 304."""
    try:
        req = urllib.request.Request(feed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            feed_dict["_fetched"] = True
            feed_dict["_etag"] = resp.headers.get("ETag") or ""
            feed_dict["_last_modified"] = resp.headers.get("Last-Modified") or ""
            xml_content = resp.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 304:
            feed_dict["_fetched"] = True
            feed_dict["_etag"], feed_dict["_last_modified"] = feed_dict.get("etag"), feed_dict.get("last_modified")
        else:
            print(f"Error obteniendo feed {feed_url}: HTTP {e.code}")
        return []
    except Exception as e:
        print(f"Error obteniendo feed {feed_url}: {e}")
        return []

    if feed_type == "youtube":
        return parse_youtube_feed_xml(xml_content, feed_id)
    return parse_rss_feed_xml(xml_content, feed_id)


def run_parallel(fn, args: list, workers: int, timeout: float) -> list:
    """Corre fn sobre args en paralelo y devuelve los resultados que terminaron
    a tiempo. Un elemento lento ya no tumba la tanda completa: antes el
    TimeoutError de as_completed salía del try y la respuesta era un 500."""
    results = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures = [executor.submit(fn, a) for a in args]
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=timeout):
            try:
                results.append(fut.result())
            except Exception as e:
                print(f"run_parallel: {e}")
    except concurrent.futures.TimeoutError:
        print(f"run_parallel: {sum(not f.done() for f in futures)} sin terminar a los {timeout}s")
    executor.shutdown(wait=False, cancel_futures=True)
    return results


FRESH_SECONDS = 15 * 60  # YouTube no manda ETag; publica con max-age=900


def is_fresh(feed: dict) -> bool:
    last = feed.get("last_synced_at")
    if not last:
        return False
    try:
        dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() < FRESH_SECONDS
    except ValueError:
        return False


OG_IMAGE_CAP = 40  # páginas por tanda: cuidan el presupuesto de 45 s del cron


def only_new(items: list) -> list:
    """Items cuyo guid todavía no está en el corpus."""
    if not items:
        return []
    already = get_existing_guids([i.get("feed_id") for i in items])
    return [i for i in items if i["guid"] not in already]


def enrich_images(items: list, timeout: float = 4) -> None:
    """Artículos sin imagen en el feed: toma og:image de la página, en paralelo
    y con tope. Lo que no alcance a resolverse se queda con el respaldo de
    color en la tarjeta. Timeout corto a propósito: la última tanda del cron
    puede arrancar en el segundo 44 y la función muere a los 60."""
    missing = [i for i in items if not i.get("thumbnail_url") and i.get("url")][:OG_IMAGE_CAP]
    if not missing:
        return

    def resolve(item):
        item["thumbnail_url"] = fetch_og_image(item["url"], timeout=3.5)

    run_parallel(resolve, missing, workers=16, timeout=timeout)


def prepare_new_items(items: list) -> list:
    """Solo lo nuevo, ya con imagen. Se corre antes de batch_save_items porque
    ese insert nunca actualiza items existentes."""
    fresh = only_new(items)
    enrich_images(fresh)
    return fresh


def fill_images(item_ids: list) -> dict:
    """Imagen bajo demanda para las tarjetas que se van a mostrar: og:image de
    la página, una sola vez. Si no hay, se guarda NO_IMAGE para no volver a
    pedirla. Devuelve {id: url} solo de las que sí trajeron imagen."""
    pending = items_missing_image(item_ids[:16])
    if not pending:
        return {}

    def resolve(item):
        url = fetch_og_image(item.get("url"), timeout=3.5)
        save_item_thumbnail(item["id"], url or NO_IMAGE)
        return item["id"], url

    return {i: u for i, u in run_parallel(resolve, pending, workers=16, timeout=6) if u}


def sync_feeds(feeds: list, timeout: float = 12, force: bool = False) -> int:
    """Descarga feeds en paralelo, guarda los items nuevos y la caché HTTP de
    cada feed. Salta los sincronizados hace menos de 15 min salvo force.
    Devuelve cuántos items nuevos entraron al corpus."""
    if not force:
        feeds = [f for f in feeds if not is_fresh(f)]
    batches = run_parallel(fetch_feed_and_parse, feeds, workers=16, timeout=timeout)
    new_items = prepare_new_items([i for b in batches for i in (b or [])])
    for f in feeds:
        if f.get("_fetched"):
            try:
                save_feed_cache(f.get("id"), f.get("_etag"), f.get("_last_modified"))
            except Exception as e:
                print(f"save_feed_cache: {e}")
    return batch_save_items(new_items) if new_items else 0


class FeedCuratorHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def send_json_response(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Dev-User')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode('utf-8'))

    def current_user(self):
        return resolve_user(self.headers)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Dev-User')
        self.end_headers()

    # ------------------------------------------------------
    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return

        if path == "/manifest.json":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            with open(os.path.join(STATIC_DIR, "manifest.json"), "rb") as f:
                self.wfile.write(f.read())
            return

        ICONOS = {
            "/icon.svg": "image/svg+xml", "/icon-192.png": "image/png",
            "/icon-512.png": "image/png", "/apple-touch-icon.png": "image/png",
            "/favicon-32.png": "image/png", "/favicon.ico": "image/x-icon",
        }
        if path in ICONOS:
            with open(os.path.join(STATIC_DIR, path.lstrip("/")), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ICONOS[path])
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/health/ai":
            # Canario público de la IA. Cacheado 10 min para que nadie pueda
            # usarlo para gastar tokens. 503 si Gemini o JEV fallan.
            ahora = time.time()
            if ahora - _CANARIO["t"] > 600:
                _CANARIO["res"], _CANARIO["t"] = ai_canary(), ahora
            res = _CANARIO["res"]
            ok = all(v == "sin_llave" or v.startswith("ok") for v in res.values())
            self.send_json_response({"ok": ok, **res, **ai_health()}, 200 if ok else 503)
            return

        if path == "/api/status":
            self.send_json_response({
                "status": "online",
                "is_supabase": is_supabase(),
                "user_id": self.current_user(),
                "supabase_url_configured": bool(os.environ.get("SUPABASE_URL")),
                "supabase_key_configured": bool(os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY")),
                "ai_configured": bool(os.environ.get("GEMINI_API_KEY")),
                **ai_health()
            })
            return

        # Sincronización del corpus. No pertenece a ningún usuario: se descarga
        # una vez y sirve a todos los suscriptores de ese feed.
        if path == "/api/cron/sync":
            cron_secret = os.environ.get("CRON_SECRET")
            if cron_secret:
                if self.headers.get("Authorization", "") != f"Bearer {cron_secret}":
                    self.send_json_response({"status": "error", "message": "No autorizado"}, 401)
                    return

            started = time.time()
            time_budget_seconds = 45
            batch_size = 40

            try:
                feeds = fetch_all_active_feeds()
                total_feeds = len(feeds)
                feeds_synced = 0
                total_new_items = 0
                offset = 0

                while offset < total_feeds:
                    if time.time() - started > time_budget_seconds:
                        break
                    batch_slice = feeds[offset:offset + batch_size]
                    total_new_items += sync_feeds(batch_slice)
                    feeds_synced += len(batch_slice)
                    offset += batch_size

                canario = ai_canary()
                self.send_json_response({
                    "status": "success", "feeds_synced": feeds_synced, "canario_ia": canario,
                    "total_feeds": total_feeds, "new_items": total_new_items,
                    "completed": offset >= total_feeds,
                    "elapsed_seconds": round(time.time() - started, 1)
                })
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/items":
            user_id = self.current_user()
            tab = query.get("tab", ["curated"])[0]
            type_filter = query.get("type", ["all"])[0]
            try:
                limit = int(query.get("limit", ["120"])[0])
            except ValueError:
                limit = 120
            limit = max(16, min(limit, 300))
            try:
                self.send_json_response(fetch_items(user_id, tab=tab, type_filter=type_filter, limit=limit))
            except Exception as e:
                self.send_json_response({"status": "error", "message": f"No se pudo leer el feed: {e}"}, 502)
            return

        if path == "/api/feeds":
            try:
                self.send_json_response(fetch_feeds(self.current_user()))
            except Exception:
                self.send_json_response([], 200)
            return

        if path == "/api/profile":
            try:
                self.send_json_response(fetch_profile(self.current_user()))
            except Exception:
                self.send_json_response({}, 200)
            return

        if path == "/api/usage":
            try:
                self.send_json_response(usage_summary(self.current_user()))
            except Exception as e:
                self.send_json_response({"error": str(e)}, 200)
            return

        self.send_response(404)
        self.end_headers()

    # ------------------------------------------------------
    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        user_id = self.current_user()

        content_length = int(self.headers.get('Content-Length', 0))
        body_str = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else "{}"
        try:
            body = json.loads(body_str) if body_str else {}
        except Exception:
            body = {}

        match_feedback = re.match(r"^/api/items/(\d+)/feedback$", path)
        if match_feedback:
            item_id = int(match_feedback.group(1))
            rating = body.get("rating", "like")
            try:
                record_feedback(user_id, item_id, rating, body.get("comment", ""))
                # El frontend necesita saber a dónde se movió el item para
                # actualizar la tarjeta sin recargar todo el feed.
                self.send_json_response({
                    "status": "success", "item_id": item_id,
                    "new_status": RATING_TO_STATUS.get(rating)
                })
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # Analiza el corpus (universal) y luego pone score personal al lector actual.
        if path == "/api/items/process-pending":
            limit = int(body.get("limit", 10))
            try:
                pending_ids = get_pending_item_ids(limit)
                results = run_parallel(lambda iid: process_item_ai(iid, user_id),
                                       pending_ids, workers=6, timeout=20)
                processed = sum(1 for r in results if r)
                scored = score_pending_for_user(user_id)
                self.send_json_response({"status": "success", "processed": processed,
                                         "scored": scored, "has_more": len(pending_ids) >= limit})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        match_status = re.match(r"^/api/items/(\d+)/status$", path)
        if match_status:
            item_id = int(match_status.group(1))
            try:
                set_item_status(user_id, item_id, body.get("status", "inbox"))
                self.send_json_response({"status": "success", "item_id": item_id})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/items/fill-images":
            try:
                self.send_json_response({"status": "success", "images": fill_images(body.get("ids", []))})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/items/bulk-status":
            try:
                updated = bulk_set_status(user_id, body.get("ids", []), body.get("status", "archived"))
                self.send_json_response({"status": "success", "updated": updated})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/feeds":
            raw_url = body.get("url", "").strip()
            if not raw_url:
                self.send_json_response({"status": "error", "message": "URL requerida"}, 400)
                return
            try:
                resolved_feed_url, detected_type, suggested_title = resolve_feed_url(raw_url)
                final_title = body.get("title", "").strip() or suggested_title or raw_url
                feed_id = create_or_update_feed(
                    user_id, title=final_title, feed_url=resolved_feed_url,
                    feed_type=detected_type, category=body.get("category", "General").strip(),
                    site_url=raw_url)

                items = fetch_feed_and_parse(
                    {"feed_url": resolved_feed_url, "feed_type": detected_type, "id": feed_id})
                items = prepare_new_items(items)
                saved = batch_save_items(items) if items else 0
                self.send_json_response({"status": "success", "feed_id": feed_id, "new_items": saved})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/feeds/sync-single":
            feed_url = body.get("feed_url")
            if not feed_url:
                self.send_json_response({"status": "error", "message": "URL requerida"}, 400)
                return
            try:
                inserted = sync_feeds(force=True, feeds=[{
                    "feed_url": feed_url, "feed_type": body.get("feed_type", "rss"),
                    "id": body.get("feed_id")}])
                self.send_json_response({"status": "success", "new_items": inserted})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/feeds/sync":
            offset = int(body.get("offset", 0))
            limit = int(body.get("limit", 40))
            try:
                feeds = fetch_feeds(user_id)
                if not feeds:
                    ensure_seed_if_empty(user_id)
                    feeds = fetch_feeds(user_id)

                total_feeds = len(feeds)
                batch_slice = feeds[offset:offset + limit]
                inserted_count = sync_feeds(batch_slice)
                self.send_json_response({
                    "status": "success", "offset": offset, "batch_size": len(batch_slice),
                    "total_feeds": total_feeds, "new_items": inserted_count,
                    "has_more": (offset + limit) < total_feeds
                })
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/feeds/import-opml":
            opml_text = body.get("opml", "")
            if not opml_text:
                self.send_json_response({"status": "error", "message": "OPML vacío"}, 400)
                return
            try:
                feeds = parse_opml(opml_text)
                registered_feeds = []
                failed_feeds = []
                for f in feeds:
                    try:
                        fid = create_or_update_feed(
                            user_id, title=f["title"], feed_url=f["feed_url"],
                            feed_type=f["feed_type"], category=f["custom_category"],
                            site_url=f["site_url"])
                        registered_feeds.append({
                            "id": fid, "title": f["title"],
                            "feed_url": f["feed_url"], "feed_type": f["feed_type"]})
                    except Exception as e:
                        failed_feeds.append({"title": f.get("title"), "error": str(e)})
                        print(f"OPML import: fallo al registrar '{f.get('title')}': {e}")

                items_saved = 0
                try:
                    items_saved = sync_feeds(registered_feeds[:40])
                except Exception as e:
                    print(f"OPML import: fallo al guardar items iniciales: {e}")

                self.send_json_response({
                    "status": "success" if registered_feeds else "error",
                    "imported_count": len(registered_feeds),
                    "failed_count": len(failed_feeds),
                    "failed_feeds": failed_feeds[:10],
                    "initial_items_loaded": items_saved,
                    "message": (failed_feeds[0]["error"] if (not registered_feeds and failed_feeds) else None)
                })
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/profile":
            try:
                save_profile_data(user_id, body.get("focus_topics", []),
                                  body.get("system_prompt_criteria", ""))
                self.send_json_response({"status": "success"})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        self.send_response(404)
        self.end_headers()

    # ------------------------------------------------------
    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        match_feed = re.match(r"^/api/feeds/(\d+)$", path)
        if match_feed:
            try:
                unsubscribe(self.current_user(), int(match_feed.group(1)))
                self.send_json_response({"status": "success"})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return
        self.send_response(404)
        self.end_headers()


def run_server(port=PORT):
    init_db()
    with socketserver.ThreadingTCPServer(("", port), FeedCuratorHTTPHandler) as httpd:
        print(f"FocusFeed en http://localhost:{port} (Supabase: {is_supabase()})")
        httpd.serve_forever()


if __name__ == "__main__":
    run_server()
