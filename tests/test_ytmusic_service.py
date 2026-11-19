"""Tests for the ytmusicapi/yt-dlp audio module."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock

import pytest

import ytmusic_service as audio


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------

class FakeYTMusic:
    """Minimal duck-typed stand-in for ytmusicapi.YTMusic."""

    def __init__(self, search_result=None, playlist_result=None):
        self._search_result = search_result or []
        self._playlist_result = playlist_result or {}
        self.search_calls: list[dict[str, Any]] = []
        self.playlist_calls: list[dict[str, Any]] = []

    def search(self, query, filter=None, limit=None):  # noqa: A002
        self.search_calls.append({"query": query, "filter": filter, "limit": limit})
        return self._search_result

    def get_playlist(self, playlist_id, limit=None):
        self.playlist_calls.append({"playlist_id": playlist_id, "limit": limit})
        return self._playlist_result


def _song_item(video_id="abc123", title="Song", artists=None,
               album=None, thumbs=None, duration="3:21"):
    item: dict[str, Any] = {"videoId": video_id, "title": title, "duration": duration}
    if artists is not None:
        item["artists"] = artists
    if album is not None:
        item["album"] = album
    if thumbs is not None:
        item["thumbnail"] = thumbs
    return item


# ---------------------------------------------------------------------------
# _pick_thumbnail
# ---------------------------------------------------------------------------

class TestPickThumbnail:
    def test_string_passthrough(self):
        assert audio._pick_thumbnail("http://x/y.jpg") == "http://x/y.jpg"

    def test_empty_string_returns_none(self):
        assert audio._pick_thumbnail("") is None

    def test_none_returns_none(self):
        assert audio._pick_thumbnail(None) is None

    def test_empty_list_returns_none(self):
        assert audio._pick_thumbnail([]) is None

    def test_picks_last_url(self):
        thumbs = [{"url": "small"}, {"url": "large"}]
        assert audio._pick_thumbnail(thumbs) == "large"

    def test_non_dict_last_returns_none(self):
        assert audio._pick_thumbnail(["x", "y"]) is None

    def test_dict_last_without_url(self):
        assert audio._pick_thumbnail([{"url": "a"}, {}]) is None


# ---------------------------------------------------------------------------
# _track_from_item
# ---------------------------------------------------------------------------

class TestTrackFromItem:
    def test_missing_video_id_returns_none(self):
        assert audio._track_from_item({"title": "x"}) is None

    def test_full_item(self):
        item = _song_item(
            video_id="vid1",
            title="T",
            artists=[{"name": "A"}, {"name": "B"}],
            album={"name": "Alb"},
            thumbs=[{"url": "img"}],
            duration="4:00",
        )
        track = audio._track_from_item(item)
        assert track == {
            "videoId": "vid1",
            "title": "T",
            "artist": "A, B",
            "album": "Alb",
            "duration": "4:00",
            "thumbnail": "img",
        }

    def test_missing_artists_defaults_to_unknown(self):
        track = audio._track_from_item(_song_item(artists=[]))
        assert track["artist"] == "Unknown"

    def test_artist_entries_without_name_skipped(self):
        item = _song_item(artists=[{}, {"name": "A"}, {"name": ""}])
        assert audio._track_from_item(item)["artist"] == "A"

    def test_missing_album_is_none(self):
        track = audio._track_from_item(_song_item())
        assert track["album"] is None

    def test_album_non_dict_is_none(self):
        item = _song_item()
        item["album"] = "not a dict"
        assert audio._track_from_item(item)["album"] is None

    def test_thumbnail_from_thumbnails_key(self):
        item = _song_item()
        item.pop("thumbnail", None)
        item["thumbnails"] = [{"url": "t"}]
        assert audio._track_from_item(item)["thumbnail"] == "t"

    def test_missing_title_defaults(self):
        track = audio._track_from_item({"videoId": "v"})
        assert track["title"] == "Unknown"


# ---------------------------------------------------------------------------
# search_songs
# ---------------------------------------------------------------------------

class TestSearchSongs:
    def test_empty_query_returns_empty(self):
        yt = FakeYTMusic(search_result=[_song_item()])
        assert audio.search_songs(yt, "") == []
        assert audio.search_songs(yt, "   ") == []
        assert audio.search_songs(yt, None) == []  # type: ignore[arg-type]
        assert yt.search_calls == []

    def test_basic_search_passes_args(self):
        yt = FakeYTMusic(search_result=[_song_item(video_id="v1")])
        out = audio.search_songs(yt, "hello", limit=5)
        assert [t["videoId"] for t in out] == ["v1"]
        assert yt.search_calls == [{"query": "hello", "filter": "songs", "limit": 5}]

    def test_limit_clamped_min(self):
        yt = FakeYTMusic()
        audio.search_songs(yt, "q", limit=0)
        assert yt.search_calls[0]["limit"] == 1

    def test_limit_clamped_max(self):
        yt = FakeYTMusic()
        audio.search_songs(yt, "q", limit=9999)
        assert yt.search_calls[0]["limit"] == 40

    def test_non_dict_items_skipped(self):
        yt = FakeYTMusic(search_result=[None, "junk", _song_item(video_id="ok")])
        out = audio.search_songs(yt, "q")
        assert [t["videoId"] for t in out] == ["ok"]

    def test_items_without_video_id_skipped(self):
        yt = FakeYTMusic(search_result=[{"title": "no id"}, _song_item(video_id="ok")])
        out = audio.search_songs(yt, "q")
        assert [t["videoId"] for t in out] == ["ok"]


# ---------------------------------------------------------------------------
# normalize_playlist_id
# ---------------------------------------------------------------------------

class TestNormalizePlaylistId:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("PL123", "PL123"),
            ("pl123", "pl123"),      # IDs are case-sensitive
            ("  PL123  ", "PL123"),  # whitespace trimmed
        ],
    )
    def test_plain_id_passthrough(self, raw, expected):
        assert audio.normalize_playlist_id(raw) == expected

    def test_strips_vl_prefix(self):
        assert audio.normalize_playlist_id("VLPL123") == "PL123"

    def test_full_url(self):
        url = "https://music.youtube.com/playlist?list=PL123&si=xyz"
        assert audio.normalize_playlist_id(url) == "PL123"

    def test_url_with_vl_prefix(self):
        url = "https://music.youtube.com/playlist?list=VLPL123"
        assert audio.normalize_playlist_id(url) == "PL123"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            audio.normalize_playlist_id("")
        with pytest.raises(ValueError):
            audio.normalize_playlist_id(None)  # type: ignore[arg-type]

    def test_url_without_list_raises(self):
        with pytest.raises(ValueError, match="list="):
            audio.normalize_playlist_id("https://music.youtube.com/playlist?si=x")

    def test_url_with_empty_list_raises(self):
        with pytest.raises(ValueError):
            audio.normalize_playlist_id("https://music.youtube.com/playlist?list=")

    def test_only_vl_prefix_raises(self):
        with pytest.raises(ValueError):
            audio.normalize_playlist_id("VL")


# ---------------------------------------------------------------------------
# playlist_tracks
# ---------------------------------------------------------------------------

class TestPlaylistTracks:
    def test_returns_title_and_tracks(self):
        yt = FakeYTMusic(playlist_result={
            "title": "My List",
            "tracks": [_song_item(video_id="v1"), _song_item(video_id="v2")],
        })
        title, tracks = audio.playlist_tracks(yt, "PL1")
        assert title == "My List"
        assert [t["videoId"] for t in tracks] == ["v1", "v2"]
        assert yt.playlist_calls == [{"playlist_id": "PL1", "limit": 100}]

    def test_normalizes_input_id(self):
        yt = FakeYTMusic(playlist_result={"title": "T", "tracks": []})
        audio.playlist_tracks(yt, "https://music.youtube.com/playlist?list=PL9")
        assert yt.playlist_calls[0]["playlist_id"] == "PL9"

    def test_default_title_when_missing(self):
        yt = FakeYTMusic(playlist_result={"tracks": []})
        title, _ = audio.playlist_tracks(yt, "PL1")
        assert title == "Playlist"

    def test_limit_clamped(self):
        yt = FakeYTMusic(playlist_result={})
        audio.playlist_tracks(yt, "PL1", limit=9999)
        assert yt.playlist_calls[0]["limit"] == 500

        yt2 = FakeYTMusic(playlist_result={})
        audio.playlist_tracks(yt2, "PL1", limit=0)
        assert yt2.playlist_calls[0]["limit"] == 1

    def test_limit_none_passes_through(self):
        yt = FakeYTMusic(playlist_result={})
        audio.playlist_tracks(yt, "PL1", limit=None)
        assert yt.playlist_calls[0]["limit"] is None

    def test_invalid_items_skipped(self):
        yt = FakeYTMusic(playlist_result={
            "title": "T",
            "tracks": [None, "x", _song_item(video_id="ok")],
        })
        _, tracks = audio.playlist_tracks(yt, "PL1")
        assert [t["videoId"] for t in tracks] == ["ok"]

    def test_missing_tracks_key(self):
        yt = FakeYTMusic(playlist_result={"title": "T"})
        _, tracks = audio.playlist_tracks(yt, "PL1")
        assert tracks == []


# ---------------------------------------------------------------------------
# sniff_audio_mime
# ---------------------------------------------------------------------------

class TestSniffAudioMime:
    @pytest.mark.parametrize("head,expected", [
        (b"", "audio/webm"),
        (b"\x1a\x45\xdf\xa3rest", "audio/webm"),
        (b"\x00\x00\x00\x20ftypM4A ", "audio/mp4"),
        (b"OggS\x00", "audio/ogg"),
        (b"ID3\x04", "audio/mpeg"),
        (b"\xff\xfb\x90\x00", "audio/mpeg"),  # MPEG frame sync
        (b"\xff\xe0\x00\x00", "audio/mpeg"),
        (b"\xff\xff\xff\xff", "audio/mpeg"),
        (b"\x00\x00\x00\x00", "audio/webm"),  # unknown -> default
        (b"RIFF....", "audio/webm"),          # wav -> default (not sniffed)
    ])
    def test_sniff(self, head, expected):
        assert audio.sniff_audio_mime(head) == expected

    def test_single_byte_ff_does_not_crash(self):
        # len(head) == 1 -> MPEG check guarded by len > 1
        assert audio.sniff_audio_mime(b"\xff") == "audio/webm"

    def test_ftyp_needs_8_bytes(self):
        # 4 bytes only -> not enough for the ftyp check
        assert audio.sniff_audio_mime(b"\x00\x00\x00\x20") == "audio/webm"


# ---------------------------------------------------------------------------
# stream_audio_process / _build_ytdlp_command
# ---------------------------------------------------------------------------

class TestBuildYtdlpCommand:
    def test_basic_command(self):
        cmd = audio._build_ytdlp_command("abc123")
        assert cmd[0] == "yt-dlp"
        assert "-f" in cmd
        assert "--no-playlist" in cmd
        assert cmd[-1] == "https://music.youtube.com/watch?v=abc123"
        assert "-o" in cmd
        assert cmd[cmd.index("-o") + 1] == "-"

    def test_video_id_with_dashes_and_underscores(self):
        cmd = audio._build_ytdlp_command("a-b_c9")
        assert cmd[-1].endswith("v=a-b_c9")

    @pytest.mark.parametrize("bad", ["", "   ", "abc!", "a b", "../etc", "id?x=1"])
    def test_invalid_video_id_raises(self, bad):
        with pytest.raises(ValueError):
            audio._build_ytdlp_command(bad)

    def test_whitespace_is_stripped(self):
        cmd = audio._build_ytdlp_command("  abc  ")
        assert cmd[-1].endswith("v=abc")

    def test_auth_headers_appended(self):
        cmd = audio._build_ytdlp_command(
            "v", {"Authorization": "Bearer x", "X-Goog": "1"}
        )
        header_args = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-header"]
        assert "Authorization:Bearer x" in header_args
        assert "X-Goog:1" in header_args

    def test_auth_header_values_trimmed(self):
        cmd = audio._build_ytdlp_command("v", {"A": "  b  "})
        assert "A:b" in cmd

    def test_none_and_empty_headers_skipped(self):
        cmd = audio._build_ytdlp_command("v", {"A": None, "B": "", "C": "  "})
        assert "--add-header" not in cmd

    def test_empty_headers_dict(self):
        cmd = audio._build_ytdlp_command("v", {})
        assert "--add-header" not in cmd


class TestStreamAudioProcess:
    def test_uses_injected_popen(self):
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return "POPEN_SENTINEL"

        result = audio.stream_audio_process("v1", popen=fake_popen)
        assert result == "POPEN_SENTINEL"
        assert len(calls) == 1
        cmd, kwargs = calls[0]
        assert cmd[0] == "yt-dlp"
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE

    def test_headers_forwarded(self):
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return MagicMock()

        audio.stream_audio_process(
            "v1", {"Authorization": "Bearer z"}, popen=fake_popen
        )
        assert "Authorization:Bearer z" in captured["cmd"]

    def test_invalid_id_raises_before_popen(self):
        called = []
        with pytest.raises(ValueError):
            audio.stream_audio_process(
                "bad id", popen=lambda *a, **k: called.append(1)
            )
        assert called == []