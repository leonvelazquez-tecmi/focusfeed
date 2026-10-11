#!/usr/bin/env python3
"""Poka-yoke del modelo de Gemini: un retiro de modelo no debe romper la IA en silencio.

    python3 tests/test_ai_model_resolution.py
"""

import io
import json
import os
import sys
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for v in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENROUTER_API_KEY", "AI_MODEL"):
    os.environ.pop(v, None)
os.environ["GEMINI_API_KEY"] = "llave-de-prueba"

import app.ai_engine as ai  # noqa: E402

fallas = 0


def check(nombre, cond):
    global fallas
    print(("OK    " if cond else "FALLA ") + nombre)
    if not cond:
        fallas += 1


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


LISTA = {"models": [
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-4-flash-preview", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-9-flash-embedding", "supportedGenerationMethods": ["embedContent"]},
    {"name": "models/gemini-3.5-pro", "supportedGenerationMethods": ["generateContent"]},
]}
OK = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}], "usageMetadata": {}}


def reset():
    ai._MODEL_CACHE["name"] = None
    ai.AI_HEALTH.update(last_error=None, last_error_at=None, gemini_model=None)
    ai._ULTIMO_REGISTRO.clear()
    ai.GEMINI_MODEL_OVERRIDE = ""


def hay_404(url):
    return urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))


# 1) Elige el flash más nuevo, sin lite, preview, imagen, embedding ni pro
reset()
with mock.patch("urllib.request.urlopen", lambda *a, **k: Resp(json.dumps(LISTA).encode())):
    check("resuelve el flash estable más nuevo", ai._resolve_gemini_model() == "gemini-3.5-flash")

# 2) Si el modelo cacheado se retira (404), resuelve otro y reintenta una vez
reset()
llamados = []


def urlopen(req, timeout=0):
    url = req if isinstance(req, str) else req.full_url
    if "/models?" in url:
        return Resp(json.dumps(LISTA).encode())
    llamados.append(url)
    if "gemini-3.5-flash:" in url:
        raise hay_404(url)
    return Resp(json.dumps(OK).encode())


with mock.patch("urllib.request.urlopen", urlopen):
    modelo, data = ai._gemini_with_recovery("hola")
check("tras un 404 reintenta con el siguiente modelo más nuevo", modelo == "gemini-3.1-flash-lite")
check("el modelo retirado no vuelve a elegirse", ai._MODEL_CACHE["name"] == "gemini-3.1-flash-lite")
check("la falla queda a la vista", "gemini-3.5-flash HTTP 404" in (ai.ai_health()["ai_last_error"] or ""))

# 2b) Un modelo saturado (503/429) también salta al siguiente
reset()
vistos = []


def saturado(req, timeout=0):
    url = req if isinstance(req, str) else req.full_url
    if "/models?" in url:
        return Resp(json.dumps(LISTA).encode())
    vistos.append(url)
    if "gemini-3.5-flash:" in url:
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, io.BytesIO(b"{}"))
    return Resp(json.dumps(OK).encode())


with mock.patch("urllib.request.urlopen", saturado):
    modelo, _ = ai._gemini_with_recovery("hola")
check("un 503 salta al siguiente modelo", modelo == "gemini-3.1-flash-lite")

# 3) AI_MODEL manda sobre la resolución automática
reset()
ai.GEMINI_MODEL_OVERRIDE = "gemini-manual"
check("AI_MODEL tiene prioridad", ai._resolve_gemini_model() == "gemini-manual")

# 4) Sin red para listar, cae al modelo de respaldo y avisa
reset()


def sin_red(*a, **k):
    raise urllib.error.URLError("sin red")


with mock.patch("urllib.request.urlopen", sin_red):
    check("sin lista usa el respaldo", ai._resolve_gemini_model() == ai.GEMINI_MODEL_FALLBACK)
check("avisa que no pudo listar", "gemini_list_models" in (ai.ai_health()["ai_last_error"] or ""))

# 5) El canario reporta ok cuando todo responde
reset()
with mock.patch("urllib.request.urlopen", lambda req, timeout=0: Resp(
        json.dumps(LISTA if "/models?" in (req if isinstance(req, str) else req.full_url) else OK).encode())):
    res = ai.ai_canary()
check("canario ok con Gemini sano", res["gemini"].startswith("ok:"))

print(f"\n{fallas} fallas")
sys.exit(1 if fallas else 0)
