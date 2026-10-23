"""Flask routes for YT Music search, playlists, and audio streaming."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request, stream_with_context

from ytmusic_auth import (
    apply_cors,
    auth_dict_from_request,
    clear_auth_session,
    store_auth_in_session,
    ytmusic_from_request,
)
from ytmusic_service import playlist_tracks, search_songs, stream_audio_process

ytmusic_bp = Blueprint("ytmusic", __name__, url_prefix="/ytmusic")


@ytmusic_bp.after_request
def _cors_after(response):
    return apply_cors(response)


@ytmusic_bp.route("/auth", methods=["POST", "DELETE", "OPTIONS"])
def ytmusic_auth():
    if request.method == "OPTIONS":
        return "", 204

    if request.method == "DELETE":
        clear_auth_session()
        return jsonify(ok=True)

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return jsonify(
            ok=False,
            message="JSON body must be a non-empty object of browser headers",
        ), 400

    normalized = {
        str(k): "" if v is None else str(v)
        for k, v in data.items()
        if str(k).strip()
    }
    if not normalized:
        return jsonify(ok=False, message="No headers provided"), 400

    store_auth_in_session(normalized)
    return jsonify(ok=True)


@ytmusic_bp.route("/search", methods=["GET", "OPTIONS"])
def ytmusic_search():
    if request.method == "OPTIONS":
        return "", 204

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(ok=False, message="Query parameter 'q' is required."), 400

    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        limit = 20

    try:
        yt = ytmusic_from_request()
        results = search_songs(yt, q, limit=limit)
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    except Exception as exc:  # noqa: BLE001 — surface API errors to client
        return jsonify(ok=False, message=str(exc)), 500

    return jsonify(ok=True, results=results)


@ytmusic_bp.route("/playlist", methods=["GET", "OPTIONS"])
def ytmusic_playlist():
    if request.method == "OPTIONS":
        return "", 204

    playlist_id = (
        request.args.get("id") or request.args.get("playlistId") or ""
    ).strip()
    if not playlist_id:
        return jsonify(ok=False, message="Query parameter 'id' is required."), 400

    try:
        limit_raw = request.args.get("limit")
        limit = int(limit_raw) if limit_raw is not None else 200
    except ValueError:
        limit = 200

    try:
        yt = ytmusic_from_request()
        title, results = playlist_tracks(yt, playlist_id, limit=limit)
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, message=str(exc)), 500

    return jsonify(ok=True, title=title, results=results)


@ytmusic_bp.route("/play/<video_id>", methods=["GET", "OPTIONS"])
def ytmusic_play(video_id: str):
    if request.method == "OPTIONS":
        return "", 204

    try:
        auth = auth_dict_from_request()
        process = stream_audio_process(video_id, auth)
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    except FileNotFoundError:
        return jsonify(ok=False, message="yt-dlp is not installed on the server."), 500

    def generate():
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    return Response(
        stream_with_context(generate()),
        mimetype="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
