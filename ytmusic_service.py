"""YouTube Music search/playlists via ytmusicapi; audio delivery via yt-dlp."""

from __future__ import annotations

import subprocess
from typing import Any
from urllib.parse import parse_qs, urlparse

from ytmusicapi import YTMusic


def _pick_thumbnail(thumbs: Any) -> str | None:
    if isinstance(thumbs, str):
        return thumbs or None
    if not isinstance(thumbs, list) or not thumbs:
        return None
    best = thumbs[-1]
    if isinstance(best, dict):
        return best.get("url")
    return None


def _track_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    video_id = item.get("videoId")
    if not video_id:
        return None

    artists = item.get("artists") or []
    artist_names = [
        a.get("name", "")
        for a in artists
        if isinstance(a, dict) and a.get("name")
    ]
    artist = ", ".join(artist_names) if artist_names else "Unknown"

    album = item.get("album") or {}
    album_name = album.get("name") if isinstance(album, dict) else None

    thumbs = item.get("thumbnail") or item.get("thumbnails") or []
    thumbnail = _pick_thumbnail(thumbs)

    return {
        "videoId": video_id,
        "title": item.get("title") or "Unknown",
        "artist": artist,
        "album": album_name,
        "duration": item.get("duration"),
        "thumbnail": thumbnail,
    }


def search_songs(yt: YTMusic, query: str, limit: int = 20) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []

    limit = max(1, min(int(limit), 40))
    raw = yt.search(query, filter="songs", limit=limit)
    tracks: list[dict[str, Any]] = []

    for item in raw:
        if not isinstance(item, dict):
            continue
        track = _track_from_item(item)
        if track is not None:
            tracks.append(track)

    return tracks


def normalize_playlist_id(raw: str) -> str:
    playlist_id = (raw or "").strip()
    if not playlist_id:
        raise ValueError("Playlist id is required")

    if playlist_id.startswith(("http://", "https://")):
        parsed = urlparse(playlist_id)
        list_param = parse_qs(parsed.query).get("list", [None])[0]
        if not list_param:
            raise ValueError("URL has no list= playlist parameter")
        playlist_id = list_param

    if playlist_id.startswith("VL"):
        playlist_id = playlist_id[2:]

    if not playlist_id:
        raise ValueError("Invalid playlist id")
    return playlist_id


def playlist_tracks(
    yt: YTMusic, playlist_id: str, limit: int | None = 100
) -> tuple[str, list[dict[str, Any]]]:
    pid = normalize_playlist_id(playlist_id)
    if limit is not None:
        limit = max(1, min(int(limit), 500))

    data = yt.get_playlist(pid, limit=limit)
    title = data.get("title") or "Playlist"
    raw_tracks = data.get("tracks") or []
    tracks: list[dict[str, Any]] = []
    for item in raw_tracks:
        if not isinstance(item, dict):
            continue
        track = _track_from_item(item)
        if track is not None:
            tracks.append(track)
    return title, tracks


def sniff_audio_mime(head: bytes) -> str:
    """Guess a browser-safe audio Content-Type from the first bytes."""
    if not head:
        return "audio/webm"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "audio/mp4"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"ID3") or (
        len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    ):
        return "audio/mpeg"
    return "audio/webm"


def stream_audio_process(
    video_id: str, auth_headers: dict[str, Any] | None = None
) -> subprocess.Popen[bytes]:
    """Spawn yt-dlp writing best audio to stdout for proxy streaming."""
    video_id = (video_id or "").strip()
    if not video_id or not video_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid video id")

    watch_url = f"https://music.youtube.com/watch?v={video_id}"
    # Prefer container types HTML audio can decode; avoid generic octet-stream.
    cmd: list[str] = [
        "yt-dlp",
        "-f",
        "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "--no-playlist",
        "-o",
        "-",
    ]

    if auth_headers:
        for key, value in auth_headers.items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            cmd.extend(["--add-header", f"{key}:{text}"])

    cmd.append(watch_url)
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
