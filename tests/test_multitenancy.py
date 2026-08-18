"""Aislamiento entre cuentas.

La línea de llegada del ciclo: dos usuarios con sus propias fuentes y su propio
historial de lectura, sin que uno vea nada del otro. Esta prueba es la que
decide si T1 está terminado.

    python3 tests/test_multitenancy.py
"""

import os
import sys
import tempfile

os.environ["FEED_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_focusfeed.db")
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db  # noqa: E402
from app import storage  # noqa: E402
from app.ai_engine import score_for_user, record_feedback  # noqa: E402

ANA = "11111111-1111-1111-1111-111111111111"
BRUNO = "22222222-2222-2222-2222-222222222222"

fallos = []


def check(nombre, condicion, detalle=""):
    estado = "ok  " if condicion else "FALLA"
    print(f"  [{estado}] {nombre}" + (f" · {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def sembrar():
    feeds = storage.batch_create_feeds([
        {"title": "Canal IA", "feed_url": "https://ejemplo.com/ia", "feed_type": "rss"},
        {"title": "Canal Cine", "feed_url": "https://ejemplo.com/cine", "feed_type": "rss"},
        {"title": "Canal Comun", "feed_url": "https://ejemplo.com/comun", "feed_type": "rss"},
    ])
    ids = {f["feed_url"]: f["id"] for f in feeds}
    ia, cine, comun = ids["https://ejemplo.com/ia"], ids["https://ejemplo.com/cine"], ids["https://ejemplo.com/comun"]

    # Ana sigue IA y el compartido. Bruno sigue Cine y el compartido.
    storage.subscribe(ANA, ia, "IA")
    storage.subscribe(ANA, comun, "General")
    storage.subscribe(BRUNO, cine, "Cine")
    storage.subscribe(BRUNO, comun, "General")

    items = []
    for feed_id, prefijo, n in ((ia, "ia", 4), (cine, "cine", 4), (comun, "comun", 3)):
        for i in range(n):
            items.append({
                "feed_id": feed_id, "guid": f"{prefijo}-{i}",
                "title": f"Articulo {prefijo} {i}", "url": f"https://ejemplo.com/{prefijo}/{i}",
                "author": f"Autor {prefijo}", "published_at": f"2026-08-1{i}T10:00:00",
                "content_type": "article", "raw_content": "Texto sobre agentes de IA y conocimiento personal.",
            })
    storage.batch_save_items(items)
    return ia, cine, comun


def main():
    init_db()
    ia, cine, comun = sembrar()

    print("\n1. Cada quien ve solo sus suscripciones")
    ana = storage.fetch_items(ANA)["items"]
    bruno = storage.fetch_items(BRUNO)["items"]
    titulos_ana = {i["title"] for i in ana}
    titulos_bruno = {i["title"] for i in bruno}

    check("Ana ve 7 items (4 de IA + 3 comunes)", len(ana) == 7, f"vio {len(ana)}")
    check("Bruno ve 7 items (4 de Cine + 3 comunes)", len(bruno) == 7, f"vio {len(bruno)}")
    check("Ana no ve nada de Cine", not any("cine" in t for t in titulos_ana))
    check("Bruno no ve nada de IA", not any(" ia " in f" {t} " for t in titulos_bruno))

    print("\n2. El corpus compartido se guarda una sola vez")
    comunes_ana = [i for i in ana if i["feed_id"] == comun]
    comunes_bruno = [i for i in bruno if i["feed_id"] == comun]
    mismos_ids = {i["id"] for i in comunes_ana} == {i["id"] for i in comunes_bruno}
    check("Los dos leen las MISMAS filas del feed común", mismos_ids,
          "un solo análisis sirve a los dos")

    print("\n3. Marcar como visto no se filtra")
    item_compartido = comunes_ana[0]
    storage.set_item_status(ANA, item_compartido["id"], "archived")

    ana_2 = storage.fetch_items(ANA)["items"]
    bruno_2 = storage.fetch_items(BRUNO)["items"]
    check("El item desaparece del feed de Ana",
          item_compartido["id"] not in {i["id"] for i in ana_2})
    check("El MISMO item sigue en el feed de Bruno",
          item_compartido["id"] in {i["id"] for i in bruno_2})

    print("\n4. Guardados y contadores por usuario")
    storage.set_item_status(ANA, comunes_ana[1]["id"], "reading")
    c_ana = storage.fetch_items(ANA)["counts"]
    c_bruno = storage.fetch_items(BRUNO)["counts"]
    check("Ana tiene 1 guardado", c_ana.get("reading", 0) == 1, str(c_ana))
    check("Bruno tiene 0 guardados", c_bruno.get("reading", 0) == 0, str(c_bruno))
    check("Ana tiene 1 archivado", c_ana.get("archived", 0) == 1)
    check("Bruno tiene 0 archivados", c_bruno.get("archived", 0) == 0)

    print("\n5. Perfil y preferencias aprendidas por usuario")
    storage.save_profile_data(ANA, ["Inteligencia artificial", "Agentes"], "Rigor tecnico")
    storage.save_profile_data(BRUNO, ["Cine de autor"], "Narrativa visual")
    check("El perfil de Ana no contamina el de Bruno",
          storage.fetch_profile(BRUNO)["focus_topics"] == ["Cine de autor"])

    record_feedback(ANA, comunes_ana[2]["id"], "love")
    aprendido_bruno = storage.fetch_profile(BRUNO).get("learned_preferences", {})
    check("El feedback de Ana no entrena el perfil de Bruno",
          not aprendido_bruno.get("boosted_authors"))
    check("El feedback de Ana sí entrena el suyo",
          bool(storage.fetch_profile(ANA)["learned_preferences"].get("boosted_authors")))

    print("\n6. El score es personal sobre contenido compartido")
    item = comunes_bruno[0]
    s_ana = score_for_user(item, storage.fetch_profile(ANA))["relevance_score"]
    s_bruno = score_for_user(item, storage.fetch_profile(BRUNO))["relevance_score"]
    check("El mismo artículo puntúa distinto para cada perfil", s_ana != s_bruno,
          f"Ana {s_ana} vs Bruno {s_bruno}")

    print("\n7. Darse de baja no borra el feed para los demás")
    storage.unsubscribe(ANA, comun)
    check("Ana ya no ve el feed común", not [i for i in storage.fetch_items(ANA)["items"]
                                             if i["feed_id"] == comun])
    check("Bruno sigue viendo el feed común",
          len([i for i in storage.fetch_items(BRUNO)["items"] if i["feed_id"] == comun]) == 3)

    print("\n8. Registro de costo")
    storage.log_ai_usage(ANA, item["id"], "modelo-prueba", 1000, 200, 0.0012)
    storage.log_ai_usage(BRUNO, item["id"], "modelo-prueba", 500, 100, 0.0006)
    check("El costo de Ana es solo suyo",
          storage.usage_summary(ANA)["total_cost_usd"] == 0.0012,
          str(storage.usage_summary(ANA)))
    check("El resumen global suma los dos usuarios",
          storage.usage_summary()["active_users"] == 2)

    print("\n9. Calificar mueve el item de estado (fix 18-ago)")
    feed_ana = [i for i in storage.fetch_items(ANA)["items"]]
    para_dislike = feed_ana[0]["id"]
    para_love = feed_ana[1]["id"]

    record_feedback(ANA, para_dislike, "dislike")
    tras_dislike = {i["id"] for i in storage.fetch_items(ANA)["items"]}
    check("El dislike saca el item del feed de verdad", para_dislike not in tras_dislike)
    check("Y queda archivado en la base, no solo en pantalla",
          storage.get_user_item(ANA, para_dislike)["status"] == "archived")

    record_feedback(ANA, para_love, "love")
    guardados = {i["id"] for i in storage.fetch_items(ANA, tab="reading")["items"]}
    check("El corazón guarda automático", para_love in guardados)
    check("Y el item sigue visible en el feed", para_love in
          {i["id"] for i in storage.fetch_items(ANA)["items"]})

    otro = feed_ana[2]["id"]
    record_feedback(ANA, otro, "like")
    ui_like = storage.get_user_item(ANA, otro)
    check("El pulgar arriba entrena pero no mueve el item",
          ui_like["status"] == "inbox" and ui_like["user_rating"] == "like")

    check("Nada de esto tocó a Bruno",
          not storage.fetch_items(BRUNO, tab="reading")["items"]
          and not storage.fetch_items(BRUNO, tab="archived")["items"])

    print("\n" + "=" * 58)
    if fallos:
        print(f"FALLARON {len(fallos)} verificaciones:")
        for f in fallos:
            print(f"  - {f}")
        sys.exit(1)
    print("Aislamiento verificado. T1 cumple su línea de llegada.")


if __name__ == "__main__":
    main()
