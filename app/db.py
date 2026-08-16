import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.environ.get("FEED_DB_PATH", "/tmp/feed_curator.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Feeds table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feeds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        feed_url TEXT UNIQUE NOT NULL,
        site_url TEXT,
        feed_type TEXT NOT NULL, -- 'youtube', 'rss', 'podcast', 'substack'
        channel_id TEXT,
        custom_category TEXT DEFAULT 'General',
        icon_url TEXT,
        is_active INTEGER DEFAULT 1,
        last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Items table
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

    # Auto-migrate columns if table already existed with older schema
    try:
        cursor.execute("ALTER TABLE items ADD COLUMN user_rating TEXT DEFAULT 'none'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE items ADD COLUMN user_feedback_comment TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE items ADD COLUMN feedback_at TEXT")
    except Exception:
        pass

    # 3. User Profile table
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

    try:
        cursor.execute("ALTER TABLE user_profile ADD COLUMN learned_preferences TEXT")
    except Exception:
        pass

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
        
        default_prompt = (
            "Prioriza profundidad conceptual, rigor analítico y valor duradero. "
            "Descarta clickbait, noticias de corta duración y tutoriales superficiales."
        )

        cursor.execute("""
        INSERT INTO user_profile (
            user_name, focus_topics, system_prompt_criteria, learned_preferences
        ) VALUES (?, ?, ?, ?)
        """, (
            "León Velázquez",
            default_topics,
            default_prompt,
            json.dumps({"boosted_authors": [], "boosted_tags": {}, "penalized_tags": {}}, ensure_ascii=False)
        ))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized & migrated.")
