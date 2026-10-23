"""Resolve YTMusic client from browser header auth (no OAuth client id)."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from flask import request, session
from ytmusicapi import YTMusic

AUTH_HEADER = "X-YTMusic-Auth"
SESSION_KEY = "ytmusic_browser_auth"

_default_yt = YTMusic()


def auth_dict_from_request() -> dict[str, Any] | None:
    raw = (request.headers.get(AUTH_HEADER) or "").strip()
    if raw:
        try:
            decoded = base64.b64decode(raw, validate=True)
            payload = json.loads(decoded)
        except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Invalid {AUTH_HEADER} header") from exc
        if not isinstance(payload, dict) or not payload:
            raise ValueError(
                f"{AUTH_HEADER} must be a JSON object of browser headers"
            )
        return payload

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
        return _default_yt
    return YTMusic(auth)


def apply_cors(response):
    origin = request.headers.get("Origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-YTMusic-Auth, Authorization"
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response
