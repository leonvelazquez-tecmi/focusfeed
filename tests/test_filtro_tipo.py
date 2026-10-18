"""Elegir un tipo ("Solo podcasts") sin resultados no es un error de carga.

Falla del 18-oct: los contadores (Hoy 14680, Guardados 4) no miran el filtro de
tipo, y la guarda de PR 21 tomaba una lista vacía con contadores > 0 por
"respuesta rota". Con "Solo podcasts" la lista vacía es legítima, y como el
selector persiste entre pestañas, las tres quedaban en "No se pudo cargar".

    python3 tests/test_filtro_tipo.py
"""

import json
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(RAIZ, "app/static/index.html"), encoding="utf-8").read()
fallas = []


def check(nombre, ok):
    print(("OK   " if ok else "FALLA"), nombre)
    if not ok:
        fallas.append(nombre)


m = re.search(r"function listaRota\(.*?\n\}\n", html, re.S)
check("existe listaRota en index.html", bool(m))


def rota(data, tab, tipo):
    js = m.group(0) + f"\nconsole.log(JSON.stringify(listaRota({json.dumps(data)},{json.dumps(tab)},{json.dumps(tipo)})));"
    return json.loads(subprocess.check_output(["node", "-e", js], text=True))


if m:
    cont = {"counts": {"inbox": 14680, "reading": 4}, "items": []}
    for tab in ("curated", "inbox", "reading"):
        check(f"{tab} + Solo podcasts vacío NO es error", rota(cont, tab, "podcast") is False)
        check(f"{tab} + Solo videos vacío NO es error", rota(cont, tab, "video") is False)
    check("Todo tipo vacío con contadores SÍ es error (guarda de PR 21 intacta)", rota(cont, "inbox", "all") is True)
    check("Todo tipo con tarjetas no es error", rota({**cont, "items": [{"id": 1}]}, "inbox", "all") is False)
    check("Fuentes nunca es error", rota(cont, "sources", "all") is False)

print(f"\n{len(fallas)} fallas")
sys.exit(1 if fallas else 0)
