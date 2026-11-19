# yt-mp3-app

A Flask app that turns YouTube / YouTube Music URLs into MP3s. Search songs and
playlists, download tracks into SeaweedFS, and stream or download the results —
all from a browser, with optional LAN access via mDNS (`.local`).

- **Search** songs and playlists via `ytmusicapi`
- **Download** audio with `yt-dlp` and store it in SeaweedFS (filer or S3 mode)
- **Stream** audio directly to the browser (proxied, with proper Content-Type)
- **Download** previously fetched tracks as MP3
- **LAN access** via mDNS (`http://flask-app.local:5000/`)
- **Tests** with pytest (no network required)

---

## Requirements

- Python 3.12+
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) on `PATH`
- [`ffmpeg`](https://ffmpeg.org/) on `PATH` (used by `yt-dlp` for MP3 conversion)
- [SeaweedFS](https://github.com/seaweedfs/seaweedfs) (`weed`) for storage

### Python dependencies

Install into a virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt