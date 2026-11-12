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
    """Llama a Gemini con recuperación.

    Si el modelo ya no existe (400/403/404) o está saturado (429/5xx/timeout),
    se salta al siguiente más nuevo de la lista viva, hasta 3 modelos. El que
    responde queda en caché; el que falló se descarta hasta el próximo arranque.
    """
    probados = []
    ultimo = None
    for _ in range(3):
        model = _resolve_gemini_model(exclude=probados)
        if model in probados:
            break
        try:
            data = _gemini_generate(model, prompt, timeout)
            _MODEL_CACHE["name"] = model
            AI_HEALTH["gemini_model"] = model
            return model, data
        except urllib.error.HTTPError as e:
            ultimo = e
            _note_error("gemini_model", f"{model} HTTP {e.code}", model)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ultimo = e
            _note_error("gemini_model", f"{model} {e}", model)
        probados.append(model)
        if _MODEL_CACHE["name"] == model:
            _MODEL_CACHE["name"] = None
    raise ultimo


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
    # Palabra completa, no subcadena: "gran" no cuenta dentro de "granular".
    tokens = set(re.findall(r"\w+", haystack))

    score = 52
    matched = []
    for topic, words in keyword_map.items():
        hits = sum(1 for w in words if w in tokens)
        # Una palabra suelta de un tema largo ("post" de "post-punk", "visual",
        # "cultural") no basta para decir que el artículo trata del tema.
        # Se piden dos palabras distintas, o todas si el tema tiene una sola.
        if hits >= min(2, len(words)):
            matched.append(topic)
            score += min(hits * 12, 30)

    author = item.get("author") or ""
    reasons = list(matched)
    for boosted in learned.get("boosted_authors", []):
        if boosted and boosted.lower() in author.lower():
            score += 15
            reasons.append(f"fuente que sueles calificar bien ({author})")
            break

    # Abrir una tarjeta suma poco por clic (3) y con tope (9), mucho menos que
    # el corazón (15 fijos).
    abiertos = learned.get("clicked_authors") or {}
    for nombre, veces in abiertos.items():
        if nombre and nombre.lower() in author.lower():
            score += min(int(veces) * CLICK_AUTHOR_POINTS, CLICK_AUTHOR_CAP)
            break

    # Fuente que has descartado de forma repetida (saldo de -5 o peor).
    if author and _author_strength(learned, author) <= AUTHOR_AVOID_AT:
        score -= AUTHOR_AVOID_PENALTY

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


def _author_strength(learned: dict, author: str) -> int:
    """Fuerza de una fuente para este lector, sumando todas sus señales.

    author_scores: corazón +3, relevante +2, guardar +2, pulgar abajo -3.
    clicked_authors: +1 por clic (contador propio, no se duplica en author_scores).
    Quien estaba en boosted_authors desde antes de author_scores vale +3 de base.
    """
    scores = learned.get("author_scores") or {}
    fuerza = int(scores.get(author, 0))
    if author not in scores and author in (learned.get("boosted_authors") or []):
        fuerza += 3
    fuerza += int((learned.get("clicked_authors") or {}).get(author, 0))
    return fuerza


def _learned_for_prompt(learned: dict) -> dict:
    """Lo aprendido, ordenado y recortado para la pregunta a JEV.

    Nada del buzón corporativo entra: sus remitentes y etiquetas están en
    private_authors / private_tags y sus títulos nunca se guardan como ejemplo.
    """
    privados_a = set(learned.get("private_authors") or [])
    privados_t = set(learned.get("private_tags") or [])

    fuerzas = {}
    for nombre in (set(learned.get("boosted_authors") or []) | set(learned.get("author_scores") or {})
                   | set(learned.get("clicked_authors") or {})):
        if nombre and nombre not in privados_a:
            fuerzas[nombre] = _author_strength(learned, nombre)
    # Empate: orden alfabético, para que la pregunta sea estable entre llamadas.
    valora = [a for a, f in sorted(fuerzas.items(), key=lambda kv: (-kv[1], kv[0]))
              if f >= AUTHOR_VALUE_AT][:PROMPT_LIST_MAX]
    descarta_a = [a for a, f in sorted(fuerzas.items(), key=lambda kv: (kv[1], kv[0]))
                  if f <= AUTHOR_AVOID_AT][:PROMPT_AVOID_AUTHORS_MAX]

    a_favor = learned.get("boosted_tags") or {}
    en_contra = learned.get("penalized_tags") or {}
    netos = {}
    for tag in set(a_favor) | set(en_contra):
        if tag and tag not in privados_t:
            netos[tag] = int(a_favor.get(tag, 0)) - int(en_contra.get(tag, 0))
    temas_si = [t for t, n in sorted(netos.items(), key=lambda kv: (-kv[1], kv[0])) if n > 0][:PROMPT_LIST_MAX]
    temas_no = [t for t, n in sorted(netos.items(), key=lambda kv: (kv[1], kv[0])) if n < 0][:PROMPT_LIST_MAX]

    def titulos(clave):
        out = []
        for ej in (learned.get(clave) or [])[-PROMPT_EXAMPLES:][::-1]:  # los más recientes primero
            t = (ej.get("t") if isinstance(ej, dict) else str(ej)) or ""
            if t.strip():
                out.append(t.strip()[:80])
        return out

    return {"valora": valora, "descarta_autores": descarta_a, "temas_si": temas_si,
            "temas_no": temas_no, "ej_si": titulos("liked_examples"), "ej_no": titulos("disliked_examples")}


def _jev_question(profile: dict) -> dict:
    topics = "; ".join(profile.get("focus_topics", []))
    learned = profile.get("learned_preferences", {}) or {}
    L = _learned_for_prompt(learned)

    extra_si = ""
    if L["temas_si"]:
        extra_si += f" Por lo que ha calificado, le interesan sobre todo: {', '.join(L['temas_si'])}."
    if L["valora"]:
        extra_si += f" Fuentes que más valora: {', '.join(L['valora'])}."
    if L["ej_si"]:
        extra_si += " Ejemplos recientes que le gustaron: " + "; ".join(f"«{t}»" for t in L["ej_si"]) + "."

    extra_no = ""
    if L["temas_no"]:
        extra_no += f" Suele descartar temas como: {', '.join(L['temas_no'])}."
    if L["descarta_autores"]:
        extra_no += f" Fuentes que suele descartar: {', '.join(L['descarta_autores'])}."
    if L["ej_no"]:
        extra_no += " Ejemplos recientes que descartó: " + "; ".join(f"«{t}»" for t in L["ej_no"]) + "."

    return {"interesa": {
        "type": "noul",
        "instructions": "¿Este contenido vale el tiempo de este lector profesional?",
        "criteria": {
            "true": (f"Trata de sus temas ({topics}) con sustancia. "
                     f"{profile.get('system_prompt_criteria', '')}{extra_si}"),
            "false": ("Es entretenimiento, noticia efímera, clickbait, reseña de ocio o un tema "
                      "ajeno a sus intereses, aunque venga de una fuente que sigue." + extra_no),
        },
    }}


def is_outlook_item(item: dict) -> bool:
    """Items que entraron desde el buzón corporativo (guid 'outlook:...').
    Ninguna función de este módulo puede mandarlos a Gemini ni a JEV."""
    return str((item or {}).get("guid") or "").startswith("outlook:")


def score_with_jev(item: dict, profile: dict, user_id=None):
    """Score personal 0 a 100 con JEV, o None si no hay llave o falla.

    Una llamada por item (~USD 0.00003). Cada llamada se registra en ai_usage.
    Requests con ZDR y data_collection=deny.
    """
    if not OPENROUTER_API_KEY:
        return None
    if is_outlook_item(item):
        return None  # política de datos de la organización: nada del buzón corporativo sale a un modelo externo
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
    if is_outlook_item(item):
        return None  # ya viene resumido por el agente autorizado; no se manda a ningún modelo externo

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
        # Un motor por item, nunca mezclados. Si JEV califica, el score es suyo y
        # no hay "Por qué": la nota del motor de palabras clave no explica un
        # score que ese motor no calculó. Si JEV no responde, el score y la nota
        # salen juntos del motor de respaldo.
        jev_score = score_with_jev(item, profile, user_id)
        if jev_score is not None:
            return item["id"], {"relevance_score": jev_score, "curator_note": None}
        return item["id"], score_for_user(item, profile)

    with ThreadPoolExecutor(8) as ex:
        resultados = list(ex.map(calificar, pendientes))
    for item_id, result in resultados:
        save_user_score(user_id, item_id, result["relevance_score"], result["curator_note"])
    return len(resultados)


# Qué le pasa al item en el feed según cómo lo califiques.
# El corazón guarda automático; el pulgar abajo lo saca del feed de verdad.
# 'like' es una señal más suave: entrena preferencias sin mover el item.
# Pesos de las señales, de más fuerte a más débil (positivas):
#   corazón: tags +2, fuente +3 (y fija en boosted_authors)
#   relevante (like): tags +1, fuente +2 (y fija)
#   guardar (botón): tags +1, fuente +2; quitar de guardados lo deshace
#   clic en la tarjeta: tags +1 y un contador por autor (+3 por clic al puntuar, tope +9)
#   nada: 0
# El pulgar abajo penaliza tags con 2 por tag y a la fuente con -3.
# Una fuente con saldo de -5 o peor se considera "que suele descartar".
CLICK_TAG_WEIGHT = 1
CLICK_AUTHOR_POINTS = 3
CLICK_AUTHOR_CAP = 9
SAVE_TAG_WEIGHT = 1
AUTHOR_POINTS = {"love": 3, "like": 2, "save": 2, "dislike": -3}
AUTHOR_VALUE_AT = 3          # saldo mínimo para entrar a "fuentes que más valora"
AUTHOR_AVOID_AT = -5         # saldo máximo para entrar a "fuentes que suele descartar"
AUTHOR_AVOID_PENALTY = 15    # puntos que resta el motor de respaldo a esa fuente
EXAMPLES_MAX = 6             # títulos de ejemplo que se guardan por lista
PROMPT_EXAMPLES = 5          # ejemplos que viajan en la pregunta a JEV
PROMPT_LIST_MAX = 8          # temas o fuentes por lista en la pregunta a JEV
PROMPT_AVOID_AUTHORS_MAX = 5

RATING_TO_STATUS = {
    "love": "reading",
    "dislike": "archived",
}


def _item_tags(item: dict) -> list:
    try:
        raw = item.get("topic_tags")
        return json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
    except Exception:
        return []


def _add_example(learned: dict, key: str, item: dict):
    """Guarda el título como ejemplo (sin repetir, solo los últimos EXAMPLES_MAX)."""
    lista = [e for e in (learned.get(key) or []) if not (isinstance(e, dict) and e.get("id") == item.get("id"))]
    lista.append({"id": item.get("id"), "t": (item.get("title") or "")[:120]})
    learned[key] = lista[-EXAMPLES_MAX:]


def _drop_example(learned: dict, key: str, item_id):
    if key in learned:
        learned[key] = [e for e in (learned.get(key) or [])
                        if not (isinstance(e, dict) and e.get("id") == item_id)]


def _learn(learned: dict, rating: str, item: dict, undo: bool = False):
    """Aplica (o deshace) una señal sobre lo aprendido. Modifica `learned` en sitio.

    Un item del buzón corporativo también enseña al motor local, pero su
    remitente y sus etiquetas quedan marcados como privados y nunca viajan a JEV;
    su título jamás se guarda como ejemplo.
    """
    tags = _item_tags(item)
    author = item.get("author")
    privado = is_outlook_item(item)
    sign = -1 if undo else 1

    boosted_authors = set(learned.get("boosted_authors") or [])
    boosted_tags = dict(learned.get("boosted_tags") or {})
    penalized_tags = dict(learned.get("penalized_tags") or {})
    clicked_authors = dict(learned.get("clicked_authors") or {})
    author_scores = dict(learned.get("author_scores") or {})

    if author and rating in AUTHOR_POINTS:
        # Un autor que ya contaba como fijo (antes de author_scores) conserva su +3 de base.
        if author not in author_scores and author in boosted_authors:
            author_scores[author] = 3
        author_scores[author] = int(author_scores.get(author, 0)) + sign * AUTHOR_POINTS[rating]

    if rating in ("love", "like", "save"):
        weight = {"love": 2, "like": 1, "save": SAVE_TAG_WEIGHT}[rating]
        if rating != "save" and author and not undo:
            boosted_authors.add(author)
        for t in tags:
            boosted_tags[t] = max(0, boosted_tags.get(t, 0) + sign * weight)
            # Guardar no toca penalizaciones: así quitarlo deja el perfil exactamente como estaba.
            if rating != "save" and t in penalized_tags:
                penalized_tags[t] = max(0, penalized_tags[t] - 1)
        if not privado:
            if undo:
                _drop_example(learned, "liked_examples", item.get("id"))
            else:
                _add_example(learned, "liked_examples", item)
    elif rating == "click":
        if author:
            clicked_authors[author] = int(clicked_authors.get(author, 0)) + 1
        for t in tags:
            boosted_tags[t] = boosted_tags.get(t, 0) + CLICK_TAG_WEIGHT
            if t in penalized_tags:
                penalized_tags[t] = max(0, penalized_tags[t] - 1)
    elif rating == "dislike":
        boosted_authors.discard(author)
        for t in tags:
            penalized_tags[t] = penalized_tags.get(t, 0) + 2
            if t in boosted_tags:
                boosted_tags[t] = max(0, boosted_tags[t] - 1)
        if not privado:
            _add_example(learned, "disliked_examples", item)

    if privado:
        if author:
            learned["private_authors"] = sorted(set(learned.get("private_authors") or []) | {author})
        learned["private_tags"] = sorted(set(learned.get("private_tags") or []) | set(tags))

    if undo:  # deshacer no deja ceros sueltos: el perfil queda exactamente como estaba
        boosted_tags = {t: w for t, w in boosted_tags.items() if w}
        author_scores = {a: v for a, v in author_scores.items() if v}

    learned.update({
        "boosted_authors": list(boosted_authors),
        "boosted_tags": boosted_tags,
        "penalized_tags": penalized_tags,
        "clicked_authors": clicked_authors,
        "author_scores": author_scores,
    })


def _load_learned(user_id: str) -> dict:
    from app.storage import fetch_profile
    # Se parte del JSON completo para no perder campos que esta función no toca.
    return dict((fetch_profile(user_id).get("learned_preferences") or {}))


def record_feedback(user_id: str, item_id: int, rating: str, comment: str = ""):
    """Guarda la calificación, mueve el item de estado y ajusta las preferencias."""
    from app.storage import (save_user_feedback, get_item_by_id,
                             update_learned_preferences, set_item_status)

    if rating == "click":
        # El clic es la señal más débil: cuenta una sola vez por tarjeta y nunca
        # pisa una calificación explícita (corazón, relevante o pulgar abajo).
        # Tampoco suma sobre una tarjeta guardada: guardar ya dijo más.
        from app.storage import get_user_item
        previo = (get_user_item(user_id, item_id) or {}).get("user_rating") or "none"
        if previo != "none":
            return

    save_user_feedback(user_id, item_id, rating, comment)

    new_status = RATING_TO_STATUS.get(rating)
    if new_status:
        set_item_status(user_id, item_id, new_status)

    item = get_item_by_id(item_id)
    if not item:
        return

    learned = _load_learned(user_id)
    _learn(learned, rating, item)
    update_learned_preferences(user_id, learned)


def record_save(user_id: str, item_id: int):
    """Guardar con el botón es una señal positiva. No pisa una calificación
    explícita (corazón, relevante, pulgar abajo) ni cuenta dos veces."""
    from app.storage import get_user_item, get_item_by_id, save_user_feedback, update_learned_preferences

    previo = (get_user_item(user_id, item_id) or {}).get("user_rating") or "none"
    if previo not in ("none", "click"):
        return
    item = get_item_by_id(item_id)
    if not item:
        return
    save_user_feedback(user_id, item_id, "save", "")
    learned = _load_learned(user_id)
    _learn(learned, "save", item)
    update_learned_preferences(user_id, learned)


def record_unsave(user_id: str, item_id: int):
    """Quitar de guardados deshace lo que sumó el botón Guardar, y solo eso:
    un corazón o un relevante ya dados se quedan."""
    from app.storage import get_user_item, get_item_by_id, save_user_feedback, update_learned_preferences

    if ((get_user_item(user_id, item_id) or {}).get("user_rating") or "none") != "save":
        return
    item = get_item_by_id(item_id)
    save_user_feedback(user_id, item_id, "none", "")
    if not item:
        return
    learned = _load_learned(user_id)
    _learn(learned, "save", item, undo=True)
    update_learned_preferences(user_id, learned)
