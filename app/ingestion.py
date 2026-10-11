import xml.etree.ElementTree as ET
import urllib.request
import re
import json
import warnings
from datetime import datetime
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from app.db import get_db_connection
from app.extractors import clean_html_article

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'yt': 'http://www.youtube.com/xml/schemas/2015',
    'media': 'http://search.yahoo.com/mrss/',
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'itunes': 'http://www.itunes.com/dtds/podcast-1.0.dtd'
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "SOCS=CAESEwgDEgk2ODEwNTk0OTQaAmVuIAEaBgiA_LyaBg"
}

def resolve_feed_url(raw_url: str) -> tuple[str, str, str]:
    raw_url = raw_url.strip()
    feed_type = "rss"
    suggested_title = ""

    # YouTube URL handling
    if "youtube.com" in raw_url or "youtu.be" in raw_url or raw_url.startswith("@"):
        feed_type = "youtube"
        
        # Already an RSS feed URL
        if "feeds/videos.xml" in raw_url:
            return raw_url, feed_type, suggested_title
            
        # Already has /channel/UC...
        if "/channel/UC" in raw_url:
            ch_id = raw_url.split("/channel/")[1].split("/")[0].split("?")[0]
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}", feed_type, suggested_title
            
        # Handle @handle or full URL
        target_url = raw_url
        if raw_url.startswith("@"):
            target_url = f"https://www.youtube.com/{raw_url}"
            
        clean_url = target_url.rstrip("/")
        fetch_url = clean_url + "/videos" if not clean_url.endswith("/videos") else clean_url
        
        try:
            req = urllib.request.Request(fetch_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
                
                # Check for channel name / title
                title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
                if title_match:
                    suggested_title = title_match.group(1)
                    
                # Search for channelId in various YouTube script formats
                patterns = [
                    r'"browseId":"(UC[a-zA-Z0-9_-]{20,24})"',
                    r'"externalId":"(UC[a-zA-Z0-9_-]{20,24})"',
                    r'"channelId":"(UC[a-zA-Z0-9_-]{20,24})"',
                    r'href="(https://www\.youtube\.com/feeds/videos\.xml\?channel_id=[a-zA-Z0-9_-]+)"',
                    r'channel_id=(UC[a-zA-Z0-9_-]{20,24})'
                ]
                for pat in patterns:
                    m = re.search(pat, html)
                    if m:
                        cid = m.group(1)
                        if cid.startswith("https://"):
                            return cid, feed_type, suggested_title
                        return f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", feed_type, suggested_title
        except Exception:
            pass

    # Substack URL
    if "substack.com" in raw_url:
        feed_type = "substack"
        if not raw_url.endswith("/feed"):
            clean_url = raw_url.rstrip("/")
            return f"{clean_url}/feed", feed_type, suggested_title

    return raw_url, feed_type, suggested_title

def _first(el, *paths):
    """Primer subelemento que exista. Compara contra None a propósito: un
    Element sin hijos es falsy, y `find(a) or find(b)` lo descartaba, que es
    por lo que todo YouTube y Atom salía "Sin título" y con fecha de hoy."""
    for path in paths:
        found = el.find(path, NAMESPACES)
        if found is not None:
            return found
    return None


def _text(el, *paths) -> str:
    found = _first(el, *paths)
    if found is None or not found.text:
        return ""
    return found.text.strip()


def _fallback_title(text: str) -> str:
    """Primera línea con contenido, sin markdown, como título de último recurso."""
    for line in (text or "").splitlines():
        line = re.sub(r'[#*_>`\[\]]', '', line).strip()
        if line:
            return line[:120]
    return "Sin título"


_IMG_RE = re.compile(r'<img[^>]+?src=["\']([^"\']+)["\']', re.IGNORECASE)
_IMG_EXT = re.compile(r'\.(jpe?g|png|webp|gif|avif)(\?|$)', re.IGNORECASE)


def _is_image(el) -> bool:
    medium = (el.get('medium') or '').lower()
    mime = (el.get('type') or '').lower()
    if medium == 'image' or mime.startswith('image/'):
        return True
    return not medium and not mime and bool(_IMG_EXT.search(el.get('url') or ''))


def _image_for(el, raw_html: str = "") -> str:
    """Imagen de un item RSS/Atom, de la más explícita a la más débil:
    media:content, media:thumbnail (sueltos o en media:group), enclosure de
    imagen, itunes:image del episodio y, al final, el primer <img> del HTML.
    Si no hay, queda vacío y el sync prueba con og:image de la página."""
    for path in ('media:content', 'media:group/media:content'):
        for m in el.findall(path, NAMESPACES):
            if m.get('url') and _is_image(m):
                return m.get('url')
    for path in ('media:thumbnail', 'media:group/media:thumbnail', 'media:content/media:thumbnail'):
        t = el.find(path, NAMESPACES)
        if t is not None and t.get('url'):
            return t.get('url')
    for enc in el.findall('enclosure') + el.findall('atom:link[@rel="enclosure"]', NAMESPACES):
        href = enc.get('url') or enc.get('href') or ''
        if href and (enc.get('type') or '').lower().startswith('image/'):
            return href
    it = el.find('itunes:image', NAMESPACES)
    if it is not None and it.get('href'):
        return it.get('href')
    m = _IMG_RE.search(raw_html or "")
    if m and not m.group(1).startswith('data:'):
        return m.group(1).replace('&amp;', '&')
    return ""


def _is_podcast(el) -> bool:
    for enc in el.findall('enclosure') + el.findall('atom:link[@rel="enclosure"]', NAMESPACES):
        if (enc.get('type') or '').lower().startswith('audio/'):
            return True
    return False


def parse_youtube_feed_xml(xml_text: str, feed_id: int):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    for entry in root.findall('atom:entry', NAMESPACES):
        try:
            video_id = _text(entry, 'yt:videoId') or _text(entry, 'atom:id').replace('yt:video:', '')
            if not video_id:
                continue

            desc = _text(entry, 'media:group/media:description')
            title = (_text(entry, 'atom:title') or _text(entry, 'media:group/media:title')
                     or _fallback_title(desc))

            link_el = _first(entry, 'atom:link[@rel="alternate"]', 'atom:link')
            url = (link_el.get('href') if link_el is not None else "") or f"https://www.youtube.com/watch?v={video_id}"

            thumb_el = _first(entry, 'media:group/media:thumbnail')
            thumb_url = (thumb_el.get('url') if thumb_el is not None else "") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

            items.append({
                "feed_id": feed_id,
                "guid": f"yt:{video_id}",
                "title": title,
                "url": url,
                "author": _text(entry, 'atom:author/atom:name') or "Canal de YouTube",
                "published_at": (_text(entry, 'atom:published') or _text(entry, 'atom:updated')
                                 or datetime.utcnow().isoformat()),
                "content_type": "video",
                "video_id": video_id,
                "thumbnail_url": thumb_url,
                "raw_content": desc,
                "transcript": desc
            })
        except Exception:
            continue

    return items

def parse_rss_feed_xml(xml_text: str, feed_id: int):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    if root.tag.endswith('feed'):
        for entry in root.findall('atom:entry', NAMESPACES):
            try:
                link_el = _first(entry, 'atom:link[@rel="alternate"]', 'atom:link')
                url = link_el.get('href', '') if link_el is not None else ""

                content_el = _first(entry, 'atom:content', 'atom:summary')
                raw_html = content_el.text if (content_el is not None and content_el.text) else ""
                cleaned = clean_html_article(raw_html)

                items.append({
                    "feed_id": feed_id,
                    "guid": _text(entry, 'atom:id') or url,
                    "title": _text(entry, 'atom:title') or _fallback_title(cleaned["text"]),
                    "url": url,
                    "author": _text(entry, 'atom:author/atom:name') or "Autor",
                    "published_at": (_text(entry, 'atom:published') or _text(entry, 'atom:updated')
                                     or datetime.utcnow().isoformat()),
                    "content_type": "podcast" if _is_podcast(entry) else "article",
                    "video_id": None,
                    "thumbnail_url": _image_for(entry, raw_html),
                    "raw_content": cleaned["text"],
                    "transcript": cleaned["text"]
                })
            except Exception:
                continue
    else:
        channel = root.find('channel')
        if channel is None:
            channel = root
        # Portada del podcast: respaldo si el episodio no trae imagen propia.
        ch_img = channel.find('itunes:image', NAMESPACES)
        channel_image = (ch_img.get('href') if ch_img is not None else "") or _text(channel, 'image/url')
        for item_el in channel.findall('item'):
            try:
                url = _text(item_el, 'link')
                raw_html = _text(item_el, 'content:encoded') or _text(item_el, 'description')
                cleaned = clean_html_article(raw_html)
                podcast = _is_podcast(item_el)

                items.append({
                    "feed_id": feed_id,
                    "guid": _text(item_el, 'guid') or url,
                    "title": _text(item_el, 'title') or _fallback_title(cleaned["text"]),
                    "url": url,
                    "author": _text(item_el, 'dc:creator', 'author') or "Autor",
                    "published_at": _text(item_el, 'pubDate') or datetime.utcnow().isoformat(),
                    "content_type": "podcast" if podcast else "article",
                    "video_id": None,
                    "thumbnail_url": _image_for(item_el, raw_html) or (channel_image if podcast else ""),
                    "raw_content": cleaned["text"],
                    "transcript": cleaned["text"]
                })
            except Exception:
                continue

    return items

def parse_opml(opml_content: str):
    feeds = []
    try:
        soup = BeautifulSoup(opml_content, 'html.parser')
        outlines = soup.find_all('outline')
        for outline in outlines:
            xml_url = outline.get('xmlurl') or outline.get('xmlUrl')
            html_url = outline.get('htmlurl') or outline.get('htmlUrl')
            title = outline.get('title') or outline.get('text') or "Feed"
            
            category = "General"
            parent = outline.parent
            if parent and parent.name == 'outline':
                category = parent.get('title') or parent.get('text') or "General"
            elif outline.get('category'):
                category = outline.get('category')
                
            if xml_url:
                feed_type = 'rss'
                channel_id = None
                if 'youtube.com' in xml_url or 'channel_id=' in xml_url or 'gdata.youtube.com' in xml_url:
                    feed_type = 'youtube'
                    match = re.search(r'channel_id=([a-zA-Z0-9_-]+)', xml_url)
                    if match:
                        channel_id = match.group(1)
                elif 'substack.com' in xml_url:
                    feed_type = 'substack'
                    
                feeds.append({
                    "title": title.strip(),
                    "feed_url": xml_url.strip(),
                    "site_url": (html_url or "").strip(),
                    "feed_type": feed_type,
                    "channel_id": channel_id,
                    "custom_category": category.strip()
                })
        if feeds:
            return feeds
    except Exception:
        pass
        
    return feeds

def register_feed(title: str, feed_url: str, feed_type: str = "youtube", category: str = "General", site_url: str = ""):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    channel_id = None
    if feed_type == "youtube":
        match = re.search(r'channel_id=([a-zA-Z0-9_-]+)', feed_url)
        if match:
            channel_id = match.group(1)
            
    try:
        cursor.execute("""
        INSERT INTO feeds (title, feed_url, site_url, feed_type, channel_id, custom_category)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(feed_url) DO UPDATE SET title=excluded.title, custom_category=excluded.custom_category
        """, (title, feed_url, site_url, feed_type, channel_id, category))
        conn.commit()
        feed_id = cursor.lastrowid
        conn.close()
        return feed_id
    except Exception as e:
        conn.close()
        raise e

def save_items_to_db(items: list):
    """Inserta items en el corpus global. Sin estado personal: eso vive en user_items."""
    conn = get_db_connection()
    cursor = conn.cursor()
    inserted_count = 0

    for item in items:
        try:
            cursor.execute("""
            INSERT OR IGNORE INTO items (
                feed_id, guid, title, url, author, published_at,
                content_type, video_id, thumbnail_url, raw_content, transcript,
                summary_tldr, key_takeaways, topic_tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["feed_id"], item["guid"], item["title"], item["url"],
                item.get("author", "Autor"), item["published_at"], item["content_type"],
                item.get("video_id"), item.get("thumbnail_url", ""),
                item.get("raw_content", ""), item.get("transcript", ""),
                item.get("summary_tldr", ""), item.get("key_takeaways", "[]"),
                item.get("topic_tags", "[]")
            ))
            if cursor.rowcount > 0:
                inserted_count += 1
        except Exception as e:
            print(f"save_items_to_db: fallo al guardar '{item.get('title')}': {e}")
            continue

    conn.commit()
    conn.close()
    return inserted_count
