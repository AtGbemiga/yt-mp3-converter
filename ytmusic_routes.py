"""Flask routes for YT Music search and audio streaming."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request, stream_with_context

from ytmusic_service import search_songs, stream_audio_process

ytmusic_bp = Blueprint("ytmusic", __name__, url_prefix="/ytmusic")


@ytmusic_bp.get("/search")
def ytmusic_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(ok=False, message="Query parameter 'q' is required."), 400

    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        limit = 20

    try:
        results = search_songs(q, limit=limit)
    except Exception as exc:  # noqa: BLE001 — surface API errors to client
        return jsonify(ok=False, message=str(exc)), 500

    return jsonify(ok=True, results=results)


@ytmusic_bp.get("/play/<video_id>")
def ytmusic_play(video_id: str):
    try:
        process = stream_audio_process(video_id)
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
