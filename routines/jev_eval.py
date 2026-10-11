#!/usr/bin/env python3
"""Mide JEV contra las calificaciones reales (love/like/dislike) de un usuario.

Solo lectura. Compara el AUC de JEV con el del score heurístico actual: la
probabilidad de que un item que te gustó quede por encima de uno que descartaste.

    python3 routines/jev_eval.py            # usa Supabase si hay variables, si no SQLite
"""

import os
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app.db import supabase_request  # noqa: E402
from app.storage import fetch_profile  # noqa: E402
from app.ai_engine import score_for_user  # noqa: E402
from routines.jev_scoring_spike import jev, llave, pregunta  # noqa: E402


def auc(pos, neg):
    pares = [(p > n) + 0.5 * (p == n) for p in pos for n in neg]
    return sum(pares) / len(pares)


def main():
    key = llave()
    if not key:
        sys.exit("Sin OPENROUTER_API_KEY")
    filas = supabase_request("user_items?user_rating=neq.none&select=user_id,item_id,user_rating,relevance_score&limit=2000")
    user_id = filas[0]["user_id"]
    profile = fetch_profile(user_id)
    ids = ",".join(str(f["item_id"]) for f in filas)
    items = {i["id"]: i for i in supabase_request(
        f"items?id=in.({ids})&select=id,title,author,content_type,raw_content,topic_tags,summary_tldr")}
    q = pregunta(profile)
    datos = [(f, items[f["item_id"]]) for f in filas if f["item_id"] in items]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda d: jev(key, d[1], q), datos))

    pos_j, neg_j, pos_h, neg_h, costo = [], [], [], [], 0.0
    for (f, item), (p, c, _) in zip(datos, res):
        h = score_for_user(item, profile)["relevance_score"]
        costo += c
        (pos_j if f["user_rating"] in ("love", "like") else neg_j).append(p)
        (pos_h if f["user_rating"] in ("love", "like") else neg_h).append(h)
        print(f"{f['user_rating']:8} jev={p:.2f} heur={h:3} {item['title'][:60]}")
    print(f"\npositivos={len(pos_j)} negativos={len(neg_j)}")
    print(f"AUC JEV={auc(pos_j, neg_j):.2f}  AUC heurístico={auc(pos_h, neg_h):.2f}  (0.5 = azar)")
    print(f"costo USD {costo:.5f} para {len(datos)} items")


if __name__ == "__main__":
    main()
