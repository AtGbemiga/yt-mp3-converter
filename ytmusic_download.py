"""Download YouTube audio to SeaweedFS (Filer HTTP API or S3)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import re

# ---- SeaweedFS config (env-overridable) ----
SEAWEED_MODE = os.environ.get("SEAWEED_MODE", "filer")  # "filer" or "s3"
SEAWEED_FILER_URL = os.environ.get("SEAWEED_FILER_URL", "http://localhost:8888")
SEAWEED_S3_ENDPOINT = os.environ.get("SEAWEED_S3_ENDPOINT", "http://localhost:8333")
SEAWEED_S3_BUCKET = os.environ.get("SEAWEED_S3_BUCKET", "orin")
SEAWEED_S3_KEY = os.environ.get("SEAWEED_S3_KEY", "")
SEAWEED_S3_SECRET = os.environ.get("SEAWEED_S3_SECRET", "")
SEAWEED_PREFIX = os.environ.get("SEAWEED_PREFIX", "orin/audio").strip("/")
SEAWEED_PUBLIC_BASE = os.environ.get(
    "SEAWEED_PUBLIC_BASE", SEAWEED_FILER_URL.rstrip("/")
)

JOBS: dict[str, dict[str, Any]] = {}  # shared with routes



_VIDEO_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]\.mp3$")


def list_downloaded_tracks() -> list[dict[str, str]]:
    """Return [{videoId, filename, url}] for every mp3 under SEAWEED_PREFIX."""
    if SEAWEED_MODE == "s3":
        return _list_downloaded_s3()
    return _list_downloaded_filer()


def _list_downloaded_filer() -> list[dict[str, str]]:
    """Walk the Filer tree under SEAWEED_PREFIX, one page at a time."""
    results: list[dict[str, str]] = []
    last_file_name: str | None = None
    base = f"{SEAWEED_FILER_URL.rstrip('/')}/{SEAWEED_PREFIX}/"

    while True:
        params: dict[str, Any] = {"pretty": "y", "limit": 1000}
        if last_file_name:
            params["lastFileName"] = last_file_name

        resp = requests.get(base, params=params, timeout=30, headers={"Accept": "application/json"}, )
        if resp.status_code == 404:
            return results  # directory doesn't exist yet — nothing downloaded
        resp.raise_for_status()

        data = resp.json()
        entries = data.get("Entries") or []
        if not entries:
            break

        for e in entries:
            name = (e.get("FullPath") or "").rsplit("/", 1)[-1]
            m = _VIDEO_ID_RE.search(name)
            if not m:
                continue
            video_id = m.group(1)
            safe_name = quote(name)
            results.append({
                "videoId": video_id,
                "filename": name,
                "url": f"{SEAWEED_PUBLIC_BASE.rstrip('/')}/{SEAWEED_PREFIX}/{safe_name}",
            })

        last_file_name = entries[-1].get("FullPath")
        if not last_file_name:
            break
        # If this page was short, we've reached the end.
        if len(entries) < params["limit"]:
            break

    return results


def _list_downloaded_s3() -> list[dict[str, str]]:
    import boto3  # type: ignore

    s3 = boto3.client(
        "s3",
        endpoint_url=SEAWEED_S3_ENDPOINT,
        aws_access_key_id=SEAWEED_S3_KEY or None,
        aws_secret_access_key=SEAWEED_S3_SECRET or None,
        region_name="us-east-1",
    )
    results: list[dict[str, str]] = []
    paginator = s3.get_paginator("list_objects_v2")
    prefix = f"{SEAWEED_PREFIX}/"
    for page in paginator.paginate(Bucket=SEAWEED_S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            name = key.rsplit("/", 1)[-1]
            m = _VIDEO_ID_RE.search(name)
            if not m:
                continue
            results.append({
                "videoId": m.group(1),
                "filename": name,
                "url": f"{SEAWEED_S3_ENDPOINT.rstrip('/')}/{SEAWEED_S3_BUCKET}/{quote(key)}",
            })
    return results


def _safe_filename(title: str, video_id: str) -> str:
    keep = "".join(c for c in title if c.isalnum() or c in " .-_()[]").strip()
    keep = keep[:120] or "track"
    return f"{keep} [{video_id}].mp3"


# ---------------------------------------------------------------------------
# Filer helpers
# ---------------------------------------------------------------------------
def _ensure_filer_dir(dir_path: str) -> None:
    """Create the directory in the Filer if it doesn't already exist.

    SeaweedFS's Filer `POST ?op=mkdir` is idempotent-ish: it returns 200 even
    when the directory already exists on most builds, but 409 on others. We
    accept anything in the 2xx–4xx range that isn't a hard connection failure.
    """
    url = f"{SEAWEED_FILER_URL.rstrip('/')}/{dir_path.lstrip('/')}/"
    resp = requests.post(
        url,
        params={"op": "mkdir"},
        timeout=30,
    )
    # 200 = created, 201 = created, 409 = already exists, 500 = parent missing
    # (which shouldn't happen because we call this for each level in order,
    # but Filer typically auto-creates parents anyway).
    if resp.status_code >= 500:
        raise RuntimeError(
            f"Filer mkdir failed for {dir_path}: HTTP {resp.status_code} "
            f"{resp.text[:200]}"
        )


def _ensure_filer_path(full_key: str) -> None:
    """Create each level of `full_key`'s directory tree."""
    parts = full_key.split("/")[:-1]  # drop the filename
    for i in range(1, len(parts) + 1):
        _ensure_filer_dir("/".join(parts[:i]))


def _upload_to_seaweed(local_path: Path, remote_name: str) -> str:
    """Upload a file to SeaweedFS and return its public URL (URL-encoded)."""
    key = f"{SEAWEED_PREFIX}/{remote_name}"

    if SEAWEED_MODE == "s3":
        import boto3  # type: ignore

        s3 = boto3.client(
            "s3",
            endpoint_url=SEAWEED_S3_ENDPOINT,
            aws_access_key_id=SEAWEED_S3_KEY or None,
            aws_secret_access_key=SEAWEED_S3_SECRET or None,
            region_name="us-east-1",
        )
        s3.upload_file(
            str(local_path),
            SEAWEED_S3_BUCKET,
            key,
            ExtraArgs={"ContentType": "audio/mpeg"},
        )
        safe_key = "/".join(quote(part) for part in key.split("/"))
        return f"{SEAWEED_S3_ENDPOINT.rstrip('/')}/{SEAWEED_S3_BUCKET}/{safe_key}"

    # Default: SeaweedFS Filer HTTP API
    _ensure_filer_path(key)

    with open(local_path, "rb") as fh:
        resp = requests.put(
            f"{SEAWEED_FILER_URL.rstrip('/')}/{key}",
            data=fh,
            headers={"Content-Type": "audio/mpeg"},
            timeout=300,
        )
    resp.raise_for_status()

    safe_key = "/".join(quote(part) for part in key.split("/"))
    return f"{SEAWEED_PUBLIC_BASE.rstrip('/')}/{safe_key}"


# ---------------------------------------------------------------------------
# yt-dlp wrapper
# ---------------------------------------------------------------------------
def download_track_to_seaweed(
    video_id: str,
    title: str,
    auth_headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """yt-dlp → mp3 → SeaweedFS. Returns {url, filename}."""
    video_id = (video_id or "").strip()
    if not video_id or not video_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid video id")

    watch_url = f"https://music.youtube.com/watch?v={video_id}"
    filename = _safe_filename(title or video_id, video_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = str(Path(tmpdir) / "%(id)s.%(ext)s")
        cmd: list[str] = [
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--no-playlist",
            "-o", outtmpl,
            "--print", "after_move:filepath",
        ]
        if auth_headers:
            for k, v in auth_headers.items():
                if v:
                    cmd.extend(["--add-header", f"{k}:{v}"])
        cmd.append(watch_url)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"yt-dlp failed for {video_id}: {result.stderr[-800:]}"
            )
        produced = result.stdout.strip().splitlines()[-1]
        local = Path(produced)
        if not local.exists():
            raise RuntimeError(f"yt-dlp produced no file for {video_id}")

        public_url = _upload_to_seaweed(local, filename)

    return {"videoId": video_id, "url": public_url, "filename": filename}


def run_playlist_download(
    job_id: str,
    tracks: list[dict[str, Any]],
    auth_headers: dict[str, Any] | None = None,
    max_workers: int = 3,
) -> None:
    """Background worker: download every track in a playlist, update JOBS."""
    job = JOBS[job_id]
    job["status"] = "running"
    job["total"] = len(tracks)
    job["completed"] = 0
    job["failed"] = 0
    job["items"] = []

    def _one(t: dict[str, Any]) -> dict[str, Any]:
        try:
            r = download_track_to_seaweed(
                t["videoId"], t.get("title", ""), auth_headers
            )
            return {"ok": True, **r, "title": t.get("title", "")}
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "videoId": t.get("videoId"),
                "title": t.get("title", ""),
                "error": str(e),
            }

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for result in pool.map(_one, tracks):
            job["items"].append(result)
            if result["ok"]:
                job["completed"] += 1
            else:
                job["failed"] += 1

    job["status"] = "done"