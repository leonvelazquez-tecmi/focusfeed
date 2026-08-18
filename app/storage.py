"""Acceso a datos con corpus global y estado por usuario.

Regla que rige este archivo: `feeds` e `items` son de todos. `subscriptions`,
`user_items`, `user_profile` y `ai_usage` son de una persona. Cualquier función
que lea o escriba algo personal recibe `user_id` como primer argumento.
"""

import json
from datetime import datetime

from app.db import (
    is_supabase, supabase_request,
    get_db_connection, get_supabase_debug_info
)


def _now():
    return datetime.utcnow().isoformat()


def _rpc(name: str, params: dict):
    return supabase_request(f"rpc/{name}", method="POST", data=params)


# ==========================================================
# Feed de lectura
# ==========================================================

def diversify_by_feed(items: list) -> list:
    """Evita rachas del mismo canal. Greedy: respeta el orden de entrada
    (score o fecha) pero nunca pone dos items seguidos de la misma fuente
    mientras exista alternativa."""
    remaining = list(items)
    out = []
    last_feed = None
    while remaining:
        idx = 0
        for i, it in enumerate(remaining):
            if it.get("feed_id") != last_feed:
                idx = i
                break
        picked = remaining.pop(idx)
        out.append(picked)
        last_feed = picked.get("feed_id")
    return out


def fetch_items(user_id: str, tab="curated", type_filter="all", limit=120):
    if is_supabase():
        res = _rpc("get_user_feed", {
            "p_user_id": user_id, "p_tab": tab,
            "p_type": type_filter, "p_limit": limit
        })
        items = res if isinstance(res, list) else []
        debug = get_supabase_debug_info()
        if not isinstance(res, list):
            debug["items_query_error"] = res

        counts = {}
        counts_res = _rpc("get_user_counts", {"p_user_id": user_id})
        if isinstance(counts_res, list):
            for row in counts_res:
                counts[row.get("status", "inbox")] = row.get("cnt", 0)
    else:
        conn = get_db_connection()
        cursor = conn.cursor()

        base = """
        SELECT i.id, i.feed_id, i.guid, i.title, i.url, i.author, i.published_at,
               i.content_type, i.video_id, i.thumbnail_url, i.raw_content,
               i.summary_tldr, i.key_takeaways, i.topic_tags, i.ai_processed,
               COALESCE(ui.status, 'inbox')     AS status,
               ui.relevance_score               AS relevance_score,
               ui.curator_note                  AS curator_note,
               COALESCE(ui.user_rating, 'none') AS user_rating,
               ui.user_feedback_comment         AS user_feedback_comment
          FROM items i
          JOIN subscriptions s ON s.feed_id = i.feed_id AND s.user_id = ?
          LEFT JOIN user_items ui ON ui.item_id = i.id AND ui.user_id = ?
         WHERE 1=1
        """
        params = [user_id, user_id]

        if tab == "curated":
            base += " AND COALESCE(ui.status,'inbox') != 'archived'"
        elif tab == "inbox":
            base += " AND COALESCE(ui.status,'inbox') = 'inbox'"
        elif tab == "reading":
            base += " AND ui.status = 'reading'"
        elif tab == "archived":
            base += " AND ui.status = 'archived'"

        if type_filter != "all":
            base += " AND i.content_type = ?"
            params.append(type_filter)

        if tab == "curated":
            base += " ORDER BY COALESCE(ui.relevance_score, 70) DESC, i.published_at DESC"
        else:
            base += " ORDER BY i.published_at DESC"
        base += " LIMIT ?"
        params.append(limit)

        cursor.execute(base, params)
        items = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
        SELECT COALESCE(ui.status,'inbox') AS status, COUNT(*) AS cnt
          FROM items i
          JOIN subscriptions s ON s.feed_id = i.feed_id AND s.user_id = ?
          LEFT JOIN user_items ui ON ui.item_id = i.id AND ui.user_id = ?
         GROUP BY 1
        """, (user_id, user_id))
        counts = {r["status"]: r["cnt"] for r in cursor.fetchall()}
        conn.close()
        debug = get_supabase_debug_info()

    if tab in ("curated", "inbox"):
        items = diversify_by_feed(items)

    return {"items": items, "counts": counts, "debug": debug}


# ==========================================================
# Estado personal
# ==========================================================

def _upsert_user_item(user_id: str, item_id: int, fields: dict):
    if is_supabase():
        payload = {"user_id": user_id, "item_id": int(item_id)}
        payload.update(fields)
        res = supabase_request(
            "user_items?on_conflict=user_id,item_id", method="POST", data=payload,
            headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"}
        )
        if not isinstance(res, list):
            err = res.get("message") if isinstance(res, dict) else str(res)
            raise RuntimeError(f"Supabase rechazo el estado del item {item_id}: {err}")
        return res

    conn = get_db_connection()
    cursor = conn.cursor()
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields)
    cursor.execute(
        f"""INSERT INTO user_items (user_id, item_id, {cols})
            VALUES (?, ?, {placeholders})
            ON CONFLICT(user_id, item_id) DO UPDATE SET {updates}""",
        [user_id, int(item_id)] + list(fields.values())
    )
    conn.commit()
    conn.close()


def set_item_status(user_id: str, item_id: int, new_status: str):
    return _upsert_user_item(user_id, item_id, {"status": new_status})


def bulk_set_status(user_id: str, item_ids: list, new_status: str):
    """Cambia el status de varios items del usuario en una sola operación."""
    ids = []
    for i in item_ids:
        try:
            ids.append(int(i))
        except (TypeError, ValueError):
            continue
    if not ids:
        return 0

    if is_supabase():
        payload = [{"user_id": user_id, "item_id": i, "status": new_status} for i in ids]
        res = supabase_request(
            "user_items?on_conflict=user_id,item_id", method="POST", data=payload,
            headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"}
        )
        if isinstance(res, list):
            return len(res)
        err = res.get("message") if isinstance(res, dict) else str(res)
        raise RuntimeError(f"Supabase rechazo el cambio masivo de status: {err}")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.executemany(
        """INSERT INTO user_items (user_id, item_id, status) VALUES (?, ?, ?)
           ON CONFLICT(user_id, item_id) DO UPDATE SET status=excluded.status""",
        [(user_id, i, new_status) for i in ids]
    )
    conn.commit()
    conn.close()
    return len(ids)


def save_user_feedback(user_id: str, item_id: int, rating: str, comment: str = ""):
    return _upsert_user_item(user_id, item_id, {
        "user_rating": rating,
        "user_feedback_comment": comment,
        "feedback_at": _now()
    })


def save_user_score(user_id: str, item_id: int, relevance_score: int, curator_note: str = ""):
    """Score personal. Es lo único que cambia entre usuarios para el mismo item."""
    return _upsert_user_item(user_id, item_id, {
        "relevance_score": int(relevance_score),
        "curator_note": curator_note,
        "scored_at": _now()
    })


def get_user_item(user_id: str, item_id: int):
    if is_supabase():
        res = supabase_request(
            f"user_items?user_id=eq.{user_id}&item_id=eq.{int(item_id)}&select=*&limit=1")
        return res[0] if isinstance(res, list) and res else None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_items WHERE user_id = ? AND item_id = ?",
                   (user_id, int(item_id)))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ==========================================================
# Corpus global de items
# ==========================================================

def get_item_by_id(item_id: int):
    if is_supabase():
        res = supabase_request(f"items?id=eq.{int(item_id)}&select=*&limit=1")
        if isinstance(res, list):
            return res[0] if res else None
        print(f"get_item_by_id({item_id}): Supabase rechazo la consulta")
        return None
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items WHERE id = ?", (int(item_id),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_item_ids(limit: int = 10):
    """Items del corpus que nadie ha analizado. El resumen es universal:
    se calcula una vez y sirve para todos los suscriptores."""
    if is_supabase():
        res = _rpc("get_unanalyzed_items", {"p_limit": limit})
        return [r["id"] for r in res] if isinstance(res, list) else []
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM items WHERE COALESCE(ai_processed,0) = 0 ORDER BY published_at DESC LIMIT ?",
        (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [r["id"] for r in rows]


def save_item_analysis(item_id: int, analysis: dict):
    """Guarda solo lo universal. El score y la nota del curador son personales
    y van por save_user_score()."""
    data = {
        "summary_tldr": analysis.get("summary_tldr", ""),
        "key_takeaways": json.dumps(analysis.get("key_takeaways", []), ensure_ascii=False),
        "topic_tags": json.dumps(analysis.get("topic_tags", []), ensure_ascii=False),
        "ai_processed_at": _now(),
        "ai_processed": 1
    }
    if is_supabase():
        res = supabase_request(f"items?id=eq.{int(item_id)}", method="PATCH", data=data)
        if not isinstance(res, list):
            err = res.get("message") if isinstance(res, dict) else str(res)
            raise RuntimeError(f"Supabase rechazo el analisis del item {item_id}: {err}")
        return res
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE items SET summary_tldr = ?, key_takeaways = ?, topic_tags = ?,
                         ai_processed = 1, ai_processed_at = ?
         WHERE id = ?
    """, (data["summary_tldr"], data["key_takeaways"], data["topic_tags"],
          data["ai_processed_at"], int(item_id)))
    conn.commit()
    conn.close()


def get_existing_guids(feed_ids: list) -> set:
    """Guids ya guardados para esos feeds. Se consulta por feed_id (enteros)
    para no tener que urlencodear guids que son URLs completas."""
    clean_ids = sorted({int(f) for f in feed_ids if f is not None})
    if not clean_ids:
        return set()
    if is_supabase():
        id_list = ",".join(str(i) for i in clean_ids)
        res = supabase_request(f"items?select=guid&feed_id=in.({id_list})&limit=5000")
        if isinstance(res, list):
            return {r.get("guid") for r in res if r.get("guid")}
        return set()
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in clean_ids)
    cursor.execute(f"SELECT guid FROM items WHERE feed_id IN ({placeholders})", clean_ids)
    rows = cursor.fetchall()
    conn.close()
    return {r["guid"] for r in rows}


def batch_save_items(items: list):
    """Inserta solo items nuevos en el corpus global.

    Nunca actualiza los existentes: hacerlo con upsert resucitaba lo marcado
    como visto y pisaba el analisis de IA con los valores por defecto.
    """
    if not items:
        return 0

    by_guid = {}
    for item in items:
        by_guid[item["guid"]] = item

    already = get_existing_guids([i.get("feed_id") for i in by_guid.values()])
    fresh = [i for g, i in by_guid.items() if g not in already]
    if not fresh:
        return 0

    if is_supabase():
        clean_batch = [{
            "feed_id": item.get("feed_id"),
            "guid": item["guid"],
            "title": item["title"],
            "url": item["url"],
            "author": item.get("author", "Autor"),
            "published_at": item.get("published_at", _now()),
            "content_type": item.get("content_type", "article"),
            "video_id": item.get("video_id"),
            "thumbnail_url": item.get("thumbnail_url", ""),
            "raw_content": (item.get("raw_content") or "")[:2000],
            "transcript": (item.get("transcript") or "")[:2000],
            "summary_tldr": item.get("summary_tldr", ""),
            "key_takeaways": item.get("key_takeaways", "[]"),
            "topic_tags": item.get("topic_tags", "[]"),
        } for item in fresh]

        res = supabase_request(
            "items?on_conflict=guid", method="POST", data=clean_batch,
            headers_extra={"Prefer": "resolution=ignore-duplicates,return=representation"}
        )
        if isinstance(res, list):
            return len(res)
        err = res.get("message") if isinstance(res, dict) else str(res)
        raise RuntimeError(f"Supabase rechazo la insercion de items: {err}")

    from app.ingestion import save_items_to_db
    return save_items_to_db(fresh)


# ==========================================================
# Feeds y suscripciones
# ==========================================================

def fetch_feeds(user_id: str):
    """Feeds a los que este usuario está suscrito, con su categoría personal."""
    if is_supabase():
        res = supabase_request(
            f"subscriptions?user_id=eq.{user_id}&select=custom_category,feeds(*)&order=custom_category.asc")
        if not isinstance(res, list):
            return []
        feeds = []
        for row in res:
            feed = row.get("feeds") or {}
            if not feed:
                continue
            feed["custom_category"] = row.get("custom_category") or "General"
            feeds.append(feed)
        feeds.sort(key=lambda f: ((f.get("custom_category") or ""), (f.get("title") or "")))
        return feeds

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT f.*, s.custom_category AS custom_category
          FROM feeds f
          JOIN subscriptions s ON s.feed_id = f.id
         WHERE s.user_id = ?
         ORDER BY s.custom_category ASC, f.title ASC
    """, (user_id,))
    feeds = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return feeds


def fetch_all_active_feeds():
    """Todos los feeds del corpus. Lo usa el cron: se sincroniza una vez,
    sirve a todos los suscriptores."""
    if is_supabase():
        res = supabase_request("feeds?select=*&order=id.asc")
        return res if isinstance(res, list) else []
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM feeds ORDER BY id ASC")
    feeds = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return feeds


def batch_create_feeds(feeds_list: list):
    """Crea o actualiza feeds en el corpus global. Devuelve los registros con
    su id real, porque el id se necesita para enlazar items y suscripciones."""
    if not feeds_list:
        return []

    if is_supabase():
        clean_batch = [{
            "title": f["title"],
            "feed_url": f["feed_url"],
            "site_url": f.get("site_url", ""),
            "feed_type": f.get("feed_type", "rss"),
            "channel_id": f.get("channel_id"),
            "is_active": True
        } for f in feeds_list]

        res = supabase_request(
            "feeds?on_conflict=feed_url", method="POST", data=clean_batch,
            headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})
        if isinstance(res, list):
            return res
        res_standard = supabase_request("feeds", method="POST", data=clean_batch)
        if isinstance(res_standard, list):
            return res_standard
        err = res.get("message") if isinstance(res, dict) else str(res)
        raise RuntimeError(f"Supabase rechazo la creacion de feeds: {err}")

    conn = get_db_connection()
    cursor = conn.cursor()
    created = []
    for f in feeds_list:
        try:
            cursor.execute("""
                INSERT INTO feeds (title, feed_url, site_url, feed_type, channel_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(feed_url) DO UPDATE SET title=excluded.title
            """, (f["title"], f["feed_url"], f.get("site_url", ""),
                  f.get("feed_type", "rss"), f.get("channel_id")))
            row = cursor.execute("SELECT id FROM feeds WHERE feed_url = ?",
                                 (f["feed_url"],)).fetchone()
            created.append({"id": row["id"], "feed_url": f["feed_url"]})
        except Exception as e:
            print(f"Error creando feed local '{f.get('title')}': {e}")
            continue
    conn.commit()
    conn.close()
    return created


def subscribe(user_id: str, feed_id: int, category: str = "General"):
    if is_supabase():
        res = supabase_request(
            "subscriptions?on_conflict=user_id,feed_id", method="POST",
            data={"user_id": user_id, "feed_id": int(feed_id), "custom_category": category},
            headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})
        if not isinstance(res, list):
            err = res.get("message") if isinstance(res, dict) else str(res)
            raise RuntimeError(f"Supabase rechazo la suscripcion: {err}")
        return res
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO subscriptions (user_id, feed_id, custom_category) VALUES (?, ?, ?)
        ON CONFLICT(user_id, feed_id) DO UPDATE SET custom_category=excluded.custom_category
    """, (user_id, int(feed_id), category))
    conn.commit()
    conn.close()


def create_or_update_feed(user_id: str, title: str, feed_url: str,
                          feed_type: str = "youtube", category: str = "General",
                          site_url: str = ""):
    created = batch_create_feeds([{
        "title": title, "feed_url": feed_url,
        "feed_type": feed_type, "site_url": site_url
    }])
    if not created:
        raise RuntimeError(f"No se pudo crear/actualizar el feed: {feed_url}")
    feed_id = created[0]["id"]
    subscribe(user_id, feed_id, category)
    return feed_id


def unsubscribe(user_id: str, feed_id: int):
    """Quita la suscripción. El feed y sus items siguen en el corpus para
    los demás usuarios."""
    if is_supabase():
        supabase_request(
            f"subscriptions?user_id=eq.{user_id}&feed_id=eq.{int(feed_id)}", method="DELETE")
        supabase_request(
            f"user_items?user_id=eq.{user_id}&item_id=in.(select id from items where feed_id={int(feed_id)})",
            method="DELETE")
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM subscriptions WHERE user_id = ? AND feed_id = ?",
                   (user_id, int(feed_id)))
    cursor.execute("""DELETE FROM user_items WHERE user_id = ?
                      AND item_id IN (SELECT id FROM items WHERE feed_id = ?)""",
                   (user_id, int(feed_id)))
    conn.commit()
    conn.close()


# ==========================================================
# Perfil
# ==========================================================

DEFAULT_TOPICS = [
    "Inteligencia Artificial aplicada y sistemas agénticos",
    "Gestión del conocimiento personal (PKM, Obsidian, Zettelkasten)",
    "Estrategia y modelos de negocio",
]
DEFAULT_CRITERIA = "Prioriza profundidad conceptual, rigor analítico y valor duradero."


def _empty_profile(user_id):
    return {
        "user_id": user_id,
        "user_name": "",
        "focus_topics": DEFAULT_TOPICS,
        "system_prompt_criteria": DEFAULT_CRITERIA,
        "learned_preferences": {"boosted_authors": [], "boosted_tags": {}, "penalized_tags": {}},
        "plan": "free",
    }


def _parse_profile_row(row, user_id):
    def _load(val, fallback):
        if isinstance(val, (list, dict)):
            return val
        try:
            return json.loads(val) if val else fallback
        except Exception:
            return fallback

    return {
        "user_id": user_id,
        "user_name": row.get("user_name") or "",
        "focus_topics": _load(row.get("focus_topics"), DEFAULT_TOPICS) or DEFAULT_TOPICS,
        "system_prompt_criteria": row.get("system_prompt_criteria") or DEFAULT_CRITERIA,
        "learned_preferences": _load(row.get("learned_preferences"), {}) or
                               {"boosted_authors": [], "boosted_tags": {}, "penalized_tags": {}},
        "plan": row.get("plan") or "free",
    }


def fetch_profile(user_id: str):
    if is_supabase():
        res = supabase_request(f"user_profile?user_id=eq.{user_id}&select=*&limit=1")
        if isinstance(res, list) and res:
            return _parse_profile_row(res[0], user_id)
        return _empty_profile(user_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_profile WHERE user_id = ? LIMIT 1", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return _parse_profile_row(dict(row), user_id) if row else _empty_profile(user_id)


def save_profile_data(user_id: str, focus_topics: list, criteria: str = ""):
    topics_json = json.dumps(focus_topics, ensure_ascii=False)
    if is_supabase():
        supabase_request(
            "user_profile?on_conflict=user_id", method="POST",
            data={"user_id": user_id, "focus_topics": topics_json,
                  "system_prompt_criteria": criteria, "updated_at": _now()},
            headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_profile (user_id, focus_topics, system_prompt_criteria, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET focus_topics=excluded.focus_topics,
            system_prompt_criteria=excluded.system_prompt_criteria, updated_at=excluded.updated_at
    """, (user_id, topics_json, criteria, _now()))
    conn.commit()
    conn.close()


def update_learned_preferences(user_id: str, learned: dict):
    learned_json = json.dumps(learned, ensure_ascii=False)
    if is_supabase():
        return supabase_request(
            "user_profile?on_conflict=user_id", method="POST",
            data={"user_id": user_id, "learned_preferences": learned_json, "updated_at": _now()},
            headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_profile (user_id, learned_preferences, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET learned_preferences=excluded.learned_preferences,
            updated_at=excluded.updated_at
    """, (user_id, learned_json, _now()))
    conn.commit()
    conn.close()


# ==========================================================
# Costo de IA
# ==========================================================

def log_ai_usage(user_id, item_id, model: str, input_tokens: int = 0,
                 output_tokens: int = 0, cost_usd: float = 0.0,
                 kind: str = "item_analysis"):
    """Sin este registro no se puede fijar precio. Ver context/unit-economics.md."""
    row = {
        "user_id": user_id, "item_id": int(item_id) if item_id else None,
        "kind": kind, "model": model,
        "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
        "cost_usd": round(float(cost_usd), 6)
    }
    try:
        if is_supabase():
            supabase_request("ai_usage", method="POST", data=row)
            return
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""INSERT INTO ai_usage (user_id, item_id, kind, model,
                          input_tokens, output_tokens, cost_usd)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""",
                       (row["user_id"], row["item_id"], row["kind"], row["model"],
                        row["input_tokens"], row["output_tokens"], row["cost_usd"]))
        conn.commit()
        conn.close()
    except Exception as e:
        # El registro de costo nunca debe tumbar el análisis
        print(f"log_ai_usage fallo: {e}")


def usage_summary(user_id: str = None, days: int = 30):
    """Costo acumulado. Alimenta la rutina diaria de costo."""
    if is_supabase():
        query = "ai_usage?select=user_id,cost_usd,input_tokens,output_tokens"
        if user_id:
            query += f"&user_id=eq.{user_id}"
        res = supabase_request(query + "&limit=10000")
        rows = res if isinstance(res, list) else []
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        if user_id:
            cursor.execute("""SELECT user_id, cost_usd, input_tokens, output_tokens
                              FROM ai_usage WHERE user_id = ?""", (user_id,))
        else:
            cursor.execute("SELECT user_id, cost_usd, input_tokens, output_tokens FROM ai_usage")
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

    total_cost = sum(float(r.get("cost_usd") or 0) for r in rows)
    users = {r.get("user_id") for r in rows if r.get("user_id")}
    return {
        "calls": len(rows),
        "total_cost_usd": round(total_cost, 4),
        "active_users": len(users),
        "cost_per_user_usd": round(total_cost / len(users), 4) if users else 0.0,
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
    }


# ==========================================================
# Semilla
# ==========================================================

SEED_FEEDS = [
    {"title": "Andrej Karpathy", "feed_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCXUPKJOtpqmgUOxw8p9n6Tw", "feed_type": "youtube", "custom_category": "IA & Agentes"},
    {"title": "Every", "feed_url": "https://every.to/feed.xml", "feed_type": "rss", "custom_category": "IA & Agentes"},
    {"title": "Stratechery", "feed_url": "https://stratechery.com/feed/", "feed_type": "rss", "custom_category": "Estrategia"},
]


def ensure_seed_if_empty(user_id: str):
    """Le da fuentes iniciales a una cuenta nueva."""
    if fetch_feeds(user_id):
        return
    created = batch_create_feeds(SEED_FEEDS)
    by_url = {c["feed_url"]: c["id"] for c in created}
    for f in SEED_FEEDS:
        fid = by_url.get(f["feed_url"])
        if fid:
            subscribe(user_id, fid, f.get("custom_category", "General"))
