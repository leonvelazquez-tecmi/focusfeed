import sqlite3
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime

SUPABASE_URL = (
    os.environ.get("SUPABASE_URL") or 
    os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or 
    ""
).rstrip("/")

# El backend resuelve el usuario por su cuenta (app/auth.py) y luego filtra por
# user_id en cada consulta. Con RLS encendido, la llave anon no tiene auth.uid()
# y las tablas personales devolverían vacío. Por eso la service_role va primero.
# Esta llave NUNCA debe llegar al navegador.
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or
    os.environ.get("SUPABASE_KEY") or
    os.environ.get("SUPABASE_ANON_KEY") or
    os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or
    ""
)

USING_SERVICE_ROLE = bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))

DB_PATH = os.environ.get("FEED_DB_PATH", "/tmp/feed_curator.db")

LAST_SUPABASE_ERROR = None

def is_supabase():
    return bool(SUPABASE_URL and SUPABASE_KEY)

def get_supabase_debug_info():
    return {
        "is_supabase": is_supabase(),
        "url_present": bool(SUPABASE_URL),
        "url_preview": (SUPABASE_URL[:30] + "...") if SUPABASE_URL else "Falta URL",
        "key_present": bool(SUPABASE_KEY),
        "key_preview": (SUPABASE_KEY[:15] + "...") if SUPABASE_KEY else "Falta Key",
        "service_role": USING_SERVICE_ROLE,
        "last_error": LAST_SUPABASE_ERROR
    }

def supabase_request(endpoint: str, method: str = "GET", data=None, headers_extra: dict = None):
    global LAST_SUPABASE_ERROR
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
        with urllib.request.urlopen(req, timeout=9) as resp:
            resp_data = resp.read().decode('utf-8')
            LAST_SUPABASE_ERROR = None
            return json.loads(resp_data) if resp_data else []
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='ignore')
        LAST_SUPABASE_ERROR = f"HTTP {e.code}: {error_body}"
        print(f"Supabase HTTPError {e.code}: {error_body}")
        return {"error": True, "status": e.code, "message": error_body}
    except Exception as e:
        LAST_SUPABASE_ERROR = str(e)
        print(f"Supabase Exception: {e}")
        return {"error": True, "message": str(e)}

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Esquema local espejo de supabase_schema_v2.sql (corpus global + estado por usuario)."""
    if is_supabase():
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS feeds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        feed_url TEXT UNIQUE NOT NULL,
        site_url TEXT,
        feed_type TEXT NOT NULL,
        channel_id TEXT,
        icon_url TEXT,
        is_active INTEGER DEFAULT 1,
        last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feed_id INTEGER,
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
        summary_tldr TEXT,
        key_takeaways TEXT,
        topic_tags TEXT,
        ai_processed INTEGER DEFAULT 0,
        ai_processed_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        feed_id INTEGER NOT NULL,
        custom_category TEXT DEFAULT 'General',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, feed_id)
    );

    CREATE TABLE IF NOT EXISTS user_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'inbox',
        relevance_score INTEGER,
        curator_note TEXT,
        user_rating TEXT DEFAULT 'none',
        user_feedback_comment TEXT,
        feedback_at TEXT,
        scored_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, item_id)
    );

    CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        item_id INTEGER,
        kind TEXT NOT NULL DEFAULT 'item_analysis',
        model TEXT,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS user_profile (
        user_id TEXT PRIMARY KEY,
        user_name TEXT,
        focus_topics TEXT,
        system_prompt_criteria TEXT,
        learned_preferences TEXT,
        min_relevance_threshold INTEGER DEFAULT 60,
        plan TEXT DEFAULT 'free',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id);
    CREATE INDEX IF NOT EXISTS idx_uitems_user_status ON user_items(user_id, status);
    CREATE INDEX IF NOT EXISTS idx_usage_user_date ON ai_usage(user_id, created_at);
    """)
    conn.commit()
    conn.close()
