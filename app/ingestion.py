import xml.etree.ElementTree as ET
import urllib.request
import re
import json
from datetime import datetime
from app.db import get_db_connection
from app.extractors import clean_html_article, fetch_youtube_transcript_or_fallback

# Namespaces commonly found in YouTube and RSS feeds
NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'yt': 'http://www.youtube.com/xml/schemas/2015',
    'media': 'http://search.yahoo.com/mrss/',
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'dc': 'http://purl.org/dc/elements/1.1/'
}

def parse_youtube_feed_xml(xml_text: str, feed_id: int):
    """
    Parses a YouTube Atom feed XML and returns structured items.
    """
    root = ET.fromstring(xml_text)
    items = []
    
    # Check if Atom root
    entries = root.findall('atom:entry', NAMESPACES) or root.findall('{http://www.w3.org/2005/Atom}entry')
    if not entries:
        # Try finding entries without namespace prefix if any
        entries = root.findall('.//entry')

    for entry in entries:
        try:
            # Video ID
            yt_video_id_el = entry.find('yt:videoId', NAMESPACES)
            if yt_video_id_el is not None and yt_video_id_el.text:
                video_id = yt_video_id_el.text.strip()
            else:
                id_el = entry.find('atom:id', NAMESPACES) or entry.find('id')
                if id_el is not None and id_el.text:
                    video_id = id_el.text.replace('yt:video:', '').strip()
                else:
                    continue

            # Title
            title_el = entry.find('atom:title', NAMESPACES) or entry.find('title')
            title = title_el.text.strip() if (title_el is not None and title_el.text) else "Sin título"

            # URL
            link_el = entry.find('atom:link[@rel="alternate"]', NAMESPACES) or entry.find('link')
            url = link_el.get('href') if link_el is not None else f"https://www.youtube.com/watch?v={video_id}"

            # Author / Channel
            author_el = entry.find('.//atom:author/atom:name', NAMESPACES) or entry.find('.//author/name')
            author = author_el.text.strip() if (author_el is not None and author_el.text) else "YouTube Channel"

            # Published
            pub_el = entry.find('atom:published', NAMESPACES) or entry.find('published')
            published_at = pub_el.text.strip() if (pub_el is not None and pub_el.text) else datetime.utcnow().isoformat()

            # Media group (description, thumbnail)
            desc = ""
            thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            
            media_group = entry.find('media:group', NAMESPACES)
            if media_group is not None:
                desc_el = media_group.find('media:description', NAMESPACES)
                if desc_el is not None and desc_el.text:
                    desc = desc_el.text.strip()
                thumb_el = media_group.find('media:thumbnail', NAMESPACES)
                if thumb_el is not None and thumb_el.get('url'):
                    thumb_url = thumb_el.get('url')

            items.append({
                "feed_id": feed_id,
                "guid": f"yt:{video_id}",
                "title": title,
                "url": url,
                "author": author,
                "published_at": published_at,
                "content_type": "video",
                "video_id": video_id,
                "thumbnail_url": thumb_url,
                "raw_content": desc,
                "transcript": desc # Initial fallback until enriched
            })
        except Exception as e:
            continue
            
    return items

def parse_rss_feed_xml(xml_text: str, feed_id: int):
    """
    Parses a standard RSS 2.0 or Atom feed (Blogs, Substack, Podcasts).
    """
    root = ET.fromstring(xml_text)
    items = []
    
    # Check if Atom or RSS
    if root.tag.endswith('feed'):
        # Atom feed
        entries = root.findall('atom:entry', NAMESPACES) or root.findall('.//entry')
        for entry in entries:
            try:
                title_el = entry.find('atom:title', NAMESPACES) or entry.find('title')
                title = title_el.text.strip() if (title_el is not None and title_el.text) else "Sin título"
                
                link_el = entry.find('atom:link[@rel="alternate"]', NAMESPACES) or entry.find('link')
                url = link_el.get('href') if link_el is not None else ""
                
                guid_el = entry.find('atom:id', NAMESPACES) or entry.find('id')
                guid = guid_el.text.strip() if (guid_el is not None and guid_el.text) else url
                
                pub_el = entry.find('atom:published', NAMESPACES) or entry.find('atom:updated', NAMESPACES) or entry.find('published')
                published_at = pub_el.text.strip() if (pub_el is not None and pub_el.text) else datetime.utcnow().isoformat()
                
                author_el = entry.find('.//atom:author/atom:name', NAMESPACES) or entry.find('.//name')
                author = author_el.text.strip() if (author_el is not None and author_el.text) else "Autor"
                
                content_el = entry.find('atom:content', NAMESPACES) or entry.find('atom:summary', NAMESPACES) or entry.find('content')
                raw_html = content_el.text if (content_el is not None and content_el.text) else ""
                cleaned = clean_html_article(raw_html)
                
                items.append({
                    "feed_id": feed_id,
                    "guid": guid,
                    "title": title,
                    "url": url,
                    "author": author,
                    "published_at": published_at,
                    "content_type": "article",
                    "video_id": None,
                    "thumbnail_url": "",
                    "raw_content": cleaned["text"],
                    "transcript": cleaned["text"]
                })
            except Exception:
                continue
    else:
        # RSS 2.0
        channel = root.find('channel') or root
        rss_items = channel.findall('item')
        for item_el in rss_items:
            try:
                title_el = item_el.find('title')
                title = title_el.text.strip() if (title_el is not None and title_el.text) else "Sin título"
                
                link_el = item_el.find('link')
                url = link_el.text.strip() if (link_el is not None and link_el.text) else ""
                
                guid_el = item_el.find('guid')
                guid = guid_el.text.strip() if (guid_el is not None and guid_el.text) else url
                
                pub_el = item_el.find('pubDate')
                published_at = pub_el.text.strip() if (pub_el is not None and pub_el.text) else datetime.utcnow().isoformat()
                
                author_el = item_el.find('dc:creator', NAMESPACES) or item_el.find('author')
                author = author_el.text.strip() if (author_el is not None and author_el.text) else "Autor"
                
                # Check for audio podcast enclosure
                enclosure = item_el.find('enclosure')
                content_type = "article"
                if enclosure is not None and 'audio' in enclosure.get('type', ''):
                    content_type = "audio"
                
                # Content encoded or description
                content_encoded = item_el.find('content:encoded', NAMESPACES)
                raw_html = content_encoded.text if (content_encoded is not None and content_encoded.text) else ""
                if not raw_html:
                    desc_el = item_el.find('description')
                    raw_html = desc_el.text if (desc_el is not None and desc_el.text) else ""
                
                cleaned = clean_html_article(raw_html)
                
                items.append({
                    "feed_id": feed_id,
                    "guid": guid,
                    "title": title,
                    "url": url,
                    "author": author,
                    "published_at": published_at,
                    "content_type": content_type,
                    "video_id": None,
                    "thumbnail_url": "",
                    "raw_content": cleaned["text"],
                    "transcript": cleaned["text"]
                })
            except Exception:
                continue
                
    return items

def parse_opml(opml_content: str):
    """
    Parses an OPML XML string (exported from YouTube, Feedly, etc.)
    and returns a list of discovered feeds.
    """
    root = ET.fromstring(opml_content)
    feeds = []
    
    outlines = root.findall('.//outline')
    for outline in outlines:
        xml_url = outline.get('xmlUrl') or outline.get('xmlurl')
        html_url = outline.get('htmlUrl') or outline.get('htmlurl')
        title = outline.get('title') or outline.get('text') or "Feed"
        
        if xml_url:
            feed_type = 'rss'
            channel_id = None
            if 'youtube.com' in xml_url or 'channel_id=' in xml_url:
                feed_type = 'youtube'
                match = re.search(r'channel_id=([a-zA-Z0-9_-]+)', xml_url)
                if match:
                    channel_id = match.group(1)
            elif 'substack.com' in xml_url:
                feed_type = 'substack'
                
            feeds.append({
                "title": title,
                "feed_url": xml_url,
                "site_url": html_url or "",
                "feed_type": feed_type,
                "channel_id": channel_id,
                "custom_category": outline.get('category') or "General"
            })
            
    return feeds

def register_feed(title: str, feed_url: str, feed_type: str = "youtube", category: str = "General", site_url: str = ""):
    """
    Adds a new feed to the database.
    """
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
    """
    Inserts or ignores items in the database.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    inserted_count = 0
    
    for item in items:
        try:
            cursor.execute("""
            INSERT OR IGNORE INTO items (
                feed_id, guid, title, url, author, published_at, 
                content_type, video_id, thumbnail_url, raw_content, transcript, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'inbox')
            """, (
                item["feed_id"], item["guid"], item["title"], item["url"], 
                item["author"], item["published_at"], item["content_type"], 
                item.get("video_id"), item.get("thumbnail_url", ""), 
                item.get("raw_content", ""), item.get("transcript", "")
            ))
            if cursor.rowcount > 0:
                inserted_count += 1
        except Exception:
            continue
            
    conn.commit()
    conn.close()
    return inserted_count

if __name__ == "__main__":
    sample_opml = """<?xml version="1.0" encoding="UTF-8"?>
    <opml version="1.0">
        <head><title>YouTube Subscriptions</title></head>
        <body>
            <outline text="AI Research" title="AI Research" type="rss" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-BIpcydg64z4HQ" />
            <outline text="PKM Notes" title="PKM Notes" type="rss" xmlUrl="https://subdomain.substack.com/feed" />
        </body>
    </opml>"""
    feeds = parse_opml(sample_opml)
    print(f"Parsed {len(feeds)} feeds from OPML sample:", feeds)
