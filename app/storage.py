import json
import os
import urllib.request
import urllib.parse
from datetime import datetime
from app.db import (
    is_supabase, supabase_request, 
    get_db_connection, init_db, get_supabase_debug_info
)

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
        res = supabase_request(query)
        items = res if isinstance(res, list) else []
        
        all_status = supabase_request("items?select=status")
        counts = {}
        if isinstance(all_status, list):
            for r in all_status:
                s = r.get("status", "inbox")
                counts[s] = counts.get(s, 0) + 1
            
        return {"items": items, "counts": counts, "debug": get_supabase_debug_info()}
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
            
        return {"items": rows, "counts": counts, "debug": get_supabase_debug_info()}

def set_item_status(item_id: int, new_status: str):
    if is_supabase():
        return supabase_request(f"items?id=eq.{item_id}", method="PATCH", data={"status": new_status})
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE items SET status = ? WHERE id = ?", (new_status, item_id))
        conn.commit()
        conn.close()

def save_user_feedback(item_id: int, rating: str, comment: str = ""):
    now_iso = datetime.utcnow().isoformat()
    if is_supabase():
        return supabase_request(f"items?id=eq.{item_id}", method="PATCH", data={
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

def get_item_by_id(item_id: int):
    if is_supabase():
        res = supabase_request(f"items?id=eq.{item_id}&select=*&limit=1")
        print(f"DEBUG get_item_by_id({item_id}) raw res type={type(res)} value={res!r:.500}")
        if isinstance(res, list):
            return res[0] if res else None
        err_msg = res.get("message") if isinstance(res, dict) else str(res)
        print(f"get_item_by_id({item_id}): Supabase rechazó la consulta: {err_msg}")
        return None
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

def update_learned_preferences(learned: dict):
    now_iso = datetime.utcnow().isoformat()
    learned_json = json.dumps(learned, ensure_ascii=False)
    if is_supabase():
        return supabase_request("user_profile?id=eq.1", method="PATCH", data={
            "learned_preferences": learned_json,
            "updated_at": now_iso
        })
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE user_profile SET learned_preferences = ?, updated_at = ? WHERE id = 1
        """, (learned_json, now_iso))
        conn.commit()
        conn.close()

def get_pending_item_ids(limit: int = 10):
    if is_supabase():
        res = supabase_request(f"items?select=id&ai_processed=eq.0&order=published_at.desc&limit={limit}")
        return [r["id"] for r in res] if isinstance(res, list) else []
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM items WHERE ai_processed = 0 ORDER BY published_at DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [r["id"] for r in rows]

def save_item_analysis(item_id: int, analysis: dict):
    now_iso = datetime.utcnow().isoformat()
    data = {
        "relevance_score": analysis["relevance_score"],
        "summary_tldr": analysis["summary_tldr"],
        "key_takeaways": json.dumps(analysis["key_takeaways"], ensure_ascii=False),
        "curator_note": analysis["curator_note"],
        "topic_tags": json.dumps(analysis["topic_tags"], ensure_ascii=False),
        "ai_processed_at": now_iso
    }
    if is_supabase():
        data["ai_processed"] = True
        return supabase_request(f"items?id=eq.{item_id}", method="PATCH", data=data)
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE items SET
            relevance_score = ?, summary_tldr = ?, key_takeaways = ?,
            curator_note = ?, topic_tags = ?, ai_processed = 1, ai_processed_at = ?
        WHERE id = ?
        """, (
            data["relevance_score"], data["summary_tldr"], data["key_takeaways"],
            data["curator_note"], data["topic_tags"], data["ai_processed_at"], item_id
        ))
        conn.commit()
        conn.close()

def fetch_feeds():
    if is_supabase():
        res = supabase_request("feeds?select=*&order=custom_category.asc,title.asc")
        feeds = res if isinstance(res, list) else []
        return feeds
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT f.*, COUNT(i.id) as item_count 
        FROM feeds f 
        LEFT JOIN items i ON f.id = i.feed_id 
        GROUP BY f.id 
        ORDER BY f.custom_category ASC, f.title ASC
        """)
        feeds = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return feeds

def batch_create_feeds(feeds_list: list):
    """Crea o actualiza feeds. Retorna la lista de registros creados/actualizados
    (con su id real), NUNCA un conteo, porque el id real se necesita para
    enlazar los items via feed_id. Lanza RuntimeError si Supabase rechaza el insert."""
    if not feeds_list:
        return []

    if is_supabase():
        clean_batch = []
        for f in feeds_list:
            clean_batch.append({
                "title": f["title"],
                "feed_url": f["feed_url"],
                "site_url": f.get("site_url", ""),
                "feed_type": f.get("feed_type", "rss"),
                "channel_id": f.get("channel_id"),
                "custom_category": f.get("custom_category", "General"),
                "is_active": True
            })

        # Try batch upsert
        res = supabase_request("feeds?on_conflict=feed_url", method="POST", data=clean_batch, headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})
        if isinstance(res, list):
            return res

        # If batch on_conflict fails, try standard batch insert
        res_standard = supabase_request("feeds", method="POST", data=clean_batch)
        if isinstance(res_standard, list):
            return res_standard

        err_msg = res.get("message") if isinstance(res, dict) else str(res)
        raise RuntimeError(f"Supabase rechazó la creación de feeds: {err_msg}")
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        created = []
        for f in feeds_list:
            try:
                cursor.execute("""
                INSERT INTO feeds (title, feed_url, site_url, feed_type, channel_id, custom_category)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(feed_url) DO UPDATE SET title=excluded.title, custom_category=excluded.custom_category
                """, (f["title"], f["feed_url"], f.get("site_url", ""), f.get("feed_type", "rss"), f.get("channel_id"), f.get("custom_category", "General")))
                row = cursor.execute("SELECT id FROM feeds WHERE feed_url = ?", (f["feed_url"],)).fetchone()
                created.append({"id": row["id"], "feed_url": f["feed_url"]})
            except Exception as e:
                print(f"Error creando feed local '{f.get('title')}': {e}")
                continue
        conn.commit()
        conn.close()
        return created

def create_or_update_feed(title: str, feed_url: str, feed_type: str = "youtube", category: str = "General", site_url: str = ""):
    created = batch_create_feeds([{
        "title": title,
        "feed_url": feed_url,
        "feed_type": feed_type,
        "custom_category": category,
        "site_url": site_url
    }])
    if not created:
        raise RuntimeError(f"No se pudo crear/actualizar el feed: {feed_url}")
    return created[0]["id"]

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
        # Postgres rechaza el batch completo si el UPSERT intenta afectar el mismo
        # guid dos veces en un solo comando (ON CONFLICT DO UPDATE cannot affect
        # row a second time). Dedup por guid antes de enviar, quedándonos con la
        # última versión (más reciente/enriquecida) de cada item.
        by_guid = {}
        for item in items:
            by_guid[item["guid"]] = item

        clean_batch = []
        for item in by_guid.values():
            clean_batch.append({
                "feed_id": item.get("feed_id"),
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

        res = supabase_request("items?on_conflict=guid", method="POST", data=clean_batch, headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})
        if isinstance(res, list):
            return len(res)

        err_msg = res.get("message") if isinstance(res, dict) else str(res)
        raise RuntimeError(f"Supabase rechazó la inserción de items: {err_msg}")
    else:
        from app.ingestion import save_items_to_db
        return save_items_to_db(items)

def ensure_seed_if_empty():
    feeds = fetch_feeds()
    if not feeds:
        batch_create_feeds([
            {"title": "Andrej Karpathy", "feed_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCXUPKJOtpqmgUOxw8p9n6Tw", "feed_type": "youtube", "custom_category": "IA & Agentes"},
            {"title": "Tiago Forte", "feed_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCBw92y4tWvjB0U_N9l3l7-w", "feed_type": "youtube", "custom_category": "PKM & Obsidian"},
            {"title": "New York Journal of Philosophy", "feed_url": "https://journalofphilosophy.substack.com/feed", "feed_type": "substack", "custom_category": "Filosofía"},
            {"title": "StudioBinder", "feed_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCQ4v9aB3X59bF3Jb7M6T-aA", "feed_type": "youtube", "custom_category": "Cine"}
        ])
