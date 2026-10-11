#!/usr/bin/env python3
"""Spike T9: JEV (TypeSafe vía OpenRouter) como score personal de "Para ti".

Le pregunta a JEV, por item, si le interesa a este lector según su perfil, y
compara contra el score heurístico actual (score_for_user). No escribe en la
base: solo un CSV y un resumen de costo y latencia.

    python3 routines/jev_scoring_spike.py --n 100 --out /ruta/salida.csv

Llave: OPENROUTER_API_KEY o ~/.config/openrouter/.env. Requests con ZDR y
data_collection=deny.
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app.db import get_db_connection  # noqa: E402
from app.storage import DEFAULT_TOPICS, DEFAULT_CRITERIA  # noqa: E402
from app.ai_engine import score_for_user  # noqa: E402

URL = "https://openrouter.ai/api/alpha/decisions"
MODELO = "typesafe/jev-1.13"
ENV = Path.home() / ".config/openrouter/.env"


def llave():
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def pregunta(profile):
    temas = "; ".join(profile["focus_topics"])
    return {"interesa": {
        "type": "noul",
        "instructions": "¿Este contenido vale el tiempo de este lector profesional?",
        "criteria": {
            "true": (f"Trata de sus temas ({temas}) con sustancia. {profile['system_prompt_criteria']}"),
            "false": ("Es entretenimiento, noticia efímera, clickbait, reseña de ocio o un tema "
                      "ajeno a sus intereses, aunque venga de una fuente que sigue."),
        },
    }}


def jev(key, item, questions):
    body = {
        "model": MODELO,
        "state": {"titulo": item["title"], "fuente": item["author"],
                  "tipo": item["content_type"], "extracto": (item["raw_content"] or "")[:800]},
        "questions": questions,
        "provider": {"zdr": True, "data_collection": "deny"},
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    err = None
    for _ in range(3):
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            return d["answers"]["interesa"]["noul"], d.get("usage", {}).get("cost", 0.0), time.time() - t
        except (urllib.error.URLError, KeyError, ValueError) as e:
            err = e
            time.sleep(2)
    raise RuntimeError(f"JEV falló 3 veces: {err}")


def main():
    args = sys.argv[1:]
    n = int(args[args.index("--n") + 1]) if "--n" in args else 100
    out = args[args.index("--out") + 1] if "--out" in args else "jev_scoring_spike.csv"
    key = llave()
    if not key:
        sys.exit(f"Sin llave: crea {ENV} con OPENROUTER_API_KEY=...")

    conn = get_db_connection()
    items = [dict(r) for r in conn.execute(
        "SELECT id, title, author, content_type, raw_content, topic_tags, summary_tldr "
        "FROM items ORDER BY published_at DESC LIMIT ?", (n,)).fetchall()]
    conn.close()

    profile = {"focus_topics": DEFAULT_TOPICS, "system_prompt_criteria": DEFAULT_CRITERIA,
               "learned_preferences": {}}
    questions = pregunta(profile)

    # Smoke test de una llamada antes de la corrida: si el esquema falla, se ve aquí.
    p, c, s = jev(key, items[0], questions)
    print(f"smoke test ok: p={p:.3f} costo={c} latencia={s:.2f}s")

    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda i: jev(key, i, questions), items))
    total = time.time() - t0

    filas = []
    for item, (p, c, s) in zip(items, res):
        filas.append({"p_jev": round(p, 3), "score_heuristico": score_for_user(item, profile)["relevance_score"],
                      "costo": c, "latencia": round(s, 2), "fuente": item["author"], "titulo": item["title"]})
    filas.sort(key=lambda f: -f["p_jev"])
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    costo = sum(f["costo"] for f in filas)
    lat = sorted(f["latencia"] for f in filas)
    print(f"{len(filas)} items en {total:.1f}s · costo USD {costo:.5f} "
          f"(USD {costo / len(filas) * 1000:.3f} por 1,000) · latencia mediana {lat[len(lat) // 2]:.2f}s")
    print(f"pasan p>=0.5: {sum(f['p_jev'] >= 0.5 for f in filas)} de {len(filas)}")
    print("\nTop 8 según JEV:")
    for f in filas[:8]:
        print(f"  {f['p_jev']:.2f} (heur {f['score_heuristico']}) {f['fuente'][:22]:22} {f['titulo'][:70]}")
    print("\nFondo 5 según JEV:")
    for f in filas[-5:]:
        print(f"  {f['p_jev']:.2f} (heur {f['score_heuristico']}) {f['fuente'][:22]:22} {f['titulo'][:70]}")
    print(f"\nCSV: {out}")


if __name__ == "__main__":
    main()
