#!/usr/bin/env python3
"""Mide JEV contra las calificaciones reales (love/like/relevante/guardar/dislike) de un usuario.

Solo lectura. Compara el AUC de JEV con el del score heurístico actual: la
probabilidad de que un item que te gustó quede por encima de uno que descartaste.
Usa la pregunta real de producción (ai_engine._jev_question) con el perfil actual.

    python3 routines/jev_eval.py              # usa Supabase si hay variables, si no SQLite
    python3 routines/jev_eval.py --comparar   # además, con el texto anterior del prompt

Con --comparar se califica cada item dos veces (prompt anterior y nuevo) y se
imprime AUC y score promedio por tipo de calificación de cada uno. Costo: unos
USD 0.00003 por llamada (~USD 0.01 para 150 items calificados). Necesita
OPENROUTER_API_KEY y las variables de Supabase en el entorno.

Ojo: el perfil actual ya incluye lo aprendido de estos mismos items (ejemplos y
etiquetas), así que el AUC sale optimista. Sirve para comparar un prompt contra
otro, que ven el mismo perfil; no como medida absoluta.
"""

import copy
import os
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app.db import supabase_request  # noqa: E402
from app.storage import fetch_profile  # noqa: E402
from app.ai_engine import score_for_user, _jev_question, JEV_SI_TEXTO, JEV_NO_TEXTO  # noqa: E402
from routines.jev_scoring_spike import jev, llave  # noqa: E402

# Texto del prompt antes del 12-nov (para --comparar).
ANTES_SI = "Trata de sus temas ({temas}) con sustancia. "
ANTES_NO = ("Es entretenimiento, noticia efímera, clickbait, reseña de ocio o un tema "
            "ajeno a sus intereses, aunque venga de una fuente que sigue.")


def auc(pos, neg):
    if not pos or not neg:
        return float("nan")
    pares = [(p > n) + 0.5 * (p == n) for p in pos for n in neg]
    return sum(pares) / len(pares)


def pregunta_anterior(profile):
    """La pregunta de hoy con el texto base de antes: mismas listas aprendidas."""
    q = copy.deepcopy(_jev_question(profile))
    temas = "; ".join(profile.get("focus_topics", []))
    c = q["interesa"]["criteria"]
    c["true"] = c["true"].replace(JEV_SI_TEXTO.format(temas=temas), ANTES_SI.format(temas=temas), 1)
    c["false"] = c["false"].replace(JEV_NO_TEXTO, ANTES_NO, 1)
    return q


def resumen(nombre, calificaciones, puntajes):
    """AUC y promedio por tipo de calificación. calificaciones y puntajes van en paralelo."""
    pos = [p for r, p in zip(calificaciones, puntajes) if r in ("love", "like", "save")]
    neg = [p for r, p in zip(calificaciones, puntajes) if r == "dislike"]
    print(f"\n== {nombre} ==")
    for tipo in ("love", "like", "save", "dislike"):
        xs = [p for r, p in zip(calificaciones, puntajes) if r == tipo]
        if xs:
            print(f"  {tipo:8} n={len(xs):3}  score promedio {100 * sum(xs) / len(xs):5.1f}")
    print(f"  AUC={auc(pos, neg):.2f}  (0.5 = azar)")
    if pos and neg:
        print(f"  separación (promedio gustó - promedio descartó) = {100 * (sum(pos) / len(pos) - sum(neg) / len(neg)):+.1f} puntos")


def main():
    comparar = "--comparar" in sys.argv
    key = llave()
    if not key:
        sys.exit("Sin OPENROUTER_API_KEY")
    filas = supabase_request("user_items?user_rating=not.in.(none,click)&select=user_id,item_id,user_rating,relevance_score&limit=2000")
    user_id = filas[0]["user_id"]
    profile = fetch_profile(user_id)
    ids = ",".join(str(f["item_id"]) for f in filas)
    items = {i["id"]: i for i in supabase_request(
        f"items?id=in.({ids})&select=id,title,author,content_type,raw_content,topic_tags,summary_tldr")}
    datos = [(f, items[f["item_id"]]) for f in filas if f["item_id"] in items]

    def para_jev(item):
        # Igual que producción: el extracto sale del resumen si existe.
        return {**item, "raw_content": item.get("summary_tldr") or item.get("raw_content") or ""}

    variantes = [("prompt nuevo (producción)", _jev_question(profile))]
    if comparar:
        variantes.insert(0, ("prompt anterior (texto del 11-nov)", pregunta_anterior(profile)))

    costo = 0.0
    resultados = {}
    for nombre, q in variantes:
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(lambda d: jev(key, para_jev(d[1]), q), datos))
        resultados[nombre] = [p for p, _, _ in res]
        costo += sum(c for _, c, _ in res)

    calificaciones = [f["user_rating"] for f, _ in datos]
    heur = [score_for_user(item, profile)["relevance_score"] / 100 for _, item in datos]
    for nombre, _ in variantes:
        resumen(nombre, calificaciones, resultados[nombre])
    resumen("score heurístico actual", calificaciones, heur)

    if comparar:
        ant, nue = (resultados[v[0]] for v in variantes)
        print("\n== Items donde más cambió el score (nuevo - anterior) ==")
        cambios = sorted(zip(calificaciones, ant, nue, (i["title"] for _, i in datos)), key=lambda t: -abs(t[2] - t[1]))
        for r, a, n, t in cambios[:15]:
            print(f"  {r:8} {100 * a:5.1f} -> {100 * n:5.1f}  {t[:60]}")
    print(f"\ncosto USD {costo:.5f} para {len(datos)} items x {len(variantes)} prompt(s)")


if __name__ == "__main__":
    main()
