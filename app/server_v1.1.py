import http.server
import socketserver
import json
import urllib.parse
import urllib.request
import os
import re
import time
import concurrent.futures

from app.db import init_db, is_supabase
from app.auth import resolve_user
from app.storage import (
    fetch_items, set_item_status, bulk_set_status,
    fetch_feeds, fetch_all_active_feeds, create_or_update_feed, unsubscribe,
    fetch_profile, save_profile_data, batch_save_items,
    ensure_seed_if_empty, get_pending_item_ids, usage_summary
)
from app.ingestion import (
    parse_youtube_feed_xml, parse_rss_feed_xml,
    parse_opml, resolve_feed_url, HEADERS
)
from app.ai_engine import (
    process_item_ai, record_feedback, analyze_content, score_pending_for_user,
    RATING_TO_STATUS
)

PORT = int(os.environ.get("PORT", 8080))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

socketserver.TCPServer.allow_reuse_address = True


def fetch_feed_and_parse(feed_dict: dict, enrich_top: int = 4) -> list:
    """Descarga un feed y devuelve sus items. El enriquecimiento es universal:
    no depende de quién esté suscrito."""
    feed_url = feed_dict.get("feed_url")
    feed_type = feed_dict.get("feed_type", "rss")
    feed_id = feed_dict.get("id", 1)

    parsed_items = []
    try:
        req = urllib.request.Request(feed_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            xml_content = resp.read().decode('utf-8', errors='ignore')
            if feed_type == "youtube":
                parsed_items = parse_youtube_feed_xml(xml_content, feed_id)
            else:
                parsed_items = parse_rss_feed_xml(xml_content, feed_id)

        for itm in parsed_items[:enrich_top]:
            try:
                analysis = analyze_content(
                    title=itm["title"], author=itm["author"],
                    content_type=itm["content_type"], content_text=itm["raw_content"])
                itm["summary_tldr"] = analysis["summary_tldr"]
                itm["key_takeaways"] = json.dumps(analysis["key_takeaways"], ensure_ascii=False)
                itm["topic_tags"] = json.dumps(analysis["topic_tags"], ensure_ascii=False)
            except Exception as e:
                print(f"Enriquecimiento fallo para '{itm.get('title')}': {e}")
    except Exception as e:
        print(f"Error obteniendo/parseando feed {feed_url}: {e}")

    return parsed_items


class FeedCuratorHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def send_json_response(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
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

        if path == "/api/status":
            self.send_json_response({
                "status": "online",
                "is_supabase": is_supabase(),
                "user_id": self.current_user(),
                "supabase_url_configured": bool(os.environ.get("SUPABASE_URL")),
                "supabase_key_configured": bool(os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY")),
                "ai_configured": bool(os.environ.get("GEMINI_API_KEY"))
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
            time_budget_seconds = 50
            batch_size = 15

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
                    all_new_items = []
                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                        futures = [executor.submit(fetch_feed_and_parse, f) for f in batch_slice]
                        for fut in concurrent.futures.as_completed(futures, timeout=7.5):
                            try:
                                items = fut.result()
                                if items:
                                    all_new_items.extend(items)
                            except Exception as e:
                                print(f"Cron sync: fallo al sincronizar feed: {e}")
                                continue
                    if all_new_items:
                        total_new_items += batch_save_items(all_new_items)
                    feeds_synced += len(batch_slice)
                    offset += batch_size

                self.send_json_response({
                    "status": "success", "feeds_synced": feeds_synced,
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
                self.send_json_response({"items": [], "counts": {}, "error": str(e)}, 200)
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
                processed = 0
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [executor.submit(process_item_ai, iid, user_id) for iid in pending_ids]
                    for fut in concurrent.futures.as_completed(futures, timeout=20):
                        try:
                            if fut.result():
                                processed += 1
                        except Exception as e:
                            print(f"process-pending: fallo al analizar item: {e}")
                            continue
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
                items = fetch_feed_and_parse({
                    "feed_url": feed_url, "feed_type": body.get("feed_type", "rss"),
                    "id": body.get("feed_id")})
                inserted = batch_save_items(items) if items else 0
                self.send_json_response({"status": "success", "new_items": inserted})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        if path == "/api/feeds/sync":
            offset = int(body.get("offset", 0))
            limit = int(body.get("limit", 15))
            try:
                feeds = fetch_feeds(user_id)
                if not feeds:
                    ensure_seed_if_empty(user_id)
                    feeds = fetch_feeds(user_id)

                total_feeds = len(feeds)
                batch_slice = feeds[offset:offset + limit]
                all_new_items = []

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(fetch_feed_and_parse, f) for f in batch_slice]
                    for fut in concurrent.futures.as_completed(futures, timeout=7.5):
                        try:
                            items = fut.result()
                            if items:
                                all_new_items.extend(items)
                        except Exception as e:
                            print(f"Sync de feed falló: {e}")
                            continue

                inserted_count = batch_save_items(all_new_items) if all_new_items else 0
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

                initial_items = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(fetch_feed_and_parse, f) for f in registered_feeds[:15]]
                    for fut in concurrent.futures.as_completed(futures, timeout=7.5):
                        try:
                            res_items = fut.result()
                            if res_items:
                                initial_items.extend(res_items)
                        except Exception as e:
                            print(f"OPML import: fallo al descargar items: {e}")
                            continue

                items_saved = 0
                if initial_items:
                    try:
                        items_saved = batch_save_items(initial_items)
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
    with socketserver.TCPServer(("", port), FeedCuratorHTTPHandler) as httpd:
        print(f"FocusFeed en http://localhost:{port} (Supabase: {is_supabase()})")
        httpd.serve_forever()


if __name__ == "__main__":
    run_server()
