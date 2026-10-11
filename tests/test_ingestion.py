"""Parsers de feeds: título, autor, fecha y link reales.

Regresión del bug de ElementTree (un Element sin hijos es falsy) que dejaba
todo YouTube y Atom como "Sin título" y con fecha de hoy.

    python3 tests/test_ingestion.py
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app.ingestion import parse_youtube_feed_xml, parse_rss_feed_xml  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
RSS = """<?xml version="1.0"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item><title>Nota RSS</title><link>https://ejemplo.com/rss</link>
<guid>rss-1</guid><pubDate>Wed, 01 Oct 2026 10:00:00 GMT</pubDate>
<dc:creator>Luis R</dc:creator><description>&lt;p&gt;Texto&lt;/p&gt;</description></item>
</channel></rss>"""

fallas = []


def check(nombre, cond):
    print(("OK   " if cond else "FALLA") + " " + nombre)
    if not cond:
        fallas.append(nombre)


def leer(nombre):
    with open(os.path.join(FIX, nombre), encoding="utf-8") as f:
        return f.read()


yt = parse_youtube_feed_xml(leer("youtube.xml"), 1)
check("YouTube trae items", len(yt) > 0)
check("YouTube: ningún 'Sin título'", all(i["title"] != "Sin título" for i in yt))
check("YouTube: fecha real del feed, no la de hoy", all(i["published_at"].endswith("+00:00") for i in yt))
check("YouTube: autor del canal", all(i["author"] != "Canal de YouTube" for i in yt))
check("YouTube: link al video", all(i["video_id"] in i["url"] for i in yt))

atom = parse_rss_feed_xml(leer("atom.xml"), 2)
check("Atom: dos entradas", len(atom) == 2)
check("Atom: título", atom[0]["title"] == "Cómo leer menos y entender más")
check("Atom: link", atom[0]["url"] == "https://ejemplo.com/leer-menos")
check("Atom: autor", atom[0]["author"] == "Ana Pérez")
check("Atom: fecha de updated", atom[0]["published_at"] == "2026-10-01T10:00:00Z")
check("Atom: título vacío cae a la primera línea", atom[1]["title"] == "Primera línea que sirve de título")
check("Atom: guid", atom[1]["guid"] == "tag:ejemplo.com,2026:2")

rss = parse_rss_feed_xml(RSS, 3)
check("RSS: título y autor", rss[0]["title"] == "Nota RSS" and rss[0]["author"] == "Luis R")
check("RSS: link y fecha", rss[0]["url"] == "https://ejemplo.com/rss" and rss[0]["published_at"].startswith("Wed"))

print(f"\n{len(fallas)} fallas")
sys.exit(1 if fallas else 0)
