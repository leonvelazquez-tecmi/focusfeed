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

# Imágenes de artículos y podcasts
IMG_RSS = """<?xml version="1.0"?>
<rss xmlns:media="http://search.yahoo.com/mrss/" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
<itunes:image href="https://pod.com/portada.jpg"/>
<item><title>Con media:content</title><guid>m1</guid><link>https://a.com/1</link>
  <media:content url="https://a.com/video.mp4" type="video/mp4"/>
  <media:content url="https://a.com/foto.jpg" medium="image"/></item>
<item><title>Con media:thumbnail</title><guid>m2</guid><link>https://a.com/2</link>
  <media:thumbnail url="https://a.com/mini.png"/></item>
<item><title>Con enclosure</title><guid>m3</guid><link>https://a.com/3</link>
  <enclosure url="https://a.com/enc.webp" type="image/webp" length="1"/></item>
<item><title>Primer img</title><guid>m4</guid><link>https://a.com/4</link>
  <content:encoded><![CDATA[<p>Hola</p><img alt="x" src="https://a.com/cuerpo.jpg?w=800&amp;h=400"><img src="https://a.com/otra.jpg">]]></content:encoded></item>
<item><title>Sin imagen</title><guid>m5</guid><link>https://a.com/5</link><description>Solo texto</description></item>
<item><title>Episodio</title><guid>p1</guid><link>https://pod.com/1</link>
  <enclosure url="https://pod.com/1.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Episodio con arte</title><guid>p2</guid><link>https://pod.com/2</link>
  <enclosure url="https://pod.com/2.mp3" type="audio/mpeg" length="1"/>
  <itunes:image href="https://pod.com/ep2.jpg"/></item>
</channel></rss>"""
img = {i["guid"]: i for i in parse_rss_feed_xml(IMG_RSS, 4)}
check("Imagen: media:content con medium=image, no el video", img["m1"]["thumbnail_url"] == "https://a.com/foto.jpg")
check("Imagen: media:thumbnail", img["m2"]["thumbnail_url"] == "https://a.com/mini.png")
check("Imagen: enclosure de imagen", img["m3"]["thumbnail_url"] == "https://a.com/enc.webp")
check("Imagen: primer <img> del contenido", img["m4"]["thumbnail_url"] == "https://a.com/cuerpo.jpg?w=800&h=400")
check("Imagen: sin imagen queda vacío (el sync prueba og:image)", img["m5"]["thumbnail_url"] == "")
check("Podcast: enclosure de audio marca el tipo", img["p1"]["content_type"] == "podcast" and img["m1"]["content_type"] == "article")
check("Podcast: sin arte propio usa la portada del canal", img["p1"]["thumbnail_url"] == "https://pod.com/portada.jpg")
check("Podcast: arte del episodio gana", img["p2"]["thumbnail_url"] == "https://pod.com/ep2.jpg")
check("Artículo sin imagen no hereda la portada del canal", img["m5"]["thumbnail_url"] == "")

ATOM_IMG = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<entry><id>a1</id><title>Atom</title><link href="https://b.com/1"/>
<media:thumbnail url="https://b.com/t.jpg"/><content type="html">&lt;p&gt;x&lt;/p&gt;</content></entry>
<entry><id>a2</id><title>Atom img</title><link href="https://b.com/2"/>
<content type="html">&lt;img src="https://b.com/c.png"&gt;</content></entry></feed>"""
aimg = parse_rss_feed_xml(ATOM_IMG, 5)
check("Atom: media:thumbnail", aimg[0]["thumbnail_url"] == "https://b.com/t.jpg")
check("Atom: primer <img> del contenido", aimg[1]["thumbnail_url"] == "https://b.com/c.png")

from app import extractors  # noqa: E402


class _Resp:
    def __init__(self, html, url="https://c.com/nota"):
        self.html, self.url = html.encode(), url
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
    def read(self, n=-1): return self.html[:n] if n > 0 else self.html
    def geturl(self): return self.url
    def __enter__(self): return self
    def __exit__(self, *a): return False


_orig = extractors.urllib.request.urlopen
try:
    extractors.urllib.request.urlopen = lambda req, timeout=0: _Resp(
        '<head><meta content="/img/og.jpg" property="og:image"></head>')
    check("og:image relativo se vuelve absoluto", extractors.fetch_og_image("https://c.com/nota") == "https://c.com/img/og.jpg")
    extractors.urllib.request.urlopen = lambda req, timeout=0: _Resp(
        '<meta name="twitter:image" content="https://cdn.c.com/tw.png">')
    check("twitter:image como respaldo", extractors.fetch_og_image("https://c.com/nota") == "https://cdn.c.com/tw.png")
    extractors.urllib.request.urlopen = lambda req, timeout=0: _Resp("<head></head>")
    check("Sin og:image devuelve vacío", extractors.fetch_og_image("https://c.com/nota") == "")

    def _boom(req, timeout=0):
        raise OSError("red caída")
    extractors.urllib.request.urlopen = _boom
    check("Error de red devuelve vacío, no truena", extractors.fetch_og_image("https://c.com/nota") == "")
finally:
    extractors.urllib.request.urlopen = _orig

print(f"\n{len(fallas)} fallas")
sys.exit(1 if fallas else 0)
