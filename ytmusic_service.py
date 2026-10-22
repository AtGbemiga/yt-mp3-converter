"""YouTube Music search via ytmusicapi; audio delivery via yt-dlp."""

from __future__ import annotations

import subprocess
from typing import Any

from ytmusicapi import YTMusic

_yt = YTMusic()


def search_songs(query: str, limit: int = 20) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []

    limit = max(1, min(int(limit), 40))
    raw = _yt.search(query, filter="songs", limit=limit)
    tracks: list[dict[str, Any]] = []

    for item in raw:
        if not isinstance(item, dict):
            continue
        video_id = item.get("videoId")
        if not video_id:
            continue

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

        tracks.append(
            {
                "videoId": video_id,
                "title": item.get("title") or "Unknown",
                "artist": artist,
                "album": album_name,
                "duration": item.get("duration"),
                "thumbnail": thumbnail,
            }
        )

    return tracks


def _pick_thumbnail(thumbs: Any) -> str | None:
    if isinstance(thumbs, str):
        return thumbs or None
    if not isinstance(thumbs, list) or not thumbs:
        return None
    best = thumbs[-1]
    if isinstance(best, dict):
        return best.get("url")
    return None


def stream_audio_process(video_id: str) -> subprocess.Popen[bytes]:
    """Spawn yt-dlp writing best audio to stdout for proxy streaming."""
    video_id = (video_id or "").strip()
    if not video_id or not video_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid video id")

    watch_url = f"https://music.youtube.com/watch?v={video_id}"
    return subprocess.Popen(
        [
            "yt-dlp",
            "-f",
            "bestaudio/best",
            "--no-playlist",
            "-o",
            "-",
            watch_url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
