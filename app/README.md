# FocusFeed — Curador Inteligente Anti-Algoritmo (PWA + PKM)

Aplicación web progresiva (PWA) diseñada para sustituir el feed algorítmico de YouTube, Feedly y plataformas de contenido por una **capa de curación personalizada asistida por IA**, libre de distracciones y con integración directa a **Obsidian**.

---

## 🏗️ Arquitectura y Componentes

1. **Ingesta de Contenido (`app/ingestion.py`):**
   - Ingesta de suscripciones de YouTube mediante canales RSS nativos (`https://www.youtube.com/feeds/videos.xml?channel_id=...`), eliminando cuotas y límites de API.
   - Soporte para feeds RSS/Atom estándar (blogs, Substack, podcasts).
   - Importador de suscripciones en bloque vía formato **OPML**.

2. **Extracción y Limpieza (`app/extractors.py`):**
   - Extracción de transcripciones de YouTube y texto limpio de artículos (modo lectura).

3. **Motor de IA & Scoring (`app/ai_engine.py`):**
   - Calificación de relevancia (0 a 100) según tu perfil de intereses y criterio editorial configurable.
   - Generación de **Resumen Ejecutivo (TL;DR)** en 2-3 párrafos y lista de **3 a 5 Ideas Clave**.
   - Compatible con Gemini API / OpenAI API o motor de evaluación semántica local.

4. **Integración con Obsidian (`app/pkm_exporter.py`):**
   - Generación automática de notas Markdown con frontmatter YAML (tags, score, url, autor, fecha).
   - Protocolo nativo **Obsidian URI** (`obsidian://new?...`) que abre y guarda la nota en tu bóveda en un clic desde iPhone o Mac.

5. **Frontend PWA Móvil Distraction-Free (`app/static/index.html`):**
   - Reproductor embebido de YouTube sin recomendaciones laterales ni videos sugeridos (`rel=0`).
   - Lector de artículos limpio y reproductor de audio.
   - PWA instalable en iOS ("Agregar a pantalla de inicio").

---

## 🚀 Cómo Ejecutar la Aplicación

```bash
# Iniciar el servidor local
PYTHONPATH=. python3 app/server.py
```
Abre en tu navegador o dispositivo: `http://localhost:8080`

---

## 📱 Instalación en iPhone (PWA)
1. Abre la URL en Safari en tu iPhone.
2. Pulsa el botón **Compartir** (icono de cuadro con flecha hacia arriba).
3. Selecciona **"Agregar a pantalla de inicio"** (Add to Home Screen).
4. La aplicación se abrirá como app independiente a pantalla completa.
