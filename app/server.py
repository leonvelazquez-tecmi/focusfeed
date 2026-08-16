import http.server
import socketserver
import json
import urllib.parse
import urllib.request
import os
import re
import concurrent.futures
from datetime import datetime

from app.db import init_db, is_supabase
from app.storage import (
    fetch_items, set_item_status, save_user_feedback,
    fetch_feeds, create_or_update_feed, remove_feed,
    fetch_profile, save_profile_data, batch_save_items,
    ensure_seed_if_empty
)
from app.ingestion import (
    parse_youtube_feed_xml, parse_rss_feed_xml, 
    parse_opml, resolve_feed_url, HEADERS
)
from app.ai_engine import (
    process_item_ai, process_all_pending_items, 
    record_feedback, analyze_with_llm, get_user_profile
)

PORT = int(os.environ.get("PORT", 8080))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

socketserver.TCPServer.allow_reuse_address = True

def fetch_feed_and_parse(feed_dict: dict, profile: dict) -> list:
    """
    Fetches an individual RSS/YouTube feed with browser headers and pre-enriches items.
    """
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
                
        # Pre-enrich top 3 items per feed with AI score and summary
        for itm in parsed_items[:3]:
            try:
                analysis = analyze_with_llm(
                    title=itm["title"],
                    author=itm["author"],
                    content_type=itm["content_type"],
                    content_text=itm["raw_content"],
                    profile=profile
                )
                itm["relevance_score"] = analysis["relevance_score"]
                itm["summary_tldr"] = analysis["summary_tldr"]
                itm["key_takeaways"] = json.dumps(analysis["key_takeaways"], ensure_ascii=False)
                itm["curator_note"] = analysis["curator_note"]
                itm["topic_tags"] = json.dumps(analysis["topic_tags"], ensure_ascii=False)
            except Exception:
                itm["relevance_score"] = 75
    except Exception:
        pass
        
    return parsed_items

class FeedCuratorHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def send_json_response(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # Static assets
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return

        elif path == "/manifest.json":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            with open(os.path.join(STATIC_DIR, "manifest.json"), "rb") as f:
                self.wfile.write(f.read())
            return

        elif path == "/api/status":
            self.send_json_response({
                "status": "online",
                "is_supabase": is_supabase(),
                "supabase_url_configured": bool(os.environ.get("SUPABASE_URL")),
                "supabase_key_configured": bool(os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY"))
            })
            return

        elif path == "/api/items":
            tab = query.get("tab", ["curated"])[0]
            type_filter = query.get("type", ["all"])[0]
            try:
                data = fetch_items(tab=tab, type_filter=type_filter)
                self.send_json_response(data)
            except Exception as e:
                self.send_json_response({"items": [], "counts": {}, "error": str(e)}, 200)
            return

        elif path == "/api/feeds":
            try:
                feeds = fetch_feeds()
                self.send_json_response(feeds)
            except Exception as e:
                self.send_json_response([], 200)
            return

        elif path == "/api/profile":
            try:
                profile = fetch_profile()
                self.send_json_response(profile)
            except Exception as e:
                self.send_json_response({}, 200)
            return

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        content_length = int(self.headers.get('Content-Length', 0))
        body_str = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else "{}"
        try:
            body = json.loads(body_str) if body_str else {}
        except Exception:
            body = {}

        # Feedback
        match_feedback = re.match(r"^/api/items/(\d+)/feedback$", path)
        if match_feedback:
            item_id = int(match_feedback.group(1))
            rating = body.get("rating", "like")
            comment = body.get("comment", "")
            try:
                record_feedback(item_id, rating, comment)
                self.send_json_response({"status": "success", "item_id": item_id, "rating": rating})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # Status
        match_status = re.match(r"^/api/items/(\d+)/status$", path)
        if match_status:
            item_id = int(match_status.group(1))
            new_status = body.get("status", "inbox")
            try:
                set_item_status(item_id, new_status)
                self.send_json_response({"status": "success", "item_id": item_id, "new_status": new_status})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # Add Feed
        if path == "/api/feeds":
            raw_url = body.get("url", "").strip()
            title = body.get("title", "").strip()
            if not raw_url:
                self.send_json_response({"status": "error", "message": "URL requerida"}, 400)
                return

            try:
                resolved_feed_url, detected_type, suggested_title = resolve_feed_url(raw_url)
                final_title = title or suggested_title or raw_url
                
                feed_id = create_or_update_feed(
                    title=final_title,
                    feed_url=resolved_feed_url,
                    feed_type=detected_type,
                    category="General",
                    site_url=raw_url
                )
                
                # Fetch items
                profile = fetch_profile()
                items = fetch_feed_and_parse({"feed_url": resolved_feed_url, "feed_type": detected_type, "id": feed_id}, profile)
                if items:
                    batch_save_items(items)
                    
                self.send_json_response({"status": "success", "feed_id": feed_id, "new_items": len(items)})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # Sync Feeds in Parallel
        if path == "/api/feeds/sync":
            try:
                feeds = fetch_feeds()
                if not feeds:
                    ensure_seed_if_empty()
                    feeds = fetch_feeds()
                    
                profile = fetch_profile()
                all_new_items = []
                
                # Fetch up to 12 feeds in parallel with ThreadPoolExecutor
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [executor.submit(fetch_feed_and_parse, f, profile) for f in feeds[:12]]
                    for fut in concurrent.futures.as_completed(futures, timeout=7):
                        try:
                            items = fut.result()
                            if items:
                                all_new_items.extend(items)
                        except Exception:
                            continue
                            
                inserted_count = batch_save_items(all_new_items)
                
                self.send_json_response({
                    "status": "success", 
                    "new_items": len(all_new_items),
                    "inserted_count": inserted_count,
                    "is_supabase": is_supabase()
                })
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # Import OPML
        if path == "/api/feeds/import-opml":
            opml_text = body.get("opml", "")
            if not opml_text:
                self.send_json_response({"status": "error", "message": "OPML vacío"}, 400)
                return
            try:
                feeds = parse_opml(opml_text)
                
                # Register feeds
                registered_feeds = []
                for f in feeds:
                    fid = create_or_update_feed(
                        title=f["title"],
                        feed_url=f["feed_url"],
                        feed_type=f["feed_type"],
                        category=f["custom_category"],
                        site_url=f["site_url"]
                    )
                    registered_feeds.append({
                        "id": fid,
                        "title": f["title"],
                        "feed_url": f["feed_url"],
                        "feed_type": f["feed_type"]
                    })
                    
                # Fetch initial batch of feeds in parallel
                profile = fetch_profile()
                all_imported_items = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                    futures = [executor.submit(fetch_feed_and_parse, f, profile) for f in registered_feeds[:10]]
                    for fut in concurrent.futures.as_completed(futures, timeout=7):
                        try:
                            res_items = fut.result()
                            if res_items:
                                all_imported_items.extend(res_items)
                        except Exception:
                            continue
                            
                if all_imported_items:
                    batch_save_items(all_imported_items)
                    
                self.send_json_response({
                    "status": "success", 
                    "imported_count": len(feeds),
                    "initial_items_loaded": len(all_imported_items)
                })
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # Profile
        if path == "/api/profile":
            topics = body.get("focus_topics", [])
            criteria = body.get("system_prompt_criteria", "")
            try:
                save_profile_data(topics, criteria)
                self.send_json_response({"status": "success"})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        match_feed = re.match(r"^/api/feeds/(\d+)$", path)
        if match_feed:
            feed_id = int(match_feed.group(1))
            try:
                remove_feed(feed_id)
                self.send_json_response({"status": "success", "deleted_feed_id": feed_id})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return
            
        self.send_response(404)
        self.end_headers()

def run_server(port=PORT):
    init_db()
    handler = FeedCuratorHTTPHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"FocusFeed running on http://localhost:{port} (Supabase: {is_supabase()})")
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()
