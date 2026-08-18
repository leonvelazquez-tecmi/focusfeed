"""Resolución del usuario de cada request.

Estado transitorio: la autenticación real (T2) todavía no está conectada en el
frontend, así que hay un camino de respaldo por variable de entorno para que la
app siga funcionando con la cuenta del dueño. Ese respaldo se ELIMINA cuando
Supabase Auth esté en su lugar. Está marcado abajo con OWNER FALLBACK.
"""

import base64
import hashlib
import hmac
import json
import os
import time

from app.db import is_supabase

# Usuario del modo local sin Supabase. Solo aplica en desarrollo.
DEV_USER_ID = "00000000-0000-0000-0000-000000000001"

# OWNER FALLBACK: quitar cuando T2 esté completo.
OWNER_USER_ID = os.environ.get("FOCUSFEED_OWNER_ID", DEV_USER_ID)

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def verify_supabase_jwt(token: str):
    """Verifica un JWT HS256 de Supabase y devuelve el `sub` (uuid del usuario).

    Falla cerrado: sin secreto configurado no se confía en ningún token.
    """
    if not token or not SUPABASE_JWT_SECRET:
        return None
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        return None

    try:
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            # Los tokens firmados con clave asimétrica requieren JWKS.
            # Todavía no se soportan; se rechazan en lugar de aceptarse a ciegas.
            return None

        expected = hmac.new(
            SUPABASE_JWT_SECRET.encode("utf-8"),
            f"{header_b64}.{payload_b64}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
            return None

        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp") and time.time() > float(payload["exp"]):
            return None
        return payload.get("sub")
    except Exception:
        return None


def resolve_user(headers) -> str:
    """Devuelve el user_id que corresponde a este request."""
    auth_header = headers.get("Authorization", "") if headers else ""
    if auth_header.startswith("Bearer "):
        user_id = verify_supabase_jwt(auth_header[7:].strip())
        if user_id:
            return user_id

    if not is_supabase():
        # Solo en local: permite simular varias cuentas para probar aislamiento.
        # En producción is_supabase() es True y esta rama nunca corre.
        dev_user = headers.get("X-Dev-User", "") if headers else ""
        return dev_user.strip() or DEV_USER_ID

    return OWNER_USER_ID  # OWNER FALLBACK
