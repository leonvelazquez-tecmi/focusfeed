import sqlite3
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime

# Check all possible naming conventions for Supabase env vars in Vercel
SUPABASE_URL = (
    os.environ.get("SUPABASE_URL") or 
    os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or 
    ""
).rstrip("/")

SUPABASE_KEY = (
    os.environ.get("SUPABASE_KEY") or 
    os.environ.get("SUPABASE_ANON_KEY") or 
    os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or 
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or 
    ""
)

DB_PATH = os.environ.get("FEED_DB_PATH", "/tmp/feed_curator.db")

def is_supabase():
    return bool(SUPABASE_URL and SUPABASE_KEY)

def get_supabase_debug_info():
    return {
        "is_supabase": is_supabase(),
        "url_present": bool(SUPABASE_URL),
        "url_preview": SUPABASE_URL[:25] + "..." if SUPABASE_URL else "None",
        "key_present": bool(SUPABASE_KEY),
        "key_preview": SUPABASE_KEY[:10] + "..." if SUPABASE_KEY else "None"
    }

# ==================== SUPABASE REST CLIENT ====================
def supabase_request(endpoint: str, method: str = "GET", data=None, headers_extra: dict = None):
    if not is_supabase():
        return None
        
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
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
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp_data = resp.read().decode('utf-8')
            return json.loads(resp_data) if resp_data else []
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')
        print(f"Supabase HTTPError {e.code}: {error_body}")
        # Return error dict so caller can diagnose
        return {"error": True, "status": e.code, "message": error_body}
    except Exception as e:
        print(f"Supabase Exception: {e}")
        return {"error": True, "message": str(e)}

# ==================== SQLITE FALLBACK ====================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    if is_supabase():
        return
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
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("DB Init Check:", get_supabase_debug_info())
