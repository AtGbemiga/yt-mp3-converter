"""Flask routes for YT Music search, playlists, and audio streaming."""

from __future__ import annotations

import threading
import uuid

from flask import Blueprint, Response, jsonify, request, stream_with_context

from ytmusic_auth import (
    apply_cors,
    auth_dict_from_request,
    clear_auth_session,
    store_auth_in_session,
    ytmusic_from_request,
)
from ytmusic_service import (
    playlist_tracks,
    search_songs,
    sniff_audio_mime,
    stream_audio_process,
)

from ytmusic_download import (
    JOBS,
    download_track_to_seaweed,
    run_playlist_download,
)

from ytmusic_download import JOBS, download_track_to_seaweed, run_playlist_download, list_downloaded_tracks

from ytmusic_download import (
    JOBS,
    download_track_to_seaweed,
    run_playlist_download,
    list_downloaded_tracks,
    list_downloads_with_meta,
)


from flask import request, Response, stream_with_context
import requests # make sure this is imported at the top of the file

ytmusic_bp = Blueprint("ytmusic", __name__, url_prefix="/ytmusic")


@ytmusic_bp.after_request
def _cors_after(response):
    return apply_cors(response)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
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



@ytmusic_bp.get("/downloads")
def ytmusic_downloads_library():
    try:
        items = list_downloads_with_meta()
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, message=str(e)), 500
    return jsonify(ok=True, results=items)

@ytmusic_bp.get("/downloads/play")
def ytmusic_download_play_proxy():
    url = request.args.get("url")
    if not url:
        return jsonify(ok=False, message="url parameter is required"), 400

    # Security check: ensure it's actually a SeaweedFS URL
    if not url.startswith("http://192.168.8.95:8888") and not url.startswith("http://localhost:8888"):
        return jsonify(ok=False, message="Invalid URL domain"), 403

    # Proxy the request to SeaweedFS
    req = requests.get(url, stream=True)
    if req.status_code != 200:
        return jsonify(ok=False, message="File not found on SeaweedFS"), 404

    return Response(
        stream_with_context(req.iter_content(chunk_size=64 * 1024)),
        content_type=req.headers.get('Content-Type', 'audio/mpeg'),
        headers={
            "Accept-Ranges": "bytes", # Important for seeking
            "Content-Length": req.headers.get('Content-Length', ''),
        }
    )

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
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
    except Exception as exc:  # noqa: BLE001
        return jsonify(ok=False, message=str(exc)), 500

    return jsonify(ok=True, results=results)


# ---------------------------------------------------------------------------
# Playlist
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Audio streaming (proxy yt-dlp through the Flask app)
# ---------------------------------------------------------------------------
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
        return jsonify(
            ok=False, message="yt-dlp is not installed on the server."
        ), 500

    assert process.stdout is not None
    first_chunk = process.stdout.read(64 * 1024)
    if not first_chunk:
        err = b""
        if process.stderr is not None:
            err = process.stderr.read() or b""
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        message = err.decode("utf-8", errors="replace")[-800:].strip()
        return jsonify(
            ok=False,
            message=message or "Empty audio stream from yt-dlp",
        ), 502

    mime = sniff_audio_mime(first_chunk)

    def generate():
        try:
            yield first_chunk
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
        mimetype=mime,
        headers={
            "Cache-Control": "no-store",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


# ---------------------------------------------------------------------------
# Downloads → SeaweedFS
# ---------------------------------------------------------------------------
@ytmusic_bp.post("/download/track")
def download_track():
    data = request.get_json(silent=True) or {}
    video_id = (data.get("videoId") or "").strip()
    title = (data.get("title") or "").strip()
    if not video_id:
        return jsonify(ok=False, message="videoId is required"), 400

    try:
        auth_headers = auth_dict_from_request()
        result = download_track_to_seaweed(
            video_id,
            title,
            auth_headers,
            artist=(data.get("artist") or None),
            duration=(data.get("duration") or None),
            thumbnail=(data.get("thumbnail") or None),
        )
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, message=str(e)), 500

    return jsonify(ok=True, **result)

@ytmusic_bp.post("/download/playlist")
def download_playlist():
    data = request.get_json(silent=True) or {}
    tracks = data.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        return jsonify(ok=False, message="tracks[] is required"), 400

    cleaned = [
    {
        "videoId": t["videoId"],
        "title": t.get("title", ""),
        "artist": t.get("artist"),
        "duration": t.get("duration"),
        "thumbnail": t.get("thumbnail"),
    }
    for t in tracks
    if isinstance(t, dict) and t.get("videoId")
]
    if not cleaned:
        return jsonify(ok=False, message="No valid tracks"), 400

    try:
        auth_headers = auth_dict_from_request()
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "queued",
        "total": len(cleaned),
        "completed": 0,
        "failed": 0,
        "items": [],
    }
    threading.Thread(
        target=run_playlist_download,
        args=(job_id, cleaned, auth_headers),
        daemon=True,
    ).start()

    return jsonify(ok=True, jobId=job_id, total=len(cleaned))


@ytmusic_bp.get("/download/playlist/<job_id>")
def download_playlist_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(ok=False, message="Job not found"), 404
    return jsonify(ok=True, jobId=job_id, **job)


@ytmusic_bp.get("/downloaded")
def ytmusic_downloaded():
    try:
        items = list_downloaded_tracks()
    except Exception as e:  # noqa: BLE001
        return jsonify(ok=False, message=str(e)), 500
    return jsonify(ok=True, results=items)