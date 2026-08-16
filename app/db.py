import sqlite3
import json
import os
from datetime import datetime

# Default DB Path to /tmp/ for full POSIX lock compatibility in sandbox environments
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
        content_type TEXT NOT NULL, -- 'video', 'article', 'audio'
        video_id TEXT,
        thumbnail_url TEXT,
        duration_seconds INTEGER DEFAULT 0,
        raw_content TEXT,
        transcript TEXT,
        
        -- AI Enrichment fields
        relevance_score INTEGER DEFAULT 0, -- 0 to 100
        summary_tldr TEXT,
        key_takeaways TEXT, -- JSON array
        curator_note TEXT,
        topic_tags TEXT, -- JSON array
        ai_processed INTEGER DEFAULT 0, -- 0=pending, 1=processed, 2=failed
        ai_processed_at TEXT,
        
        -- User State
        status TEXT DEFAULT 'inbox', -- 'inbox', 'reading', 'archived', 'favorite'
        obsidian_exported INTEGER DEFAULT 0,
        user_notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(feed_id) REFERENCES feeds(id) ON DELETE CASCADE
    );
    """)

    # 3. User Focus Profile / Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_profile (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT DEFAULT 'Usuario',
        obsidian_vault_name TEXT DEFAULT 'ObsidianVault',
        obsidian_folder TEXT DEFAULT 'CuratedFeed',
        focus_topics TEXT, -- JSON array of topics
        system_prompt_criteria TEXT,
        min_relevance_threshold INTEGER DEFAULT 60,
        auto_summarize INTEGER DEFAULT 1,
        api_key_gemini TEXT,
        api_key_openai TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Populate default profile if empty
    cursor.execute("SELECT COUNT(*) as count FROM user_profile")
    if cursor.fetchone()["count"] == 0:
        default_topics = json.dumps([
            "Inteligencia Artificial Aplicada y Sistemas Agénticos",
            "Gestión del Conocimiento Personal (PKM, Obsidian, Zettelkasten)",
            "Transformación Institucional y Estrategia Educativa",
            "Modelos Educativos Innovadores (MAPS, FIT, Credenciales Apilables)",
            "Cine de autor, narrativa visual y crítica cinematográfica",
            "Música contemporánea, vinilos y análisis cultural",
            "Enología, gastronomía y tecnología para el hogar"
        ], ensure_ascii=False)
        
        default_prompt = (
            "Evalúa el contenido considerando su profundidad conceptual, rigor metodológico y aplicabilidad práctica. "
            "Prioriza análisis de fondo, casos de estudio y avances tecnológicos sobre noticias superficiales o clickbait."
        )

        cursor.execute("""
        INSERT INTO user_profile (
            user_name, obsidian_vault_name, obsidian_folder, 
            focus_topics, system_prompt_criteria, min_relevance_threshold
        ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "León Velázquez",
            "ObsidianVault",
            "Lecturas_y_Videos/Curados",
            default_topics,
            default_prompt,
            65
        ))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
