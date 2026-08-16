import json
import os
import re
import urllib.request
from app.db import get_db_connection

def get_user_profile():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_profile LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return {
            "focus_topics": [
                "Inteligencia Artificial Aplicada y Sistemas Agénticos",
                "Gestión del Conocimiento Personal (PKM, Obsidian, Zettelkasten)",
                "Transformación Institucional y Modelos Educativos",
                "Cine de autor y narrativa visual",
                "Música contemporánea y cultura del vinilo"
            ],
            "system_prompt_criteria": "Prioriza profundidad conceptual, rigor metodológico y valor accionable.",
            "min_relevance_threshold": 60,
            "learned_preferences": {"boosted_authors": [], "boosted_tags": {}, "penalized_tags": {}}
        }
        
    try:
        topics = json.loads(row["focus_topics"]) if row["focus_topics"] else []
    except Exception:
        topics = []
        
    try:
        learned = json.loads(row["learned_preferences"]) if row["learned_preferences"] else {}
    except Exception:
        learned = {}
        
    return {
        "focus_topics": topics,
        "system_prompt_criteria": row["system_prompt_criteria"] or "",
        "min_relevance_threshold": row["min_relevance_threshold"] or 60,
        "learned_preferences": learned,
        "api_key_gemini": row["api_key_gemini"] or os.environ.get("GEMINI_API_KEY", ""),
        "api_key_openai": row["api_key_openai"] or os.environ.get("OPENAI_API_KEY", "")
    }

def record_feedback(item_id: int, rating: str, comment: str = ""):
    """
    Saves user rating ('love', 'like', 'dislike', 'none') and feedback comments,
    and dynamically adapts learned preferences.
    """
    from app.storage import save_user_feedback, get_item_by_id, update_learned_preferences, fetch_profile

    save_user_feedback(item_id, rating, comment)

    item = get_item_by_id(item_id)

    if item:
        profile = fetch_profile()
        learned = profile.get("learned_preferences", {})
        boosted_authors = set(learned.get("boosted_authors", []))
        boosted_tags = learned.get("boosted_tags", {})
        penalized_tags = learned.get("penalized_tags", {})

        try:
            raw_tags = item.get("topic_tags")
            tags = json.loads(raw_tags) if isinstance(raw_tags, str) and raw_tags else (raw_tags or [])
        except Exception:
            tags = []

        author = item.get("author")

        if rating in ["love", "like"]:
            if author:
                boosted_authors.add(author)
            weight = 2 if rating == "love" else 1
            for t in tags:
                boosted_tags[t] = boosted_tags.get(t, 0) + weight
                if t in penalized_tags:
                    penalized_tags[t] = max(0, penalized_tags[t] - 1)
        elif rating == "dislike":
            if author in boosted_authors:
                boosted_authors.remove(author)
            for t in tags:
                penalized_tags[t] = penalized_tags.get(t, 0) + 2
                if t in boosted_tags:
                    boosted_tags[t] = max(0, boosted_tags[t] - 1)

        updated_learned = {
            "boosted_authors": list(boosted_authors),
            "boosted_tags": boosted_tags,
            "penalized_tags": penalized_tags
        }

        update_learned_preferences(updated_learned)

def analyze_with_llm(title: str, author: str, content_type: str, content_text: str, profile: dict) -> dict:
    topics_list = profile["focus_topics"]
    criteria = profile["system_prompt_criteria"]
    learned = profile.get("learned_preferences", {})
    
    api_key_gemini = profile.get("api_key_gemini")
    
    if api_key_gemini:
        try:
            prompt = f"""Eres el curador personal de contenido para León Velázquez.
Evalúa este material contra sus intereses y su historial de aprendizaje adaptativo.
Responde ÚNICAMENTE en formato JSON.

ÁREAS DE INTERÉS PRINCIPALES:
{json.dumps(topics_list, ensure_ascii=False, indent=2)}

CRITERIO EDITORIAL:
{criteria}

PREFERENCIAS APRENDIDAS (Basadas en feedback real):
- Autores favoritos: {', '.join(learned.get('boosted_authors', []))}
- Temas con feedback muy positivo: {json.dumps(learned.get('boosted_tags', {}), ensure_ascii=False)}
- Temas que suele descartar: {json.dumps(learned.get('penalized_tags', {}), ensure_ascii=False)}

CONTENIDO A EVALUAR:
- Título: {title}
- Autor / Canal: {author}
- Tipo: {content_type}
- Texto / Transcripción:
{content_text[:6000]}

Tu respuesta DEBE ser este JSON exacto:
{{
  "relevance_score": (número entero de 0 a 100),
  "summary_tldr": "Resumen conciso y fluido de 2 párrafos.",
  "key_takeaways": [
    "Idea clave 1",
    "Idea clave 2",
    "Idea clave 3"
  ],
  "curator_note": "1 frase concisa de por qué vale la pena verlo.",
  "topic_tags": ["Etiqueta1", "Etiqueta2"]
}}
"""
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key_gemini}"
            headers = {'Content-Type': 'application/json'}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=12) as response:
                result_data = json.loads(response.read().decode('utf-8'))
                raw_text = result_data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(raw_text)
        except Exception:
            pass

    # Heuristic Engine with Learned Preferences Integration
    combined_text = f"{title} {author} {content_text}".lower()
    score_base = 52
    matched_tags = []
    relevance_reasons = []
    
    topic_keywords = {
        "IA & Agentes": ["agente", "agent", "agéntico", "llm", "ia", "ai", "prompt", "mcp", "workflow", "automati", "modelo"],
        "PKM & Notas": ["obsidian", "pkm", "knowledge", "zettelkasten", "second brain", "nota", "vault", "organi", "markdown"],
        "Educación & Estrategia": ["educa", "universidad", "modelo", "aprendizaje", "competencia", "docen", "estrat", "transforma", "fit", "maps"],
        "Cine & Narrativa": ["cine", "película", "director", "filme", "nolan", "guion", "fotografía", "narrativa", "formato"],
        "Música & Cultura": ["música", "vinilo", "álbum", "banda", "concierto", "indie", "reseña", "post-punk", "sonido"],
        "Enología & Gourmet": ["vino", "enología", "maridaje", "cocina", "gastronomía", "uva", "cata", "thermomix"]
    }
    
    for tag, kws in topic_keywords.items():
        hits = sum(1 for kw in kws if kw in combined_text)
        if hits >= 2:
            matched_tags.append(tag)
            score_base += min(hits * 12, 36)
            relevance_reasons.append(tag)
            
    # Apply Learned Preferences Boosts / Penalties
    boosted_authors = learned.get("boosted_authors", [])
    if any(ba.lower() in author.lower() for ba in boosted_authors):
        score_base += 15
        relevance_reasons.append(f"Canal favorito ({author})")
        
    boosted_tags = learned.get("boosted_tags", {})
    for t, weight in boosted_tags.items():
        if t in matched_tags:
            score_base += min(weight * 5, 20)
            
    penalized_tags = learned.get("penalized_tags", {})
    for t, weight in penalized_tags.items():
        if t in matched_tags:
            score_base -= min(weight * 8, 25)

    # Clean summary and takeaways
    clean_lines = [l.strip() for l in content_text.split("\n") if len(l.strip()) > 35]
    if len(clean_lines) >= 2:
        summary_tldr = f"{clean_lines[0]}\n\n{clean_lines[1]}"
    elif content_text.strip():
        summary_tldr = content_text.strip()[:350] + ("..." if len(content_text) > 350 else "")
    else:
        summary_tldr = f"Análisis publicado por {author} sobre {title}."
        
    takeaways = []
    if len(clean_lines) >= 4:
        for line in clean_lines[2:5]:
            takeaways.append(line[:120].strip(" -•*"))
    if len(takeaways) < 2:
        takeaways = [
            f"Análisis clave presentado por {author}.",
            f"Conexión directa con {', '.join(matched_tags) if matched_tags else 'tus temas prioritarios'}."
        ]
        
    final_score = max(15, min(99, score_base))
    
    if relevance_reasons:
        curator_note = f"Alta afinidad con {', '.join(relevance_reasons[:2])}."
    else:
        curator_note = f"Publicación de {author} relevante para explorar."
        
    if not matched_tags:
        matched_tags = ["Lecturas"]
        
    return {
        "relevance_score": final_score,
        "summary_tldr": summary_tldr,
        "key_takeaways": takeaways,
        "curator_note": curator_note,
        "topic_tags": matched_tags
    }

def process_item_ai(item_id: int):
    from app.storage import get_item_by_id, save_item_analysis, fetch_profile

    item = get_item_by_id(item_id)
    if not item:
        return None

    profile = fetch_profile()
    content_text = item.get("transcript") or item.get("raw_content") or item.get("title")

    analysis = analyze_with_llm(
        title=item["title"],
        author=item["author"],
        content_type=item["content_type"],
        content_text=content_text,
        profile=profile
    )

    save_item_analysis(item_id, analysis)
    return analysis

def process_all_pending_items(limit: int = 20):
    from app.storage import get_pending_item_ids

    pending_ids = get_pending_item_ids(limit)
    processed = 0
    for item_id in pending_ids:
        process_item_ai(item_id)
        processed += 1
    return processed
