import re
import urllib.request
import json
import xml.etree.ElementTree as ET

def clean_html_article(html_content: str) -> dict:
    """
    Extracts clean readable text and metadata from raw HTML.
    Uses bs4 and html2text if available, falls back to regex.
    """
    if not html_content:
        return {"text": "", "word_count": 0}
        
    try:
        from bs4 import BeautifulSoup
        import html2text
        
        soup = BeautifulSoup(html_content, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'aside', 'header', 'noscript', 'iframe']):
            tag.decompose()
            
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0
        clean_md = h.handle(str(soup))
        clean_md = re.sub(r'\n{3,}', '\n\n', clean_md).strip()
        return {
            "text": clean_md,
            "word_count": len(clean_md.split())
        }
    except ImportError:
        # Graceful regex fallback if bs4/html2text are not installed
        clean = re.sub(r'<script.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<style.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return {
            "text": clean,
            "word_count": len(clean.split())
        }

def fetch_youtube_transcript_or_fallback(video_id: str, description: str = "") -> str:
    """
    Fetches transcript text for a YouTube video using YouTube's timedtext/caption tracks,
    or falls back to high-signal structured metadata.
    """
    if not video_id:
        return description
        
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            page_html = resp.read().decode('utf-8', errors='ignore')
            match = re.search(r'"captionTracks":\s*(\[.*?\])', page_html)
            if match:
                tracks = json.loads(match.group(1))
                if tracks:
                    track_url = tracks[0].get('baseUrl')
                    for t in tracks:
                        lang = t.get('languageCode', '')
                        if lang in ['es', 'es-419', 'en']:
                            track_url = t.get('baseUrl')
                            break
                    
                    if track_url:
                        with urllib.request.urlopen(track_url, timeout=5) as c_resp:
                            xml_captions = c_resp.read().decode('utf-8', errors='ignore')
                            root = ET.fromstring(xml_captions)
                            transcript_lines = []
                            for text_elem in root.findall('.//text'):
                                text = text_elem.text or ''
                                text = re.sub(r'&#\d+;', '', text).replace('&quot;', '"').replace('&amp;', '&').replace('&#39;', "'")
                                transcript_lines.append(text.strip())
                            full_transcript = " ".join(transcript_lines)
                            if len(full_transcript) > 100:
                                return full_transcript
    except Exception:
        pass
        
    return description


_OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\'][^>]*>',
    re.IGNORECASE)
_CONTENT_RE = re.compile(r'content=["\']([^"\']+)["\']', re.IGNORECASE)


def fetch_og_image(url: str, timeout: float = 4) -> str:
    """og:image (o twitter:image) de una página, como URL absoluta. Lee solo
    los primeros 96 KB: las meta viven en el <head>. Vacío si no hay o falla."""
    if not url or not url.startswith("http"):
        return ""
    try:
        from urllib.parse import urljoin
        from app.ingestion import HEADERS
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if "html" not in (resp.headers.get("Content-Type") or "html"):
                return ""
            head = resp.read(96 * 1024).decode("utf-8", errors="ignore")
            final_url = resp.geturl()
        tag = _OG_RE.search(head)
        if not tag:
            return ""
        m = _CONTENT_RE.search(tag.group(0))
        return urljoin(final_url, m.group(1).replace("&amp;", "&").strip()) if m else ""
    except Exception:
        return ""
