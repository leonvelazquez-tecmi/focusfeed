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
import time
import urllib.error
import urllib.request

# JEV (TypeSafe vía OpenRouter): score personal de "Para ti". Sin llave, el
# score sigue siendo el heurístico de score_for_user().
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"

# Precio del modelo. Se configura por entorno para poder ajustarlo sin tocar
# código. Mientras esté en 0 se siguen registrando los tokens, que es el dato
# duro; el costo se puede recalcular después.
PRICE_IN_PER_MTOK = float(os.environ.get("AI_PRICE_IN_PER_MTOK", "0"))
PRICE_OUT_PER_MTOK = float(os.environ.get("AI_PRICE_OUT_PER_MTOK", "0"))

# Modelo de Gemini: AI_MODEL lo fija a mano; si no, se resuelve contra la lista
# viva de la API (ver _resolve_gemini_model) para que un retiro no rompa la app.
GEMINI_MODEL_OVERRIDE = os.environ.get("AI_MODEL", "")
GEMINI_MODEL_FALLBACK = "gemini-3.5-flash"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_MODELO_EXCLUIDO = ("preview", "image", "tts", "live", "embedding", "audio", "exp",
                    "thinking", "computer", "robotics", "vision")

# Salud de la IA: la falla del modelo no se puede quedar callada detrás del respaldo.
AI_HEALTH = {"gemini_model": None, "last_error": None, "last_error_at": None}
_MODEL_CACHE = {"name": None}
_ULTIMO_REGISTRO = {}
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


def _note_error(source: str, err, model: str = "", user_id=None):
    """Deja la falla a la vista: /api/status y una fila ai_usage kind=ai_error."""
    from datetime import datetime, timezone
    AI_HEALTH["last_error"] = f"{source}: {err}"
    AI_HEALTH["last_error_at"] = datetime.now(timezone.utc).isoformat()
    ahora = time.time()
    if ahora - _ULTIMO_REGISTRO.get(source, 0) < 600:  # una fila por fuente cada 10 min
        return
    _ULTIMO_REGISTRO[source] = ahora
    try:
        from app.storage import log_ai_usage
        log_ai_usage(user_id, None, model or source, kind="ai_error")
    except Exception:
        pass


def _version_key(name: str):
    m = re.search(r"gemini-(\d+(?:\.\d+)*)", name)
    ver = tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
    return (ver, "lite" not in name)


def _list_flash_models() -> list:
    url = f"{GEMINI_BASE}/models?key={GEMINI_API_KEY}&pageSize=200"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    nombres = []
    for m in data.get("models", []):
        name = (m.get("name") or "").replace("models/", "")
        if ("flash" in name and name.startswith("gemini-")
                and "generateContent" in (m.get("supportedGenerationMethods") or [])
                and not any(x in name for x in _MODELO_EXCLUIDO)):
            nombres.append(name)
    return sorted(nombres, key=_version_key, reverse=True)


def _resolve_gemini_model(exclude=None) -> str:
    """Modelo vigente: AI_MODEL, o el flash más nuevo de la lista viva de la API."""
    exclude = set(exclude or [])
    if GEMINI_MODEL_OVERRIDE and GEMINI_MODEL_OVERRIDE not in exclude:
        return GEMINI_MODEL_OVERRIDE
    if _MODEL_CACHE["name"] and _MODEL_CACHE["name"] not in exclude:
        return _MODEL_CACHE["name"]
    try:
        for name in _list_flash_models():
            if name not in exclude:
                _MODEL_CACHE["name"] = name
                AI_HEALTH["gemini_model"] = name
                return name
    except Exception as e:
        _note_error("gemini_list_models", e)
    return GEMINI_MODEL_FALLBACK


def _gemini_generate(model: str, prompt: str, timeout: int = 15) -> dict:
    url = f"{GEMINI_BASE}/models/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"response_mime_type": "application/json"}}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _gemini_with_recovery(prompt: str, timeout: int = 15):
    """Llama a Gemini; si el modelo ya no existe, resuelve otro y reintenta una vez."""
    model = _resolve_gemini_model()
    try:
        return model, _gemini_generate(model, prompt, timeout)
    except urllib.error.HTTPError as e:
        if e.code not in (400, 403, 404):
            raise
        _note_error("gemini_model", f"{model} HTTP {e.code}", model)
        _MODEL_CACHE["name"] = None
        otro = _resolve_gemini_model(exclude={model})
        if otro == model:
            raise
        return otro, _gemini_generate(otro, prompt, timeout)


def ai_health() -> dict:
    return {"ai_model": GEMINI_MODEL_OVERRIDE or _MODEL_CACHE["name"] or None,
            "ai_last_error": AI_HEALTH["last_error"],
            "ai_last_error_at": AI_HEALTH["last_error_at"]}


def ai_canary() -> dict:
    """Prueba mínima de Gemini y JEV. Alimenta el cron diario y el aviso en GitHub."""
    res = {"gemini": "sin_llave", "jev": "sin_llave"}
    if GEMINI_API_KEY:
        try:
            model, _ = _gemini_with_recovery('Responde solo {"ok": true}', timeout=15)
            res["gemini"] = f"ok:{model}"
        except Exception as e:
            res["gemini"] = f"error:{e}"
            _note_error("canary_gemini", e)
    if OPENROUTER_API_KEY:
        p = score_with_jev({"id": None, "title": "prueba", "author": "canario",
                            "content_type": "article"}, {"focus_topics": ["IA"]}, None)
        res["jev"] = "ok" if p is not None else "error"
    return res


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
        modelo, data = _gemini_with_recovery(prompt)

        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw_text)

        usage = data.get("usageMetadata", {})
        input_tokens = int(usage.get("promptTokenCount", 0))
        output_tokens = int(usage.get("candidatesTokenCount", 0))
        try:
            from app.storage import log_ai_usage
            log_ai_usage(user_id_for_billing, item_id_for_billing, modelo,
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
        _note_error("analyze_content", e)
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


def _jev_question(profile: dict) -> dict:
    topics = "; ".join(profile.get("focus_topics", []))
    learned = profile.get("learned_preferences", {}) or {}
    gusta = ", ".join(list(learned.get("boosted_authors", []))[:8])
    evita = ", ".join(list((learned.get("penalized_tags") or {}))[:8])
    extra = ""
    if gusta:
        extra += f" Suele calificar bien: {gusta}."
    if evita:
        extra += f" Suele descartar: {evita}."
    return {"interesa": {
        "type": "noul",
        "instructions": "¿Este contenido vale el tiempo de este lector profesional?",
        "criteria": {
            "true": (f"Trata de sus temas ({topics}) con sustancia. "
                     f"{profile.get('system_prompt_criteria', '')}{extra}"),
            "false": ("Es entretenimiento, noticia efímera, clickbait, reseña de ocio o un tema "
                      "ajeno a sus intereses, aunque venga de una fuente que sigue."),
        },
    }}


def score_with_jev(item: dict, profile: dict, user_id=None):
    """Score personal 0 a 100 con JEV, o None si no hay llave o falla.

    Una llamada por item (~USD 0.00003). Cada llamada se registra en ai_usage.
    Requests con ZDR y data_collection=deny.
    """
    if not OPENROUTER_API_KEY:
        return None
    body = {
        "model": JEV_MODEL,
        "state": {"titulo": item.get("title", ""), "fuente": item.get("author", ""),
                  "tipo": item.get("content_type", ""),
                  "extracto": (item.get("summary_tldr") or item.get("raw_content") or "")[:800]},
        "questions": _jev_question(profile),
        "provider": {"zdr": True, "data_collection": "deny"},
    }
    req = urllib.request.Request(JEV_URL, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"})
    for intento in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            p = float(d["answers"]["interesa"]["noul"])
            cost = float(d.get("usage", {}).get("cost", 0.0) or 0.0)
            try:
                from app.storage import log_ai_usage
                log_ai_usage(user_id, item.get("id"), JEV_MODEL, cost_usd=cost, kind="jev_score")
            except Exception:
                pass
            return max(1, min(99, round(p * 100)))
        except (urllib.error.URLError, KeyError, ValueError, TypeError) as e:
            if intento == 1:
                print(f"score_with_jev: falló, se usa el heurístico: {e}")
                _note_error("jev", e, JEV_MODEL, user_id)
            else:
                time.sleep(1)
    return None


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
    """Pone score personal a los items nuevos que este usuario todavía no tiene calificados.

    Se mira la bandeja (los más recientes primero), no "Para ti": esa pestaña
    ordena por score, así que los items sin calificar quedaban siempre fuera de
    la ventana y nunca se calificaban.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.storage import fetch_items, fetch_profile, save_user_score

    profile = fetch_profile(user_id)
    data = fetch_items(user_id, tab="inbox", limit=limit * 4)
    pendientes = [i for i in data.get("items", []) if i.get("relevance_score") is None][:limit]

    def calificar(item):
        result = score_for_user(item, profile)
        jev_score = score_with_jev(item, profile, user_id)
        if jev_score is not None:
            result["relevance_score"] = jev_score
        return item["id"], result

    with ThreadPoolExecutor(8) as ex:
        resultados = list(ex.map(calificar, pendientes))
    for item_id, result in resultados:
        save_user_score(user_id, item_id, result["relevance_score"], result["curator_note"])
    return len(resultados)


# Qué le pasa al item en el feed según cómo lo califiques.
# El corazón guarda automático; el pulgar abajo lo saca del feed de verdad.
# 'like' es una señal más suave: entrena preferencias sin mover el item.
RATING_TO_STATUS = {
    "love": "reading",
    "dislike": "archived",
}


def record_feedback(user_id: str, item_id: int, rating: str, comment: str = ""):
    """Guarda la calificación, mueve el item de estado y ajusta las preferencias."""
    from app.storage import (save_user_feedback, get_item_by_id,
                             update_learned_preferences, fetch_profile,
                             set_item_status)

    save_user_feedback(user_id, item_id, rating, comment)

    new_status = RATING_TO_STATUS.get(rating)
    if new_status:
        set_item_status(user_id, item_id, new_status)

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
