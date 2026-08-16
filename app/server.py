import http.server
import socketserver
import json
import urllib.parse
import urllib.request
import os
import re
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
    parse_opml, resolve_feed_url
)
from app.ai_engine import (
    process_item_ai, process_all_pending_items, 
    record_feedback, analyze_with_llm, get_user_profile
)

PORT = int(os.environ.get("PORT", 8080))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

socketserver.TCPServer.allow_reuse_address = True

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

        # Diagnostics & Status
        elif path == "/api/status":
            self.send_json_response({
                "status": "online",
                "is_supabase": is_supabase(),
                "supabase_url_configured": bool(os.environ.get("SUPABASE_URL")),
                "supabase_key_configured": bool(os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY"))
            })
            return

        # API: /api/items
        elif path == "/api/items":
            tab = query.get("tab", ["curated"])[0]
            type_filter = query.get("type", ["all"])[0]
            try:
                data = fetch_items(tab=tab, type_filter=type_filter)
                self.send_json_response(data)
            except Exception as e:
                self.send_json_response({"items": [], "counts": {}, "error": str(e)}, 200)
            return

        # API: /api/feeds
        elif path == "/api/feeds":
            try:
                feeds = fetch_feeds()
                self.send_json_response(feeds)
            except Exception as e:
                self.send_json_response([], 200)
            return

        # API: /api/profile
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

        # API: /api/items/<id>/feedback
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

        # API: /api/items/<id>/status
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

        # API: /api/feeds
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
                
                # Fetch items immediately
                parsed_items = []
                try:
                    req = urllib.request.Request(resolved_feed_url, headers={'User-Agent': 'Mozilla/5.0 FocusFeed/1.0'})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        xml_content = resp.read().decode('utf-8', errors='ignore')
                        if detected_type == "youtube":
                            parsed_items = parse_youtube_feed_xml(xml_content, feed_id)
                        else:
                            parsed_items = parse_rss_feed_xml(xml_content, feed_id)
                            
                        # Enrich items with AI score before saving
                        profile = fetch_profile()
                        for itm in parsed_items[:6]:
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
                            
                        batch_save_items(parsed_items)
                except Exception:
                    pass
                    
                self.send_json_response({"status": "success", "feed_id": feed_id, "new_items": len(parsed_items)})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # API: /api/feeds/sync
        if path == "/api/feeds/sync":
            try:
                feeds = fetch_feeds()
                
                # If no feeds yet, seed defaults so user sees immediate results
                if not feeds:
                    ensure_seed_if_empty()
                    feeds = fetch_feeds()
                    
                new_items_count = 0
                profile = fetch_profile()
                
                # Sync up to 6 feeds per request to stay safely within Vercel's 10s timeout
                for f in feeds[:6]:
                    try:
                        req = urllib.request.Request(f["feed_url"], headers={'User-Agent': 'Mozilla/5.0 FocusFeed/1.0'})
                        with urllib.request.urlopen(req, timeout=4) as resp:
                            xml_content = resp.read().decode('utf-8', errors='ignore')
                            if f.get("feed_type") == "youtube":
                                parsed_items = parse_youtube_feed_xml(xml_content, f["id"])
                            else:
                                parsed_items = parse_rss_feed_xml(xml_content, f["id"])
                                
                            # Pre-enrich top 3 items of each feed
                            for itm in parsed_items[:3]:
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

                            new_items_count += batch_save_items(parsed_items)
                    except Exception:
                        continue
                        
                self.send_json_response({
                    "status": "success", 
                    "new_items": new_items_count,
                    "is_supabase": is_supabase()
                })
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # API: /api/feeds/import-opml
        if path == "/api/feeds/import-opml":
            opml_text = body.get("opml", "")
            if not opml_text:
                self.send_json_response({"status": "error", "message": "OPML vacío"}, 400)
                return
            try:
                feeds = parse_opml(opml_text)
                for f in feeds:
                    create_or_update_feed(
                        title=f["title"],
                        feed_url=f["feed_url"],
                        feed_type=f["feed_type"],
                        category=f["custom_category"],
                        site_url=f["site_url"]
                    )
                self.send_json_response({"status": "success", "imported_count": len(feeds)})
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, 500)
            return

        # API: /api/profile
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
