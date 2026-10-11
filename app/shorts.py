"""Detección de Shorts de YouTube.

El RSS de un canal mezcla Shorts y videos normales sin distinguirlos. YouTube
sí lo sabe: /shorts/<id> responde 200 si es Short y redirige (303) a /watch
si es un video normal. Se pide con HEAD y sin seguir la redirección.
"""

import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait

from app.ingestion import HEADERS


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)

# Shorts ya vistos en esta instancia. Como no entran al corpus, sin esto se
# volverían a preguntar en cada sync mientras sigan en los 15 del RSS.
_KNOWN_SHORTS = set()


def is_youtube_short(video_id: str, timeout: float = 4) -> bool:
    """True solo con un 200 claro. Redirección, error o red caída = no es
    Short: preferimos dejar pasar un Short a perder un video."""
    if not video_id:
        return False
    req = urllib.request.Request(f"https://www.youtube.com/shorts/{video_id}",
                                 headers=HEADERS, method="HEAD")
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


def find_shorts(video_ids: list, workers: int = 16, timeout: float = 4, total: float = None) -> set:
    """Los video_ids que son Shorts, revisados en paralelo. Con `total`, lo que
    no termine a tiempo se cuenta como video normal."""
    ids = sorted({v for v in video_ids if v} - _KNOWN_SHORTS)
    known = {v for v in video_ids if v in _KNOWN_SHORTS}
    if not ids:
        return known
    ex = ThreadPoolExecutor(max_workers=workers)
    futures = {ex.submit(is_youtube_short, v, timeout): v for v in ids}
    done, _ = wait(futures, timeout=total)
    ex.shutdown(wait=False, cancel_futures=True)
    found = {futures[f] for f in done if not f.exception() and f.result()}
    _KNOWN_SHORTS.update(found)
    return found | known


def drop_shorts(items: list, total: float = 4.5) -> list:
    """Quita los Shorts de una lista de items nuevos. Solo revisa videos."""
    shorts = find_shorts([i.get("video_id") for i in items if i.get("content_type") == "video"],
                         timeout=3.5, total=total)
    if shorts:
        print(f"drop_shorts: {len(shorts)} Shorts fuera")
    return [i for i in items if i.get("video_id") not in shorts]
