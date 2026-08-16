import sqlite3
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

DB_PATH = os.environ.get("FEED_DB_PATH", "/tmp/feed_curator.db")

def is_supabase():
    return bool(SUPABASE_URL and SUPABASE_KEY)

# ==================== SUPABASE REST CLIENT ====================
def supabase_request(endpoint: str, method: str = "GET", data: dict or list = None, params: dict = None, headers_extra: dict = None):
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    if params:
        query_str = urllib.parse.urlencode(params)
        url += f"?{query_str}"
        
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    if headers_extra:
        headers.update(headers_extra)
        
    body_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_data = resp.read().decode('utf-8')
            return json.loads(resp_data) if resp_data else []
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        return []
    except Exception:
        return []

# ==================== LOCAL SQLITE FALLBACK ====================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    if is_supabase():
        # In Supabase, tables are created via Supabase SQL Editor.
        # We ensure user_profile has default record if empty.
        try:
            profile = supabase_request("user_profile?select=*&limit=1")
            if not profile:
                default_topics = json.dumps([
                    "Inteligencia Artificial Aplicada y Sistemas Agénticos",
                    "Gestión del Conocimiento Personal (PKM, Obsidian, Zettelkasten)",
                    "Transformación Institucional y Modelos Educativos (MAPS, FIT)",
                    "Cine de autor, narrativa visual y formato gran escala",
                    "Música contemporánea, post-punk, vinilos y análisis cultural",
                    "Enología y exploración gastronómica"
                ], ensure_ascii=False)
                default_prompt = "Prioriza profundidad conceptual, rigor analítico y valor duradero. Descarta clickbait y noticias superficiales."
                supabase_request("user_profile", method="POST", data={
                    "id": 1,
                    "user_name": "León Velázquez",
                    "focus_topics": default_topics,
                    "system_prompt_criteria": default_prompt,
                    "learned_preferences": json.dumps({"boosted_authors": [], "boosted_tags": {}, "penalized_tags": {}}, ensure_ascii=False)
                })
        except Exception:
            pass
        return

    # SQLite Local init
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feeds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        feed_url TEXT UNIQUE NOT NULL,
        site_url TEXT,
        feed_type TEXT NOT NULL,
        channel_id TEXT,
        custom_category TEXT DEFAULT 'General',
        icon_url TEXT,
        is_active INTEGER DEFAULT 1,
        last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feed_id INTEGER NOT NULL,
        guid TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        author TEXT,
        published_at TEXT,
        content_type TEXT NOT NULL,
        video_id TEXT,
        thumbnail_url TEXT,
        duration_seconds INTEGER DEFAULT 0,
        raw_content TEXT,
        transcript TEXT,
        relevance_score INTEGER DEFAULT 0,
        summary_tldr TEXT,
        key_takeaways TEXT,
        curator_note TEXT,
        topic_tags TEXT,
        ai_processed INTEGER DEFAULT 0,
        ai_processed_at TEXT,
        status TEXT DEFAULT 'inbox',
        user_rating TEXT DEFAULT 'none',
        user_feedback_comment TEXT,
        feedback_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(feed_id) REFERENCES feeds(id) ON DELETE CASCADE
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_profile (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT DEFAULT 'León Velázquez',
        focus_topics TEXT,
        system_prompt_criteria TEXT,
        learned_preferences TEXT,
        min_relevance_threshold INTEGER DEFAULT 60,
        api_key_gemini TEXT,
        api_key_openai TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("SELECT COUNT(*) as count FROM user_profile")
    if cursor.fetchone()["count"] == 0:
        default_topics = json.dumps([
            "Inteligencia Artificial Aplicada y Sistemas Agénticos",
            "Gestión del Conocimiento Personal (PKM, Obsidian, Zettelkasten)",
            "Transformación Institucional y Modelos Educativos (MAPS, FIT)",
            "Cine de autor, narrativa visual y formato gran escala",
            "Música contemporánea, post-punk, vinilos y análisis cultural",
            "Enología y exploración gastronómica"
        ], ensure_ascii=False)
        default_prompt = "Prioriza profundidad conceptual, rigor analítico y valor duradero."
        cursor.execute("""
        INSERT INTO user_profile (user_name, focus_topics, system_prompt_criteria, learned_preferences)
        VALUES (?, ?, ?, ?)
        """, ("León Velázquez", default_topics, default_prompt, json.dumps({"boosted_authors": [], "boosted_tags": {}, "penalized_tags": {}}, ensure_ascii=False)))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database init executed. Supabase active:", is_supabase())
