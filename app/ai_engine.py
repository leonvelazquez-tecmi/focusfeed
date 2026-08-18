"""Motor de curación.

Dos operaciones distintas, y la distinción es la que sostiene el margen:

  analyze_content()   → universal. Resumen, ideas clave y tags de un item.
                        Es igual para todos, así que se calcula UNA vez por item
                        y se guarda en el corpus global.

  score_for_user()    → personal. Relevancia y nota del curador contra el perfil
                        de una persona. Es lo único que cambia entre usuarios.

Si estas dos se mezclan, mil suscriptores del mismo blog pagan mil análisis del
mismo texto. Ver context/unit-economics.md.
"""

import json
import os
import re
import unicodedata
import urllib.request

# Precio del modelo. Se configura por entorno para poder ajustarlo sin tocar
# código. Mientras esté en 0 se siguen registrando los tokens, que es el dato
# duro; el costo se puede recalcular después.
PRICE_IN_PER_MTOK = float(os.environ.get("AI_PRICE_IN_PER_MTOK", "0"))
PRICE_OUT_PER_MTOK = float(os.environ.get("AI_PRICE_OUT_PER_MTOK", "0"))

GEMINI_MODEL = os.environ.get("AI_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

STOPWORDS = {
    "de", "la", "el", "y", "en", "los", "las", "del", "para", "con", "por", "una", "uno",
    "the", "and", "for", "with", "of", "to", "in", "on", "a", "an",
}


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000.0) * PRICE_IN_PER_MTOK + \
           (output_tokens / 1_000_000.0) * PRICE_OUT_PER_MTOK


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _keywords_from_topics(topics: list) -> dict:
    """Deriva palabras clave del perfil de cada usuario.

    Antes esto era un diccionario fijo con los temas de una sola persona, lo que
    hacía imposible que el motor sirviera a otro usuario.
    """
    mapping = {}
    for topic in topics or []:
        words = [w for w in re.split(r"[^\wáéíóúñ]+", _normalize(topic))
                 if len(w) > 3 and w not in STOPWORDS]
        if words:
            mapping[topic] = words
    return mapping


# ==========================================================
# Universal: resumen del item
# ==========================================================

def analyze_content(title: str, author: str, content_type: str, content_text: str,
                    user_id_for_billing=None, item_id_for_billing=None) -> dict:
    """Resumen, ideas clave y tags. Sin score: eso es personal."""
    if GEMINI_API_KEY:
        result = _analyze_with_llm(title, author, content_type, content_text,
                                   user_id_for_billing, item_id_for_billing)
        if result:
            return result
    return _analyze_heuristic(title, author, content_text)


def _analyze_with_llm(title, author, content_type, content_text,
                      user_id_for_billing, item_id_for_billing):
    prompt = f"""Eres un editor que resume material para lectores profesionales.
Responde ÚNICAMENTE en JSON, en español.

CONTENIDO
- Título: {title}
- Autor o canal: {author}
- Tipo: {content_type}
- Texto:
{(content_text or '')[:6000]}

Devuelve exactamente esta estructura:
{{
  "summary_tldr": "Resumen fluido de dos párrafos, sin markdown.",
  "key_takeaways": ["Idea 1", "Idea 2", "Idea 3"],
  "topic_tags": ["Etiqueta1", "Etiqueta2"]
}}
No incluyas juicio sobre a quién le sirve: eso se decide después, por lector."""

    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw_text)

        usage = data.get("usageMetadata", {})
        input_tokens = int(usage.get("promptTokenCount", 0))
        output_tokens = int(usage.get("candidatesTokenCount", 0))
        try:
            from app.storage import log_ai_usage
            log_ai_usage(user_id_for_billing, item_id_for_billing, GEMINI_MODEL,
                         input_tokens, output_tokens,
                         _estimate_cost(input_tokens, output_tokens))
        except Exception:
            pass

        return {
            "summary_tldr": parsed.get("summary_tldr", ""),
            "key_takeaways": parsed.get("key_takeaways", []),
            "topic_tags": parsed.get("topic_tags", []),
        }
    except Exception as e:
        print(f"analyze_content: el modelo falló, se usa el motor de respaldo: {e}")
        return None


def _analyze_heuristic(title, author, content_text):
    """Respaldo sin modelo. Sirve para no dejar la tarjeta vacía; no es el producto."""
    clean_lines = [l.strip() for l in (content_text or "").split("\n") if len(l.strip()) > 35]
    if len(clean_lines) >= 2:
        summary = f"{clean_lines[0]}\n\n{clean_lines[1]}"
    elif (content_text or "").strip():
        summary = content_text.strip()[:350] + ("..." if len(content_text) > 350 else "")
    else:
        summary = f"Publicación de {author}: {title}."

    takeaways = [l[:120].strip(" -•*") for l in clean_lines[2:5]] or [
        f"Material publicado por {author}."]

    return {"summary_tldr": summary, "key_takeaways": takeaways, "topic_tags": []}


# ==========================================================
# Personal: score contra el perfil de un lector
# ==========================================================

def score_for_user(item: dict, profile: dict) -> dict:
    """Relevancia de 0 a 100 y nota del curador para un lector concreto.

    Hoy es determinista y barato a propósito: corre sobre cada item de cada
    usuario. La ruta siguiente es embeddings del perfil contra embeddings del
    item, y reservar el modelo grande para el top del día.
    """
    topics = profile.get("focus_topics", [])
    learned = profile.get("learned_preferences", {}) or {}
    keyword_map = _keywords_from_topics(topics)

    tags = item.get("topic_tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = []
    tags = tags or []

    haystack = _normalize(
        f"{item.get('title','')} {item.get('author','')} "
        f"{item.get('summary_tldr') or item.get('raw_content','')} {' '.join(tags)}")

    score = 52
    matched = []
    for topic, words in keyword_map.items():
        hits = sum(1 for w in words if w in haystack)
        if hits >= 1:
            matched.append(topic)
            score += min(hits * 12, 30)

    author = item.get("author") or ""
    reasons = list(matched)
    for boosted in learned.get("boosted_authors", []):
        if boosted and boosted.lower() in author.lower():
            score += 15
            reasons.append(f"fuente que sueles calificar bien ({author})")
            break

    for tag, weight in (learned.get("boosted_tags") or {}).items():
        if tag in tags or tag in matched:
            score += min(int(weight) * 5, 20)
    for tag, weight in (learned.get("penalized_tags") or {}).items():
        if tag in tags or tag in matched:
            score -= min(int(weight) * 8, 25)

    score = max(15, min(99, score))

    if reasons:
        note = f"Coincide con {', '.join(reasons[:2])}."
    else:
        note = f"Publicación de {author} fuera de tus temas declarados."

    return {"relevance_score": score, "curator_note": note}


# ==========================================================
# Orquestación
# ==========================================================

def process_item_ai(item_id: int, user_id_for_billing=None):
    """Analiza un item del corpus global. No escribe nada personal."""
    from app.storage import get_item_by_id, save_item_analysis

    item = get_item_by_id(item_id)
    if not item:
        return None

    content_text = item.get("transcript") or item.get("raw_content") or item.get("title")
    analysis = analyze_content(
        title=item["title"], author=item.get("author", ""),
        content_type=item.get("content_type", "article"), content_text=content_text,
        user_id_for_billing=user_id_for_billing, item_id_for_billing=item_id)

    save_item_analysis(item_id, analysis)
    return analysis


def score_pending_for_user(user_id: str, limit: int = 40):
    """Pone score personal a los items que este usuario todavía no tiene calificados."""
    from app.storage import fetch_items, fetch_profile, save_user_score

    profile = fetch_profile(user_id)
    data = fetch_items(user_id, tab="curated", limit=limit)
    scored = 0
    for item in data.get("items", []):
        if item.get("relevance_score") is not None:
            continue
        result = score_for_user(item, profile)
        save_user_score(user_id, item["id"], result["relevance_score"], result["curator_note"])
        scored += 1
    return scored


def record_feedback(user_id: str, item_id: int, rating: str, comment: str = ""):
    """Guarda la calificación y ajusta las preferencias aprendidas del usuario."""
    from app.storage import (save_user_feedback, get_item_by_id,
                             update_learned_preferences, fetch_profile)

    save_user_feedback(user_id, item_id, rating, comment)

    item = get_item_by_id(item_id)
    if not item:
        return

    profile = fetch_profile(user_id)
    learned = profile.get("learned_preferences", {}) or {}
    boosted_authors = set(learned.get("boosted_authors", []))
    boosted_tags = dict(learned.get("boosted_tags", {}))
    penalized_tags = dict(learned.get("penalized_tags", {}))

    try:
        raw_tags = item.get("topic_tags")
        tags = json.loads(raw_tags) if isinstance(raw_tags, str) and raw_tags else (raw_tags or [])
    except Exception:
        tags = []

    author = item.get("author")

    if rating in ("love", "like"):
        if author:
            boosted_authors.add(author)
        weight = 2 if rating == "love" else 1
        for t in tags:
            boosted_tags[t] = boosted_tags.get(t, 0) + weight
            if t in penalized_tags:
                penalized_tags[t] = max(0, penalized_tags[t] - 1)
    elif rating == "dislike":
        boosted_authors.discard(author)
        for t in tags:
            penalized_tags[t] = penalized_tags.get(t, 0) + 2
            if t in boosted_tags:
                boosted_tags[t] = max(0, boosted_tags[t] - 1)

    update_learned_preferences(user_id, {
        "boosted_authors": list(boosted_authors),
        "boosted_tags": boosted_tags,
        "penalized_tags": penalized_tags,
    })
