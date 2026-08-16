import urllib.parse
import json
import re
from datetime import datetime

def sanitize_filename(title: str) -> str:
    """
    Cleans title string to make it safe for Obsidian filenames.
    """
    clean = re.sub(r'[\\/*?:"<>|]', "", title)
    clean = re.sub(r'\s+', " ", clean).strip()
    return clean[:80]

def generate_markdown(item: dict, vault_name: str = "ObsidianVault", folder_path: str = "CuratedFeed") -> dict:
    """
    Generates structured Markdown note with frontmatter and Obsidian URI.
    """
    title = item.get("title", "Sin título")
    author = item.get("author", "Autor desconocido")
    url = item.get("url", "")
    content_type = item.get("content_type", "article")
    score = item.get("relevance_score", 0)
    summary = item.get("summary_tldr", "Sin resumen disponible.")
    curator_note = item.get("curator_note", "Relevante según tu perfil de intereses.")
    
    # Parse tags
    raw_tags = item.get("topic_tags", "[]")
    try:
        tags_list = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
    except Exception:
        tags_list = ["CuratedFeed"]
    if not tags_list:
        tags_list = ["CuratedFeed"]
    if "CuratedFeed" not in tags_list:
        tags_list.append("CuratedFeed")
        
    # Parse takeaways
    raw_takeaways = item.get("key_takeaways", "[]")
    try:
        takeaways_list = json.loads(raw_takeaways) if isinstance(raw_takeaways, str) else raw_takeaways
    except Exception:
        takeaways_list = []
        
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    clean_title = sanitize_filename(title)
    
    # Frontmatter
    frontmatter_tags = "\n".join([f"  - {t}" for t in tags_list])
    frontmatter = f"""---
title: "{title.replace('"', '')}"
author: "{author.replace('"', '')}"
url: "{url}"
date_curated: {today_str}
relevance_score: {score}
content_type: {content_type}
tags:
{frontmatter_tags}
status: inbox
---"""

    # Takeaways section
    takeaways_md = ""
    if takeaways_list:
        takeaways_md = "## 💡 Ideas Clave & Puntos Destacados\n" + "\n".join([f"- {t}" for t in takeaways_list]) + "\n\n"
    
    body = f"""# {title}

**Fuente:** [{author}]({url}) | **Tipo:** `{content_type}` | **Score de Relevancia:** `{score}/100`

> 🎯 **Por qué es relevante:** {curator_note}

## 📌 Resumen Ejecutivo (TL;DR)
{summary}

{takeaways_md}## 📝 Notas & Conexiones Personales
- 

---
*Procesado por Feed Anti-Algoritmo el {today_str}*
"""
    full_markdown = f"{frontmatter}\n\n{body}"
    
    # Obsidian URI generation
    # obsidian://new?vault=VaultName&file=Path/To/Filename&content=UrlEncodedContent
    relative_file_path = f"{folder_path}/{clean_title}".strip("/")
    encoded_vault = urllib.parse.quote(vault_name)
    encoded_file = urllib.parse.quote(relative_file_path)
    encoded_content = urllib.parse.quote(full_markdown)
    
    obsidian_uri = f"obsidian://new?vault={encoded_vault}&file={encoded_file}&content={encoded_content}"
    
    return {
        "filename": f"{clean_title}.md",
        "markdown": full_markdown,
        "obsidian_uri": obsidian_uri,
        "vault_name": vault_name,
        "folder_path": folder_path
    }

if __name__ == "__main__":
    sample_item = {
        "title": "Arquitecturas Agénticas y Modelos de Contexto Largo",
        "author": "Andrej Karpathy",
        "url": "https://www.youtube.com/watch?v=sample123",
        "content_type": "video",
        "relevance_score": 96,
        "summary_tldr": "Análisis profundo sobre el diseño de agentes cognitivos...",
        "curator_note": "Alineación directa con tu investigación de sistemas agénticos.",
        "key_takeaways": json.dumps(["Los agentes requieren memoria episódica", "El contexto largo no reemplaza el retrieval"]),
        "topic_tags": json.dumps(["Sistemas-Agénticos", "IA-Aplicada"])
    }
    res = generate_markdown(sample_item)
    print("Obsidian Exporter Test:")
    print("URI snippet:", res["obsidian_uri"][:80] + "...")
    print("Markdown header:\n", res["markdown"][:250])
