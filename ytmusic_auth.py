"""Resolve YTMusic client from browser header auth (no OAuth client id)."""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any
from urllib.parse import urlparse

from flask import request, session
from ytmusicapi import YTMusic

AUTH_HEADER = "X-YTMusic-Auth"
SESSION_KEY = "ytmusic_browser_auth"

_default_yt: YTMusic | None = None

def _get_default_yt() -> YTMusic:
    global _default_yt
    if _default_yt is None:
        _default_yt = YTMusic()
    return _default_yt

def _decode_auth_header(raw: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(raw, validate=True)
        payload = json.loads(decoded)
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid {AUTH_HEADER} header") from exc
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"{AUTH_HEADER} must be a JSON object of browser headers")
    return payload


def auth_dict_from_request() -> dict[str, Any] | None:
    raw = (request.headers.get(AUTH_HEADER) or "").strip()
    if raw:
        return _decode_auth_header(raw)
    stored = session.get(SESSION_KEY)
    if isinstance(stored, dict) and stored:
        return stored
    return None


def store_auth_in_session(auth: dict[str, Any]) -> None:
    session[SESSION_KEY] = auth
    session.modified = True


def clear_auth_session() -> None:
    session.pop(SESSION_KEY, None)


def ytmusic_from_request() -> YTMusic:
    auth = auth_dict_from_request()
    if auth is None:
        return _get_default_yt()
    return YTMusic(auth)


_CORS_ALLOW_HEADERS = (
    "Content-Type, X-YTMusic-Auth, Authorization, ngrok-skip-browser-warning"
)
_CORS_ALLOW_METHODS = "GET, POST, DELETE, OPTIONS"


def _origin_allowed(origin: str) -> bool:
    extra = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    for item in extra.split(","):
        item = item.strip()
        if item and origin == item:
            return True

    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1"):
        return True
    if host.endswith(".vercel.app"):
        return True
    if host.endswith(".netlify.app"):
        return True
    return False


def apply_cors(response):
    origin = request.headers.get("Origin")
    if origin and _origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = _CORS_ALLOW_HEADERS
        response.headers["Access-Control-Allow-Methods"] = _CORS_ALLOW_METHODS
        response.headers["Vary"] = "Origin"
        if request.method == "OPTIONS":
            response.headers["Access-Control-Max-Age"] = "86400"
    return response
