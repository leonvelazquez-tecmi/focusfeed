"""Auto-descubrimiento de RSS: Medium, <link rel="alternate">, rutas típicas y Substack.

Sin red: `discover_rss` recibe el `fetch` por parámetro.

    python3 tests/test_discover_rss.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ingestion import discover_rss, looks_like_feed, medium_feed_url, resolve_feed_url  # noqa: E402

fallas = []


def check(nombre, cond, detalle=""):
    print(("OK   " if cond else "FALLA") + " " + nombre + (f" · {detalle}" if detalle else ""))
    if not cond:
        fallas.append(nombre)


RSS = '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
ATOM = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def sitio(paginas):
    return lambda url, *a, **k: paginas.get(url, "")


print("Medium")
check("Autor", medium_feed_url("https://medium.com/@ana") == "https://medium.com/feed/@ana")
check("Publicación", medium_feed_url("https://medium.com/mi-publicacion/un-post-123") == "https://medium.com/feed/mi-publicacion")
check("Subdominio", medium_feed_url("https://ana.medium.com/post-1") == "https://ana.medium.com/feed")
check("Una página de tema no es un feed", medium_feed_url("https://medium.com/tag/ai") is None)
check("discover_rss resuelve Medium sin tocar la red",
      discover_rss("https://medium.com/@ana", fetch=lambda u: (_ for _ in ()).throw(AssertionError("red"))) == "https://medium.com/feed/@ana")

print("Auto-descubrimiento")
html = '<html><head><link rel="alternate" type="application/rss+xml" href="/rss/atom"></head></html>'
check("<link rel=alternate> con ruta relativa",
      discover_rss("https://blog.example.com/", fetch=sitio({"https://blog.example.com/": html})) == "https://blog.example.com/rss/atom")
check("Si la URL ya es el feed, la devuelve",
      discover_rss("https://blog.example.com/feed", fetch=sitio({"https://blog.example.com/feed": RSS})) == "https://blog.example.com/feed")
check("Ruta típica /atom.xml cuando la página no anuncia nada",
      discover_rss("https://blog.example.com", fetch=sitio({"https://blog.example.com": "<html></html>",
                                                           "https://blog.example.com/atom.xml": ATOM})) == "https://blog.example.com/atom.xml")
check("Sin feed devuelve None", discover_rss("https://nada.example.com", fetch=sitio({})) is None)
check("Una fuente de correo no se busca", discover_rss("email:abc:xyz", fetch=sitio({})) is None)
check("Reconoce RSS, Atom y no reconoce HTML",
      looks_like_feed(RSS) and looks_like_feed(ATOM) and not looks_like_feed("<html><body>hola</body></html>"))

print("Substack")
check("La URL de un post resuelve al feed de la publicación",
      resolve_feed_url("https://ejemplo.substack.com/p/un-post")[0] == "https://ejemplo.substack.com/feed")
check("Una URL que ya termina en /feed se respeta",
      resolve_feed_url("https://ejemplo.substack.com/feed")[0] == "https://ejemplo.substack.com/feed")

print(f"\n{len(fallas)} fallas")
sys.exit(1 if fallas else 0)
