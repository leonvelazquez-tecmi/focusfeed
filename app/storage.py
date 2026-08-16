import json
import os
import urllib.request
import urllib.parse
from datetime import datetime
from app.db import (
    is_supabase, supabase_request, 
    get_db_connection, init_db
)

# ==================== DATA ACCESS LAYER ====================

def fetch_items(tab="curated", type_filter="all", limit=60):
    if is_supabase():
        query = "items?select=*"
        if tab == "curated":
            query += "&status=neq.archived&order=relevance_score.desc,published_at.desc"
        elif tab == "reading":
            query += "&status=eq.reading&order=published_at.desc"
        elif tab == "inbox":
            query += "&status=eq.inbox&order=published_at.desc"
        elif tab == "archived":
            query += "&status=eq.archived&order=published_at.desc"
            
        if type_filter != "all":
            query += f"&content_type=eq.{type_filter}"
            
        query += f"&limit={limit}"
        items = supabase_request(query)
        if not isinstance(items, list):
            items = []
        
        # Calculate counts
        all_status = supabase_request("items?select=status")
        counts = {}
        if isinstance(all_status, list):
            for r in all_status:
                s = r.get("status", "inbox")
                counts[s] = counts.get(s, 0) + 1
            
        return {"items": items, "counts": counts}
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status, COUNT(*) as cnt FROM items GROUP BY status")
        counts = {r["status"]: r["cnt"] for r in cursor.fetchall()}
        
        sql = "SELECT * FROM items WHERE 1=1"
        if tab == "curated":
            sql += " AND status != 'archived' ORDER BY (CASE WHEN user_rating = 'love' THEN 100 ELSE relevance_score END) DESC, published_at DESC LIMIT ?"
        elif tab == "inbox":
            sql += " AND status = 'inbox' ORDER BY published_at DESC LIMIT ?"
        elif tab == "reading":
            sql += " AND status = 'reading' ORDER BY published_at DESC LIMIT ?"
        elif tab == "archived":
            sql += " AND status = 'archived' ORDER BY published_at DESC LIMIT ?"
            
        cursor.execute(sql, (limit,))
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        
        if type_filter != "all":
            rows = [r for r in rows if r.get("content_type") == type_filter]
            
        return {"items": rows, "counts": counts}

def set_item_status(item_id: int, new_status: str):
    if is_supabase():
        supabase_request(f"items?id=eq.{item_id}", method="PATCH", data={"status": new_status})
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE items SET status = ? WHERE id = ?", (new_status, item_id))
        conn.commit()
        conn.close()

def save_user_feedback(item_id: int, rating: str, comment: str = ""):
    now_iso = datetime.utcnow().isoformat()
    if is_supabase():
        supabase_request(f"items?id=eq.{item_id}", method="PATCH", data={
            "user_rating": rating,
            "user_feedback_comment": comment,
            "feedback_at": now_iso
        })
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE items SET user_rating = ?, user_feedback_comment = ?, feedback_at = ? WHERE id = ?
        """, (rating, comment, now_iso, item_id))
        conn.commit()
        conn.close()

def fetch_feeds():
    if is_supabase():
        feeds = supabase_request("feeds?select=*&order=created_at.desc")
        if not isinstance(feeds, list):
            feeds = []
        for f in feeds:
            f["item_count"] = 0
        return feeds
    else:
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
        return feeds

def create_or_update_feed(title: str, feed_url: str, feed_type: str = "youtube", category: str = "General", site_url: str = ""):
    if is_supabase():
        data = {
            "title": title,
            "feed_url": feed_url,
            "site_url": site_url,
            "feed_type": feed_type,
            "custom_category": category,
            "is_active": True
        }
        res = supabase_request("feeds?on_conflict=feed_url", method="POST", data=data, headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})
        return res[0]["id"] if (res and isinstance(res, list) and len(res) > 0) else 1
    else:
        from app.ingestion import register_feed
        return register_feed(title, feed_url, feed_type, category, site_url)

def remove_feed(feed_id: int):
    if is_supabase():
        supabase_request(f"items?feed_id=eq.{feed_id}", method="DELETE")
        supabase_request(f"feeds?id=eq.{feed_id}", method="DELETE")
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM items WHERE feed_id = ?", (feed_id,))
        cursor.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        conn.commit()
        conn.close()

def fetch_profile():
    default_topics = [
        "Inteligencia Artificial Aplicada y Sistemas Agénticos",
        "Gestión del Conocimiento Personal (PKM, Obsidian, Zettelkasten)",
        "Transformación Institucional y Modelos Educativos (MAPS, FIT)",
        "Cine de autor, narrativa visual y formato gran escala",
        "Música contemporánea, post-punk, vinilos y análisis cultural",
        "Enología y exploración gastronómica"
    ]
    if is_supabase():
        res = supabase_request("user_profile?select=*&limit=1")
        if res and isinstance(res, list) and len(res) > 0:
            p = res[0]
            try:
                topics = json.loads(p.get("focus_topics")) if isinstance(p.get("focus_topics"), str) else p.get("focus_topics")
            except Exception:
                topics = default_topics
            try:
                learned = json.loads(p.get("learned_preferences")) if isinstance(p.get("learned_preferences"), str) else p.get("learned_preferences")
            except Exception:
                learned = {}
            return {
                "focus_topics": topics or default_topics,
                "system_prompt_criteria": p.get("system_prompt_criteria", ""),
                "learned_preferences": learned or {"boosted_authors": [], "boosted_tags": {}, "penalized_tags": {}}
            }
        return {"focus_topics": default_topics, "system_prompt_criteria": "", "learned_preferences": {}}
    else:
        from app.ai_engine import get_user_profile
        return get_user_profile()

def save_profile_data(focus_topics: list, criteria: str = ""):
    topics_json = json.dumps(focus_topics, ensure_ascii=False)
    if is_supabase():
        data = {
            "focus_topics": topics_json,
            "system_prompt_criteria": criteria,
            "updated_at": datetime.utcnow().isoformat()
        }
        supabase_request("user_profile?id=eq.1", method="PATCH", data=data)
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE user_profile SET focus_topics = ?, system_prompt_criteria = ? WHERE id = 1", (topics_json, criteria))
        conn.commit()
        conn.close()

def batch_save_items(items: list):
    if not items:
        return 0
    if is_supabase():
        clean_batch = []
        for item in items:
            clean_batch.append({
                "feed_id": item.get("feed_id", 1),
                "guid": item["guid"],
                "title": item["title"],
                "url": item["url"],
                "author": item.get("author", "Autor"),
                "published_at": item.get("published_at", datetime.utcnow().isoformat()),
                "content_type": item.get("content_type", "article"),
                "video_id": item.get("video_id"),
                "thumbnail_url": item.get("thumbnail_url", ""),
                "raw_content": (item.get("raw_content") or "")[:2000],
                "transcript": (item.get("transcript") or "")[:2000],
                "relevance_score": item.get("relevance_score", 70),
                "summary_tldr": item.get("summary_tldr", ""),
                "key_takeaways": item.get("key_takeaways", "[]"),
                "curator_note": item.get("curator_note", ""),
                "topic_tags": item.get("topic_tags", "[]"),
                "status": "inbox"
            })
        
        # Batch insert with PostgREST on_conflict=guid upsert
        res = supabase_request("items?on_conflict=guid", method="POST", data=clean_batch, headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})
        if res and isinstance(res, list):
            return len(res)
        return len(clean_batch)
    else:
        from app.ingestion import save_items_to_db
        return save_items_to_db(items)

def ensure_seed_if_empty():
    """Seeds initial feeds if empty."""
    feeds = fetch_feeds()
    if not feeds:
        create_or_update_feed("Andrej Karpathy", "https://www.youtube.com/feeds/videos.xml?channel_id=UCXUPKJOtpqmgUOxw8p9n6Tw", "youtube", "IA & Agentes")
        create_or_update_feed("Tiago Forte", "https://www.youtube.com/feeds/videos.xml?channel_id=UCBw92y4tWvjB0U_N9l3l7-w", "youtube", "PKM & Obsidian")
        create_or_update_feed("New York Journal of Philosophy", "https://journalofphilosophy.substack.com/feed", "substack", "Filosofía")
        create_or_update_feed("StudioBinder", "https://www.youtube.com/feeds/videos.xml?channel_id=UCQ4v9aB3X59bF3Jb7M6T-aA", "youtube", "Cine")
