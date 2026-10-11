"""Shorts de YouTube: detección y filtro antes de guardar.

    python3 tests/test_shorts.py
"""

import os
import sys
import urllib.error
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.simplefilter("ignore")

from app import shorts  # noqa: E402

fallas = []


def check(nombre, cond):
    print(("OK   " if cond else "FALLA") + " " + nombre)
    if not cond:
        fallas.append(nombre)


class _Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


pedidos = []


def falso_open(req, timeout=0):
    vid = req.full_url.rsplit("/", 1)[1]
    pedidos.append((req.get_method(), req.full_url))
    if vid.startswith("short"):
        return _Resp()
    if vid.startswith("caido"):
        raise OSError("red caída")
    raise urllib.error.HTTPError(req.full_url, 303, "See Other", {}, None)


shorts._opener.open = falso_open

check("200 en /shorts/ es Short", shorts.is_youtube_short("short1") is True)
check("303 a /watch es video normal", shorts.is_youtube_short("normal1") is False)
check("Red caída no se pierde el video", shorts.is_youtube_short("caido1") is False)
check("Sin video_id no pregunta", shorts.is_youtube_short("") is False)
check("Pide con HEAD a /shorts/<id>", pedidos[0] == ("HEAD", "https://www.youtube.com/shorts/short1"))

items = [
    {"guid": "yt:short2", "content_type": "video", "video_id": "short2"},
    {"guid": "yt:normal2", "content_type": "video", "video_id": "normal2"},
    {"guid": "yt:caido2", "content_type": "video", "video_id": "caido2"},
    {"guid": "art1", "content_type": "article", "video_id": None},
]
pedidos.clear()
quedan = [i["guid"] for i in shorts.drop_shorts(items)]
check("drop_shorts quita solo el Short", quedan == ["yt:normal2", "yt:caido2", "art1"])
check("drop_shorts no consulta artículos", all("art1" not in u for _, u in pedidos))

pedidos.clear()
shorts.drop_shorts([{"guid": "yt:short2", "content_type": "video", "video_id": "short2"}])
check("Un Short ya visto no se vuelve a preguntar", pedidos == [])

# Canales por su playlist de videos largos
from app import server  # noqa: E402

check("UC del canal pasa a UULF", server.youtube_long_form_url(
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCBJycsmduvYEL83R_U4JriQ")
    == "https://www.youtube.com/feeds/videos.xml?playlist_id=UULFBJycsmduvYEL83R_U4JriQ")
check("URL sin channel_id no se toca", server.youtube_long_form_url(
    "http://gdata.youtube.com/feeds/base/users/TEDEducation/uploads") is None)

pedidas = []


def falso_fetch(feed_dict, url, headers, feed_type, feed_id):
    pedidas.append(url)
    if "UULF" in url:
        return None if feed_dict.get("sin_uulf") else [{"guid": "yt:largo", "video_id": "largo", "content_type": "video"}]
    return [{"guid": "yt:short9", "video_id": "short9", "content_type": "video"},
            {"guid": "yt:normal9", "video_id": "normal9", "content_type": "video"}]


server._fetch_and_parse = falso_fetch
canal = "https://www.youtube.com/feeds/videos.xml?channel_id=UCBJycsmduvYEL83R_U4JriQ"
r = server.fetch_feed_and_parse({"feed_url": canal, "feed_type": "youtube", "id": 1})
check("YouTube se pide por UULF, sin tocar el feed del canal", [i["guid"] for i in r] == ["yt:largo"] and len(pedidas) == 1)
pedidas.clear()
r = server.fetch_feed_and_parse({"feed_url": canal, "feed_type": "youtube", "id": 1, "sin_uulf": True})
check("UULF 404 cae al feed del canal y filtra Shorts", [i["guid"] for i in r] == ["yt:normal9"] and len(pedidas) == 2)

print(f"\n{len(fallas)} fallas")
sys.exit(1 if fallas else 0)
