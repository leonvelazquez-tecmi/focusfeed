import http.server
import socketserver
import json
import urllib.parse
import urllib.request
import os
import re
from datetime import datetime

from app.db import get_db_connection, init_db
from app.ingestion import (
    register_feed, parse_youtube_feed_xml, 
    parse_rss_feed_xml, parse_opml, save_items_to_db
)
from app.ai_engine import process_item_ai, process_all_pending_items, get_user_profile

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

        # Serve static files
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return

        # API: /api/items
        elif path == "/api/items":
            tab = query.get("tab", ["curated"])[0]
            type_filter = query.get("type", ["all"])[0]
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Counts
            cursor.execute("SELECT status, COUNT(*) as cnt FROM items GROUP BY status")
            counts_rows = cursor.fetchall()
            counts = {r["status"]: r["cnt"] for r in counts_rows}
            
            sql = "SELECT * FROM items WHERE 1=1"
            
            if tab == "curated":
                sql += " AND status != 'archived' ORDER BY relevance_score DESC, published_at DESC LIMIT 50"
            elif tab == "inbox":
                sql += " AND status = 'inbox' ORDER BY published_at DESC LIMIT 50"
            elif tab == "reading":
                sql += " AND status = 'reading' ORDER BY published_at DESC LIMIT 50"
            elif tab == "archived":
                sql += " AND status = 'archived' ORDER BY published_at DESC LIMIT 50"
                
            cursor.execute(sql)
            rows = cursor.fetchall()
            
            items = []
            for r in rows:
                item_dict = dict(r)
                if type_filter != "all" and item_dict.get("content_type") != type_filter:
                    continue
                items.append(item_dict)
                
            conn.close()
            self.send_json_response({"items": items, "counts": counts})
            return

        # API: /api/feeds
        elif path == "/api/feeds":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
            SELECT f.*, COUNT(i.id) as item_count 
            FROM feeds f 
            LEFT JOIN items i ON f.id = i.feed_id 
            GROUP BY f.id 
            ORDER BY f.created_at DESC
            """)
            feeds = [dict(r) for r in cursor.fetchall()]
            conn.close()
            self.send_json_response(feeds)
            return

        # API: /api/profile
        elif path == "/api/profile":
            profile = get_user_profile()
            self.send_json_response(profile)
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

        # API: /api/items/<id>/status
        match_status = re.match(r"^/api/items/(\d+)/status$", path)
        if match_status:
            item_id = int(match_status.group(1))
            new_status = body.get("status", "inbox")
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE items SET status = ? WHERE id = ?", (new_status, item_id))
            conn.commit()
            conn.close()
            self.send_json_response({"status": "success", "item_id": item_id, "new_status": new_status})
            return

        # API: /api/feeds
        if path == "/api/feeds":
            title = body.get("title", "")
            raw_url = body.get("url", "")
            feed_type = body.get("type", "youtube")
            
            feed_url = raw_url
            if feed_type == "youtube" and not raw_url.endswith(".xml"):
                if "channel/" in raw_url:
                    ch_id = raw_url.split("channel/")[1].split("/")[0].split("?")[0]
                    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
                elif "@" in raw_url:
                    # In real usage, YouTube feed for @handle can be resolved or provided
                    feed_url = raw_url
            
            feed_id = register_feed(
                title=title or raw_url,
                feed_url=feed_url,
                feed_type=feed_type,
                category="General",
                site_url=raw_url
            )
            self.send_json_response({"status": "success", "feed_id": feed_id})
            return

        # API: /api/feeds/sync
        if path == "/api/feeds/sync":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM feeds WHERE is_active = 1")
            feeds = [dict(r) for r in cursor.fetchall()]
            conn.close()
            
            new_items_count = 0
            for f in feeds:
                try:
                    req = urllib.request.Request(f["feed_url"], headers={'User-Agent': 'Mozilla/5.0 FocusFeed/1.0'})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        xml_content = resp.read().decode('utf-8', errors='ignore')
                        if f["feed_type"] == "youtube":
                            parsed_items = parse_youtube_feed_xml(xml_content, f["id"])
                        else:
                            parsed_items = parse_rss_feed_xml(xml_content, f["id"])
                        new_items_count += save_items_to_db(parsed_items)
                except Exception:
                    continue
                    
            process_all_pending_items(limit=30)
            self.send_json_response({"status": "success", "new_items": new_items_count})
            return

        # API: /api/feeds/import-opml
        if path == "/api/feeds/import-opml":
            opml_text = body.get("opml", "")
            if not opml_text:
                self.send_json_response({"error": "Empty OPML"}, 400)
                return
            feeds = parse_opml(opml_text)
            for f in feeds:
                register_feed(
                    title=f["title"],
                    feed_url=f["feed_url"],
                    feed_type=f["feed_type"],
                    category=f["custom_category"],
                    site_url=f["site_url"]
                )
            self.send_json_response({"status": "success", "imported_count": len(feeds)})
            return

        # API: /api/profile
        if path == "/api/profile":
            conn = get_db_connection()
            cursor = conn.cursor()
            topics_json = json.dumps(body.get("focus_topics", []), ensure_ascii=False)
            cursor.execute("""
            UPDATE user_profile SET
                focus_topics = ?,
                system_prompt_criteria = ?,
                updated_at = ?
            WHERE id = 1
            """, (
                topics_json,
                body.get("system_prompt_criteria", ""),
                datetime.utcnow().isoformat()
            ))
            conn.commit()
            conn.close()
            self.send_json_response({"status": "success"})
            return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        match_feed = re.match(r"^/api/feeds/(\d+)$", path)
        if match_feed:
            feed_id = int(match_feed.group(1))
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM items WHERE feed_id = ?", (feed_id,))
            cursor.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
            conn.commit()
            conn.close()
            self.send_json_response({"status": "success", "deleted_feed_id": feed_id})
            return
            
        self.send_response(404)
        self.end_headers()

    def seed_sample_dataset(self):
        """
        Seeds high-signal realistic feeds and enriched items aligned with user focus.
        """
        f1 = register_feed("Andrej Karpathy", "https://www.youtube.com/feeds/videos.xml?channel_id=UCXUPKJOtpqmgUOxw8p9n6Tw", "youtube", "IA & Agentes")
        f2 = register_feed("Tiago Forte", "https://www.youtube.com/feeds/videos.xml?channel_id=UCBw92y4tWvjB0U_N9l3l7-w", "youtube", "PKM & Obsidian")
        f3 = register_feed("MIT Technology Review", "https://www.technologyreview.com/feed/", "rss", "Educación")
        f4 = register_feed("StudioBinder", "https://www.youtube.com/feeds/videos.xml?channel_id=UCQ4v9aB3X59bF3Jb7M6T-aA", "youtube", "Cine")

        sample_items = [
            {
                "feed_id": f1,
                "guid": "yt:sample_karpathy_agents",
                "title": "Arquitecturas Agénticas y Memoria Contextual en Modelos de Lenguaje",
                "url": "https://www.youtube.com/watch?v=kCc8FmEb1nY",
                "author": "Andrej Karpathy",
                "published_at": "2026-08-15T18:00:00Z",
                "content_type": "video",
                "video_id": "kCc8FmEb1nY",
                "thumbnail_url": "https://i.ytimg.com/vi/kCc8FmEb1nY/hqdefault.jpg",
                "raw_content": "Deep dive into building reliable cognitive agents, tool calling loops, and episodic memory persistence in structured formats.",
                "transcript": "In this session, we dissect the architecture of autonomous agents. We analyze why naive prompting fails, how memory persistence in structured Markdown and vector stores stabilizes reasoning loops, and why model context size does not eliminate the need for hierarchical retrieval.",
                "relevance_score": 98,
                "summary_tldr": "Una exploración técnica sobre los principios fundamentales para construir agentes cognitivos robustos.\n\nKarpathy explica cómo estructurar bucles iterativos de toma de decisiones, uso de herramientas externas y persistencia de memoria para evitar degradación en tareas complejas.",
                "key_takeaways": json.dumps([
                    "Los bucles agénticos autónomos requieren memoria episódica estructurada para no perder el contexto.",
                    "Ventanas de contexto grandes complementan pero no sustituyen el retrieval jerárquico.",
                    "La modularidad en herramientas y esquemas de validación previa previene errores de ejecución."
                ], ensure_ascii=False),
                "curator_note": "Alineación directa con investigación de sistemas agénticos y automatización.",
                "topic_tags": json.dumps(["Sistemas-Agénticos", "IA-Aplicada"], ensure_ascii=False),
                "ai_processed": 1,
                "status": "inbox"
            },
            {
                "feed_id": f2,
                "guid": "yt:sample_tiago_obsidian",
                "title": "Zettelkasten vs. Building a Second Brain: Flujos de Síntesis",
                "url": "https://www.youtube.com/watch?v=r0X9qL_W1e4",
                "author": "Tiago Forte",
                "published_at": "2026-08-14T14:30:00Z",
                "content_type": "video",
                "video_id": "r0X9qL_W1e4",
                "thumbnail_url": "https://i.ytimg.com/vi/r0X9qL_W1e4/hqdefault.jpg",
                "raw_content": "Comparative study between progressive summarization in modern PKM vaults and atomic Zettelkasten card indexing.",
                "transcript": "Managing your knowledge in Obsidian requires distinguishing capture friction from synthesis value. We explore how atomic notes and project-oriented folders coexist harmoniously.",
                "relevance_score": 94,
                "summary_tldr": "Análisis comparativo sobre cómo balancear la toma de notas atómicas con estructuras orientadas a proyectos accionables.\n\nForte comparte pautas para evitar la fatiga por sobre-captura de información y centrarse en la destilación progresiva de ideas.",
                "key_takeaways": json.dumps([
                    "El valor de una nota radica en la compresión progresiva de sus ideas clave, no en guardarla pasivamente.",
                    "Priorizar enlaces bidireccionales basados en problemas o proyectos concretos."
                ], ensure_ascii=False),
                "curator_note": "Relevante para tu gestión del conocimiento personal (PKM).",
                "topic_tags": json.dumps(["PKM", "Zettelkasten"], ensure_ascii=False),
                "ai_processed": 1,
                "status": "inbox"
            },
            {
                "feed_id": f3,
                "guid": "rss:sample_mit_education",
                "title": "Modelos Educativos Asíncronos y Micro-Credenciales Apilables para 2030",
                "url": "https://www.technologyreview.com",
                "author": "MIT Technology Review",
                "published_at": "2026-08-12T10:00:00Z",
                "content_type": "article",
                "video_id": None,
                "thumbnail_url": "",
                "raw_content": "Higher education institutions are pivoting toward modular, stackable competency frameworks integrated with continuous AI coaching.",
                "transcript": "Higher education institutions are accelerating the transformation of traditional degrees into modular, stackable micro-credentials.",
                "relevance_score": 91,
                "summary_tldr": "Reporte estratégico sobre la reconfiguración de la educación superior hacia trayectorias modulares y credenciales apilables.\n\nSe analiza la integración de acompañamiento vocacional adaptativo para elevar la retención y pertinencia formativa.",
                "key_takeaways": json.dumps([
                    "Desarticulación de planes rígidos en módulos independientes con valor curricular propio.",
                    "Evaluación por competencias prácticas asistida por analítica de datos."
                ], ensure_ascii=False),
                "curator_note": "Alineación directa con planeación institucional y modelos educativos innovadores.",
                "topic_tags": json.dumps(["Estrategia-Educativa", "Transformación"], ensure_ascii=False),
                "ai_processed": 1,
                "status": "inbox"
            },
            {
                "feed_id": f4,
                "guid": "yt:sample_film_nolan",
                "title": "El Formato 1.43:1 y la Composición Espacial en el Cine Contemporáneo",
                "url": "https://www.youtube.com/watch?v=s9f9K_O1b7c",
                "author": "StudioBinder",
                "published_at": "2026-08-11T16:00:00Z",
                "content_type": "video",
                "video_id": "s9f9K_O1b7c",
                "thumbnail_url": "https://i.ytimg.com/vi/s9f9K_O1b7c/hqdefault.jpg",
                "raw_content": "Visual essay exploring how changing aspect ratios and large format film create spatial immersion in modern cinema.",
                "transcript": "Aspect ratios in modern cinema are not merely technical choices; they dictate the psychological relationship between the spectator and the physical landscape.",
                "relevance_score": 86,
                "summary_tldr": "Ensayo visual sobre el impacto del aspect ratio vertical 1.43:1 en directores como Christopher Nolan y Yorgos Lanthimos, y cómo la arquitectura visual genera tensión escénica.",
                "key_takeaways": json.dumps([
                    "El formato 1.43:1 aprovecha la verticalidad para intensificar la relación del personaje con el entorno.",
                    "Uso del espacio negativo para generar tensión dramática."
                ], ensure_ascii=False),
                "curator_note": "Afinidad con tus temas de cine de autor y narrativa visual.",
                "topic_tags": json.dumps(["Cine-Narrativa", "Aspect-Ratio"], ensure_ascii=False),
                "ai_processed": 1,
                "status": "inbox"
            }
        ]

        conn = get_db_connection()
        cursor = conn.cursor()
        for item in sample_items:
            cursor.execute("""
            INSERT OR REPLACE INTO items (
                feed_id, guid, title, url, author, published_at,
                content_type, video_id, thumbnail_url, raw_content, transcript,
                relevance_score, summary_tldr, key_takeaways, curator_note, topic_tags,
                ai_processed, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["feed_id"], item["guid"], item["title"], item["url"], item["author"], item["published_at"],
                item["content_type"], item["video_id"], item["thumbnail_url"], item["raw_content"], item["transcript"],
                item["relevance_score"], item["summary_tldr"], item["key_takeaways"], item["curator_note"], item["topic_tags"],
                item["ai_processed"], item["status"]
            ))
        conn.commit()
        conn.close()

def run_server(port=PORT):
    init_db()
    handler = FeedCuratorHTTPHandler
    handler.seed_sample_dataset(handler)
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"FocusFeed running on http://localhost:{port}")
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()
